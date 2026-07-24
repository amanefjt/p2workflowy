import json
import shutil
import fitz
import re
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional

from .config import STATE_DIR, SessionState, print_log
from .engine.p1_ingest.pdf_splitter import PDFSplitter
from .engine.p1_ingest.routing import decide_pdf_mode as _decide_book_pdf_mode
from .engine.p3_structure.state_integrator import StateIntegrator
from .llm_client import call_gemini, get_default_model, load_coreprompts, GeminiTier, apply_tier_settings

# gemini-3.5-flash（旧 DEFAULT_MODEL_RESUME）は公称入力上限（1,048,576 tok）とは別に、
# 単発リクエストで実測 ~186,000〜187,000 tok（本文字数にして概ね 735,000 字前後）を超えると
# 400 INVALID_ARGUMENT を返す（ドキュメント未記載の実挙動。troubleshooting_log I-20）。
# 書籍全文スキャンはこの規模を容易に超えるため、超過時は resume モデルを使わず
# 既定モデル（gemini-3.1-flash-lite）にフォールバックする。安全マージンとして
# 実測しきい値（734,997字=OK / 738,015字=FAIL）よりかなり低い値を設定。
# 2026-07-22: DEFAULT_MODEL_RESUME を gemini-3.6-flash に切替済みだが、この上限は
# gemini-3.5-flash 実測値のまま未検証（3.6-flash で同じ制約が出るかは要再測定）。
# 保守的な値のため当面はこのまま流用する。
RESUME_MODEL_SAFE_CHAR_LIMIT = 600_000

# ①〜④ルーティング規則の実体は core/engine/p1_ingest/routing.py に一元化
# （論文モードの main.py / server.py とも共有するため）。_decide_book_pdf_mode
# の名前はテスト（tests/unit/test_book_manager.py）との互換のために維持する。


