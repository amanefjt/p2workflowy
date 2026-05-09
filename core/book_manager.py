import json
import shutil
import fitz
import re
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional

from .config import SessionState, print_log
from .pdf_splitter import PDFSplitter
from .engine.p3_structure.state_integrator import StateIntegrator
from .llm_client import call_gemini, get_default_model, load_coreprompts, GeminiTier, apply_tier_settings

class BookManager:
    """書籍全体のライフサイクル（全体解析 -> 分割 -> 処理 -> 統合）を管理する。"""

    def __init__(self, input_path: str, api_key: str, model: Optional[str] = None):
        self.input_path = Path(input_path)
        self.api_key = api_key
        self.model = model # 解決は後で行う (get_default_model のために tier 確定を待つ)
        self.book_title = self.input_path.stem
        
        # 物理データ主権: PDFの中身に応じた一意なハッシュを生成
        self.fingerprint = self._get_pdf_fingerprint(self.input_path)
        self.session_dir = Path("state/book_sessions") / f"{self.book_title}_{self.fingerprint}"
        
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

    def _generate_global_context(self):
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
        resume_prompt = prompts.get("GLOBAL_SUMMARY_PROMPT", "").replace("{expertise}", "文化人類学") \
                                     .replace("{context_guide}", "書籍全体の核心的問い、論理構成を俯瞰して下さい。") \
                                     .replace("{text}", full_text)
        
        self.global_resume = call_gemini(resume_prompt, api_key=self.api_key, model=self.model, thinking_level="High")

        # 2. 全体用語集生成
        print_log("  [BookManager] 書籍全体の共通用語集を生成中...")
        glossary_prompt = prompts.get("KEYWORD_EXTRACTION_PROMPT", "").replace("{expertise}", "文化人類学") \
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
        from .pdf_ingester import diagnose_pdf_quality
        
        # ティア状態の初期化（get_default_model を呼ぶ前に必要）
        tier = pipeline_kwargs.get("tier", "paid")
        apply_tier_settings(tier)
        
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
                    self._generate_global_context()
            except Exception as e:
                print_log(f"  [BookManager] キャッシュ読込エラー: {e}")
                self._generate_global_context()
        elif can_use_full_scan:
            self._generate_global_context()
        else:
            print_log("  [BookManager] PDF破損につき事後生成ルートへ倒れます。")
            self.global_resume = None

        # 用語集CSVの作成
        glossary_path = self.session_dir / "global_glossary.csv"
        if self.global_glossary:
            import csv
            with open(glossary_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["en", "ja", "definition"])
                writer.writeheader()
                writer.writerows(self.global_glossary)
        
        glossary_path_str = str(glossary_path) if glossary_path.exists() else None

        # 1. PDF 分割
        model_to_use = self.model or get_default_model("default")
        splitter = PDFSplitter(api_key=self.api_key, model=model_to_use)
        chapters = splitter.split(str(self.input_path), self.session_dir / "chapters")
        
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
        resume_only = pipeline_kwargs.get("resume_only", False)
        structure_only = pipeline_kwargs.get("structure_only", False)
        resume_from = pipeline_kwargs.get("resume_from", None)
        
        explicit_keys = [
            "glossary_path", "pdf_mode", "thinking_level", "tier", 
            "heavy_ocr", "max_pages", "resume_only", "structure_only", "api_key", "model", "resume_from"
        ]
        for key in explicit_keys:
            pipeline_kwargs.pop(key, None)

        failed_chapters = []

        for i, ch in enumerate(target_chapters):
            ch_title = ch["title"]
            ch_role = ch.get("role", "chapter")
            ch_session_id = f"{self.book_title}_{self.fingerprint}_ch{i+1}"
            ch_state = SessionState(session_id=ch_session_id)

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
                # 注意: resume_content に self.global_resume を渡すと章の要約が全体要約で上書きされるため None にする
                processed_paths = run_pipeline(
                    input_path=ch["path"],
                    api_key=self.api_key,
                    session_id=ch_session_id,
                    is_book=True,
                    title=ch_title,
                    resume_content=None,
                    glossary_path=glossary_path_str,
                    model=model_to_use,
                    pdf_mode="full_vlm",
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