class BookManager:
    """書籍全体のライフサイクル（全体解析 -> 分割 -> 処理 -> 統合）を管理する。"""

    def __init__(self, input_path: str, api_key: str, model: Optional[str] = None):
        self.input_path = Path(input_path)
        self.api_key = api_key
        self.model = model # 解決は後で行う (get_default_model のために tier 確定を待つ)
        self.book_title = self.input_path.stem
        
        # 物理データ主権: PDFの中身に応じた一意なハッシュを生成
        self.fingerprint = self._get_pdf_fingerprint(self.input_path)
        self.session_dir = STATE_DIR / "book_sessions" / f"{self.book_title}_{self.fingerprint}"
        
        # グローバルコンテキスト保持用
        self.global_resume = ""
        self.global_glossary = []

        self.session_dir.mkdir(parents=True, exist_ok=True)

    def _get_pdf_fingerprint(self, path: Path) -> str:
        """PDF のファイルハッシュ（最初の1MB）を取得して指紋とする。"""
        try:
            with open(path, "rb") as f:
                # 高速化のため冒頭1MBのみハッシュ化（内容の変化を捉えるには十分）
                chunk = f.read(1024 * 1024)
                return hashlib.md5(chunk).hexdigest()[:12]
        except Exception as e:
            print_log(f"  [BookManager] Fingerprint calculation failed: {e}")
            import uuid
            return f"fallback_{uuid.uuid4().hex[:8]}"

    def _generate_global_context(self, expertise: str = "文化人類学"):
        """PDF 全編をスキャンし、書籍全体のレジュメと用語集を事前生成する。"""
        print_log(f"\n--- Phase 0: Global Context Generation (Full Scan) ---")
        doc = fitz.open(self.input_path)
        full_text = ""
        try:
            for page in doc:
                full_text += page.get_text() + "\n"
        finally:
            doc.close()

        # トークン制限対策
        MAX_CHARS = 1_200_000
        if len(full_text) > MAX_CHARS:
            print_log(f"  [BookManager] テキストサンプリング実行 ({len(full_text)} chars)")
            full_text = full_text[:800_000] + "\n\n[...Skipped...]\n\n" + full_text[-400_000:]
        else:
            print_log(f"  [BookManager] フルテキスト抽出完了 ({len(full_text)} chars)")

        prompts = load_coreprompts()

        # 1. 全体レジュメ生成
        print_log("  [BookManager] 書籍全体のレジュメを生成中...")
        resume_prompt = prompts.get("BOOK_SUMMARY_PROMPT", "").replace("{expertise}", expertise) \
                                     .replace("{context_guide}", "書籍全体の核心的問い、論理構成を俯瞰して下さい。") \
                                     .replace("{text}", full_text)

        resume_model = self.model or get_default_model("resume")
        if not self.model and len(full_text) > RESUME_MODEL_SAFE_CHAR_LIMIT:
            print_log(f"  [BookManager] 全文が{RESUME_MODEL_SAFE_CHAR_LIMIT}字超のため resume モデルの実効入力上限を回避し既定モデルへフォールバック")
            resume_model = get_default_model("default")
        self.global_resume = call_gemini(resume_prompt, api_key=self.api_key, model=resume_model, thinking_level="High")

        # 2. 全体用語集生成
        print_log("  [BookManager] 書籍全体の共通用語集を生成中...")
        glossary_prompt = prompts.get("KEYWORD_EXTRACTION_PROMPT", "").replace("{expertise}", expertise) \
                                         .replace("{text}", full_text)
        
        glossary_json = call_gemini(glossary_prompt, api_key=self.api_key, model=self.model, response_mime_type="application/json")
        try:
            self.global_glossary = json.loads(glossary_json)
        except:
            self.global_glossary = []

        # 結果を確実に保存して次回スキップ可能にする
        context_file = self.session_dir / "global_context.json"
        save_data = {
            "resume": self.global_resume,
            "glossary": self.global_glossary,
            "book_title": self.book_title
        }
        context_file.write_text(json.dumps(save_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print_log(f"  [BookManager] Global Context を保存しました: {context_file.absolute()}")

    def run(self, resume_only: bool = False, structure_only: bool = False, max_chapters: Optional[int] = None, **pipeline_kwargs) -> List[str]:
        """全工程を一括実行する。"""
        print_log(f"\n=== Book Mode Orchestration: {self.book_title} ===")
        
        # 0. 診断とグローバルコンテキストの判定（スキップ機能）
        from .engine.p1_ingest.pdf_ingester import diagnose_pdf_quality
        
        # ティア状態の初期化（get_default_model を呼ぶ前に必要）
        tier = pipeline_kwargs.get("tier", "paid")
        apply_tier_settings(tier, api_key=self.api_key)
        expertise = pipeline_kwargs.get("expertise", "文化人類学")

        can_use_full_scan = diagnose_pdf_quality(str(self.input_path))

        global_context_path = self.session_dir / "global_context.json"

        if global_context_path.exists():
            print_log(f"  [BookManager] 既存のキャッシュを確認中: {global_context_path.absolute()}")
            try:
                data = json.loads(global_context_path.read_text(encoding="utf-8"))
                self.global_resume = data.get("resume", "")
                self.global_glossary = data.get("glossary", [])
                if self.global_resume:
                    print_log("  [BookManager] 既存の Global Context を発見。Phase 0 をスキップします。")
                else:
                    print_log("  [BookManager] キャッシュが不完全なため再解析を行います。")
                    self._generate_global_context(expertise=expertise)
            except Exception as e:
                print_log(f"  [BookManager] キャッシュ読込エラー: {e}")
                self._generate_global_context(expertise=expertise)
        elif can_use_full_scan:
            self._generate_global_context(expertise=expertise)
        else:
            print_log("  [BookManager] PDF破損につき事後生成ルートへ倒れます。")
            self.global_resume = ""

        # 用語集CSVの作成
        glossary_path = self.session_dir / "global_glossary.csv"
        if self.global_glossary:
            import csv
            with open(glossary_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["en", "ja"], extrasaction="ignore")
                writer.writeheader()
                writer.writerows(self.global_glossary)
        
        glossary_path_str = str(glossary_path) if glossary_path.exists() else None

        # 1. PDF 分割
        # 見開きスキャンPDFは分割してから章分割・処理に渡す
        from .engine.p1_ingest.spread_splitter import is_spread_pdf, split_spread_pdf
        from .engine.p1_ingest.docling_ingester import is_docling_viable

        pdf_for_splitting = str(self.input_path)
        is_spread = is_spread_pdf(pdf_for_splitting)
        if is_spread:
            print_log("  [BookManager] 見開きスキャンPDFを検出。単ページに分割します...")
            pdf_for_splitting = split_spread_pdf(pdf_for_splitting)

        # 書籍単位のルーティング決定（①〜④）: ユーザー明示指定 pop はここで一度だけ行う
        explicit_pdf_mode = pipeline_kwargs.pop("pdf_mode", None)
        is_docling_ok = is_docling_viable(str(self.input_path))
        book_pdf_mode, routing_reason = _decide_book_pdf_mode(explicit_pdf_mode, is_spread, is_docling_ok)
        print_log(
            f"  [BookManager] 入力ルーティング決定: pdf_mode={book_pdf_mode} "
            f"(理由: {routing_reason}, spread={is_spread}, docling_viable={is_docling_ok}, "
            f"explicit={explicit_pdf_mode})"
        )
        routing_path = self.session_dir / "routing_decision.json"
        routing_path.write_text(
            json.dumps({
                "pdf_mode": book_pdf_mode,
                "reason": routing_reason,
                "is_spread": is_spread,
                "is_docling_viable": is_docling_ok,
                "explicit_pdf_mode": explicit_pdf_mode,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        model_to_use = self.model or get_default_model("default")
        splitter = PDFSplitter(api_key=self.api_key, model=model_to_use)
        chapters = splitter.split(pdf_for_splitting, self.session_dir / "chapters")
        
        if not chapters:
            return []

        # 2. 各章の処理
        from .pipeline import run_pipeline
        chapter_sessions = []
        target_chapters = chapters[:max_chapters] if max_chapters else chapters

        # BookManager が制御する引数は pipeline_kwargs から抽出して重複エラーを防ぐ
        max_pages = pipeline_kwargs.get("max_pages")
        heavy_ocr = pipeline_kwargs.get("heavy_ocr", False)
        thinking_level = pipeline_kwargs.get("thinking_level", "High")
        tier = pipeline_kwargs.get("tier", "paid")
        resume_from = pipeline_kwargs.get("resume_from", None)

        explicit_keys = [
            "glossary_path", "thinking_level", "tier",
            "heavy_ocr", "max_pages", "api_key", "model", "resume_from"
        ]
        for key in explicit_keys:
            pipeline_kwargs.pop(key, None)

        failed_chapters = []

        for i, ch in enumerate(target_chapters):
            ch_title = ch["title"]
            ch_role = ch.get("role", "chapter")
            ch_session_id = f"{self.book_title}_{self.fingerprint}_ch{i+1}"
            # 章ディレクトリは book_sessions/<book>_<fp>/ 配下に入れ子にする（グローバル
            # セッション上限 SessionState.MAX_STATE_SESSIONS の対象外である state/book_sessions/
            # を利用し、章が個別セッションとして誤カウントされて他セッションに追い出される、
            # あるいは書籍1冊の章数だけで自壊するのを防ぐ）。書籍が削除される時は章も一緒に
            # 削除される（_cleanup_old_book_sessions()、意図通り）。
            ch_state_dir = self.session_dir / "chapters_state" / f"ch{i+1}"
            ch_state = SessionState(session_id=ch_session_id, base_dir=ch_state_dir)

            print_log(f"\n--- Processing [{ch_role}] {ch_title} ({i+1}/{len(target_chapters)}) ---")

            # 章単位 resume: 完了済みの章はスキップして既存出力を再利用する。
            # 出力パスは成功時に output_paths.json として保存し、次回起動時に参照する。
            output_paths_cache = ch_state.session_dir / "output_paths.json"
            if output_paths_cache.exists():
                try:
                    saved = json.loads(output_paths_cache.read_text(encoding="utf-8"))
                    if saved and all(Path(p).exists() for p in saved):
                        print_log(f"  [BookManager] スキップ（完了済み）: {ch_title}")
                        chapter_sessions.append({"title": ch_title, "output_paths": saved})
                        continue
                except Exception:
                    pass  # キャッシュ破損時は再処理

            try:
                # シンプルモード判定（前書きや後書き用）
                is_simple = "Coda" in ch_title or ch_role in ["preface", "appendix"]

                # パイプライン実行: 各章を独立した「論文」として完結させ、物理ファイルを出力させる
                processed_paths = run_pipeline(
                    input_path=ch["path"],
                    api_key=self.api_key,
                    session_id=ch_session_id,
                    state_base_dir=ch_state_dir,
                    is_book=True,
                    title=ch_title,
                    resume_content=self.global_resume or None,
                    glossary_path=glossary_path_str,
                    model=self.model,
                    pdf_mode=book_pdf_mode,
                    simple_mode=is_simple,
                    resume_only=resume_only,
                    structure_only=structure_only,
                    max_pages=max_pages,
                    heavy_ocr=heavy_ocr,
                    thinking_level=thinking_level,
                    tier=tier,
                    resume_from=resume_from,
                    **pipeline_kwargs
                )

                str_paths = [str(p) for p in processed_paths]
                # 次回実行時のスキップ用にパスを保存
                output_paths_cache.write_text(
                    json.dumps(str_paths, ensure_ascii=False), encoding="utf-8"
                )
                chapter_sessions.append({"title": ch_title, "output_paths": str_paths})

            except Exception as e:
                import traceback
                print_log(f"  [Error] Chapter '{ch_title}' failed: {e}")
                print_log(traceback.format_exc())
                failed_chapters.append(ch_title)
                chapter_sessions.append({
                    "title": ch_title,
                    "output_paths": [],
                })

        # 失敗章のサマリー
        if failed_chapters:
            print_log(f"\n  [BookManager] ⚠️ 失敗した章 ({len(failed_chapters)}件):")
            for t in failed_chapters:
                print_log(f"    - {t}")
            print_log("  再実行すると完了済みの章はスキップされ、失敗した章のみ再処理されます。")

        # 3. 統合
        if chapter_sessions:
            print_log("\n--- Consolidating Chapters ---")
            integrator = StateIntegrator(book_title=self.book_title, session_dir=str(self.session_dir))
            output_paths = integrator.integrate_to_book(chapter_sessions, global_resume=self.global_resume)
            self._cleanup_old_book_sessions()
            return output_paths
        return []

    MAX_BOOK_SESSIONS = 5

    def _cleanup_old_book_sessions(self):
        """book_sessions/ 以下の古いセッションを削除し MAX_BOOK_SESSIONS 以内に収める。"""
        from .config import STATE_DIR
        book_sessions_dir = STATE_DIR / "book_sessions"
        if not book_sessions_dir.exists():
            return
        dirs = [d for d in book_sessions_dir.iterdir() if d.is_dir()]
        if len(dirs) <= self.MAX_BOOK_SESSIONS:
            return
        dirs.sort(key=lambda d: d.stat().st_mtime)
        to_delete = dirs[:len(dirs) - self.MAX_BOOK_SESSIONS]
        print_log(f"  [BookManager] 古い書籍セッションを削除（保持上限: {self.MAX_BOOK_SESSIONS}）")
        for d in to_delete:
            try:
                shutil.rmtree(d)
                print_log(f"    削除: {d.name}")
            except Exception as e:
                print_log(f"    削除失敗 {d.name}: {e}")
