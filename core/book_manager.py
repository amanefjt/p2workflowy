import json
import shutil
import fitz
import re
import hashlib
import threading
import concurrent.futures
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from .config import STATE_DIR, SessionState, print_log, set_log_prefix
from .engine.p1_ingest.pdf_splitter import PDFSplitter
from .engine.p1_ingest.routing import decide_pdf_mode as _decide_book_pdf_mode
from .engine.p3_structure.state_integrator import StateIntegrator
from .llm_client import call_gemini, get_default_model, load_coreprompts, GeminiTier, apply_tier_settings, key_rotator

# gemini-3.5-flash（旧 DEFAULT_MODEL_RESUME）は公称入力上限（1,048,576 tok）とは別に、
# 単発リクエストで実測 ~186,000〜187,000 tok（本文字数にして概ね 735,000 字前後）を超えると
# 400 INVALID_ARGUMENT を返す（ドキュメント未記載の実挙動。troubleshooting_log I-20）。
# 2026-07-22 に DEFAULT_MODEL_RESUME を gemini-3.6-flash に切替した際はこの上限が
# 3.6-flash でも再現するか未検証のまま、保守的にこのガードを引き継いでいた。
# 2026-09-22、有料キー・gemini-3.6-flash で 1,113,493字（279,358tok）を単発送信し
# 400 が再現しないことを実測で確認した（troubleshooting_log.md I-46）。そのため
# このガードは「有料キーが無い（＝無料枠モデルにフォールバックせざるを得ない）場合」
# にのみ適用する。有料キーがあるときは resume モデル（gemini-3.6-flash）のまま
# 単発送信してよい（BookManager._generate_global_context_single 参照）。
RESUME_MODEL_SAFE_CHAR_LIMIT = 600_000

# 無料枠の TPM（Tokens Per Minute）上限は Lite・Flash 問わず一律 250,000（実際に踏んだ
# 429 のエラー詳細でも実測・再確認済み。docs/gemini_models.md §4）。字/トークン比は
# 文書により 3.9〜4.5 程度ブレる（troubleshooting_log I-32）ため、実測比（2026-09-22:
# gemini-3.6-flash で 1,113,493字 = 279,358tok ≒ 3.99字/tok）を基準に、既存の
# RESUME_MODEL_SAFE_CHAR_LIMIT と同程度の安全マージン（実測しきい値の8割程度）を
# 取って 800,000 字とする。無料キーのみ（有料キーが無い）環境でこれを超える書籍は、
# 単発リクエストでは無料枠のどのモデル・どのキーでも TPM 超過で 429 が確定するため、
# チャンク分割の map-reduce 処理に回す（troubleshooting_log.md I-46）。
FREE_TIER_TPM_SAFE_CHAR_LIMIT = 800_000

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

    def _get_paid_key(self) -> Optional[str]:
        """設定済みのキーが有料キーならそれを返す（無料キーまたは未設定なら None）。"""
        if key_rotator.is_configured() and key_rotator.current_tier() == "paid":
            return key_rotator.current()
        return None

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

        # トークン制限対策（極端に巨大な書籍のみ対象の最終防衛ライン）
        MAX_CHARS = 1_200_000
        if len(full_text) > MAX_CHARS:
            print_log(f"  [BookManager] テキストサンプリング実行 ({len(full_text)} chars)")
            full_text = full_text[:800_000] + "\n\n[...Skipped...]\n\n" + full_text[-400_000:]
        else:
            print_log(f"  [BookManager] フルテキスト抽出完了 ({len(full_text)} chars)")

        prompts = load_coreprompts()
        paid_key = self._get_paid_key()

        # 単発送信できる条件: ①ユーザーが --model を明示指定 / ②有料キーがある
        # （TPM上限は無料枠のみの制約） / ③無料枠でもTPM安全域(800,000字)に収まる。
        # いずれにも該当しない＝無料キーのみでTPM安全域を超える場合だけチャンク分割する。
        if self.model or paid_key or len(full_text) <= FREE_TIER_TPM_SAFE_CHAR_LIMIT:
            self._generate_global_context_single(full_text, expertise, prompts, paid_key)
        else:
            print_log(f"  [BookManager] 全文が無料枠のTPM安全域（{FREE_TIER_TPM_SAFE_CHAR_LIMIT}字）を超え、有料キーも無いためチャンク分割で処理します")
            self._generate_global_context_chunked(full_text, expertise, prompts)

        # 結果を確実に保存して次回スキップ可能にする
        context_file = self.session_dir / "global_context.json"
        save_data = {
            "resume": self.global_resume,
            "glossary": self.global_glossary,
            "book_title": self.book_title
        }
        context_file.write_text(json.dumps(save_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print_log(f"  [BookManager] Global Context を保存しました: {context_file.absolute()}")

    def _generate_global_context_single(self, full_text: str, expertise: str, prompts: dict, paid_key: Optional[str]):
        """全文を1回のリクエストで送って生成する経路。

        _generate_global_context() のガード条件（--model明示 / 有料キーあり /
        無料枠TPM安全域内）のいずれかを満たす場合に使う。
        """
        if self.model:
            # ユーザー明示指定時は従来どおり尊重し、フォールバックは適用しない
            resume_model = self.model
            glossary_model = self.model
            key, key_pinned = self.api_key, False
        elif paid_key:
            # 有料キーがあれば resume モデルのまま1回で送る。TPM上限は無料枠のみの
            # 制約であり、400字数ガードも gemini-3.6-flash では未再現（RESUME_MODEL_SAFE_CHAR_LIMIT
            # 冒頭コメント・troubleshooting_log.md I-46 参照）なのでフォールバック不要。
            resume_model = get_default_model("resume")
            glossary_model = resume_model
            key, key_pinned = paid_key, True
        else:
            resume_model = get_default_model("resume")
            if len(full_text) > RESUME_MODEL_SAFE_CHAR_LIMIT:
                print_log(f"  [BookManager] 全文が{RESUME_MODEL_SAFE_CHAR_LIMIT}字超のため resume モデルの実効入力上限を回避し既定モデルへフォールバック")
                resume_model = get_default_model("default")
            glossary_model = self.model  # None → 既存どおり tier 追従（無料枠なら自動でLiteへ）
            key, key_pinned = self.api_key, False

        print_log("  [BookManager] 書籍全体のレジュメを生成中...")
        resume_prompt = prompts.get("BOOK_SUMMARY_PROMPT", "").replace("{expertise}", expertise) \
                                     .replace("{context_guide}", "書籍全体の核心的問い、論理構成を俯瞰して下さい。") \
                                     .replace("{text}", full_text)
        self.global_resume = call_gemini(resume_prompt, api_key=key, model=resume_model, thinking_level="High", key_pinned=key_pinned)

        print_log("  [BookManager] 書籍全体の共通用語集を生成中...")
        glossary_prompt = prompts.get("KEYWORD_EXTRACTION_PROMPT", "").replace("{expertise}", expertise) \
                                         .replace("{text}", full_text)
        glossary_json = call_gemini(glossary_prompt, api_key=key, model=glossary_model, response_mime_type="application/json", key_pinned=key_pinned)
        try:
            self.global_glossary = json.loads(glossary_json)
        except:
            self.global_glossary = []

    @staticmethod
    def _split_into_chunks(full_text: str, max_chars: int) -> List[str]:
        """段落境界（\\n\\n）を優先して max_chars 以下のチャンクに分割する。"""
        paragraphs = full_text.split("\n\n")
        chunks: List[str] = []
        current = ""
        for para in paragraphs:
            candidate = f"{current}\n\n{para}" if current else para
            if len(candidate) > max_chars and current:
                chunks.append(current)
                current = para
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks

    def _generate_global_context_chunked(self, full_text: str, expertise: str, prompts: dict):
        """無料キーのみ・かつ無料枠TPM安全域を超える場合の map-reduce 処理。

        チャンクごとに部分レジュメ・部分用語集を無料枠モデルで生成し、レジュメは
        最後にもう1回のLLM呼び出しで統合し直す（用語集はコード側で重複除去して結合）。
        書籍1冊あたりのAPI呼び出し数は増えるが、無料枠のみでも全文を欠落なく処理できる。
        """
        chunks = self._split_into_chunks(full_text, FREE_TIER_TPM_SAFE_CHAR_LIMIT)
        print_log(f"  [BookManager] 全文を{len(chunks)}チャンクに分割して処理します")

        partial_resumes = []
        partial_glossaries = []
        for i, chunk in enumerate(chunks, 1):
            print_log(f"  [BookManager] チャンク {i}/{len(chunks)} のレジュメを生成中...")
            chunk_prompt = prompts.get("BOOK_SUMMARY_PROMPT", "").replace("{expertise}", expertise) \
                                         .replace("{context_guide}", f"これは書籍全体のうち分割ブロック {i}/{len(chunks)} 番目です。このブロックの範囲内で核心的な議論を俯瞰して下さい。") \
                                         .replace("{text}", chunk)
            partial = call_gemini(chunk_prompt, api_key=self.api_key, thinking_level="High")
            partial_resumes.append(f"## [分割ブロック {i}/{len(chunks)}]\n\n{partial}")

            print_log(f"  [BookManager] チャンク {i}/{len(chunks)} の用語集を生成中...")
            chunk_glossary_prompt = prompts.get("KEYWORD_EXTRACTION_PROMPT", "").replace("{expertise}", expertise) \
                                             .replace("{text}", chunk)
            glossary_json = call_gemini(chunk_glossary_prompt, api_key=self.api_key, response_mime_type="application/json")
            try:
                partial_glossaries.extend(json.loads(glossary_json))
            except:
                pass

        print_log("  [BookManager] 部分レジュメを統合中...")
        combine_prompt = prompts.get("BOOK_SUMMARY_COMBINE_PROMPT", "").replace("{expertise}", expertise) \
                                         .replace("{partial_summaries}", "\n\n---\n\n".join(partial_resumes))
        self.global_resume = call_gemini(combine_prompt, api_key=self.api_key, thinking_level="High")

        # 用語集は重複除去して結合（英語表記を大文字小文字無視でキーに、先勝ち）
        seen = set()
        merged_glossary = []
        for item in partial_glossaries:
            dedup_key = str(item.get("en", "")).strip().lower()
            if dedup_key and dedup_key not in seen:
                seen.add(dedup_key)
                merged_glossary.append(item)
        self.global_glossary = merged_glossary

    def run(self, resume_only: bool = False, structure_only: bool = False, max_chapters: Optional[int] = None,
            book_concurrency: Optional[int] = None, vlm_concurrency: Optional[int] = None,
            **pipeline_kwargs) -> List[str]:
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
        from .llm_client import key_rotator
        target_chapters = chapters[:max_chapters] if max_chapters else chapters
        n_chapters = len(target_chapters)
        chapter_sessions: List[Optional[Dict[str, Any]]] = [None] * n_chapters

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

        failed_chapters: List[str] = []
        failed_lock = threading.Lock()

        # --- 章単位 resume（完了済み章のスキップ判定）は LLM を呼ばない安価な処理なので、
        # 並列投入の前に直列で済ませる。スキップされた章はスレッドを一切消費しない。---
        pending: List[Tuple[int, Dict[str, Any], str, Path]] = []
        for i, ch in enumerate(target_chapters):
            ch_title = ch["title"]
            ch_session_id = f"{self.book_title}_{self.fingerprint}_ch{i+1}"
            # 章ディレクトリは book_sessions/<book>_<fp>/ 配下に入れ子にする（グローバル
            # セッション上限 SessionState.MAX_STATE_SESSIONS の対象外である state/book_sessions/
            # を利用し、章が個別セッションとして誤カウントされて他セッションに追い出される、
            # あるいは書籍1冊の章数だけで自壊するのを防ぐ）。書籍が削除される時は章も一緒に
            # 削除される（_cleanup_old_book_sessions()、意図通り）。
            ch_state_dir = self.session_dir / "chapters_state" / f"ch{i+1}"
            ch_state = SessionState(session_id=ch_session_id, base_dir=ch_state_dir)

            # 章単位 resume: 完了済みの章はスキップして既存出力を再利用する。
            # 出力パスは成功時に output_paths.json として保存し、次回起動時に参照する。
            output_paths_cache = ch_state.session_dir / "output_paths.json"
            if output_paths_cache.exists():
                try:
                    saved = json.loads(output_paths_cache.read_text(encoding="utf-8"))
                    if saved and all(Path(p).exists() for p in saved):
                        print_log(f"  [BookManager] スキップ（完了済み）: {ch_title}")
                        chapter_sessions[i] = {"title": ch_title, "output_paths": saved}
                        continue
                except Exception:
                    pass  # キャッシュ破損時は再処理

            pending.append((i, ch, ch_session_id, ch_state_dir))

        def run_one_chapter(idx: int, ch: Dict[str, Any], ch_session_id: str, ch_state_dir: Path,
                             chapter_vlm_concurrency: Optional[int] = None) -> None:
            """1章を run_pipeline() で処理し、結果を chapter_sessions[idx] に書き込む。
            例外は内部で吸収し、失敗章として記録したうえで他章の処理を止めない。"""
            ch_title = ch["title"]
            ch_role = ch.get("role", "chapter")
            print_log(f"\n--- Processing [{ch_role}] {ch_title} ({idx+1}/{n_chapters}) ---")

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
                    vlm_concurrency=chapter_vlm_concurrency,
                    **pipeline_kwargs
                )

                str_paths = [str(p) for p in processed_paths]
                # 次回実行時のスキップ用にパスを保存
                output_paths_cache = ch_state_dir / "output_paths.json"
                output_paths_cache.write_text(
                    json.dumps(str_paths, ensure_ascii=False), encoding="utf-8"
                )
                chapter_sessions[idx] = {"title": ch_title, "output_paths": str_paths}

            except Exception as e:
                import traceback
                print_log(f"  [Error] Chapter '{ch_title}' failed: {e}")
                print_log(traceback.format_exc())
                with failed_lock:
                    failed_chapters.append(ch_title)
                chapter_sessions[idx] = {
                    "title": ch_title,
                    "output_paths": [],
                }

        # --- 章並列化の有効化条件（安全装置）---
        # 章並列化は「単一の有料キーを複数スレッドで共有する」ことでのみ行う（無料キーの
        # 複数プロジェクトへの分散は2026-09-23に撤去済み、docs/management/requirements_log.md
        # 同日エントリ）。無料キー使用時は常に直列にする——無料ティアはRPMの絶対値が低く、
        # 単一キーへの複数スレッド同時アクセスでも429が起きやすいため（有料ティアは
        # レート上限が高く、実測ではなく既存の429/503リトライ・バックオフに委ねる設計、
        # docs/model_optimization.md §2.5 参照）。
        DEFAULT_BOOK_CONCURRENCY = 4
        can_parallelize = tier == "paid" and len(pending) > 1

        if not can_parallelize:
            effective_concurrency = 1
        elif book_concurrency is not None:
            effective_concurrency = max(1, book_concurrency)
        else:
            effective_concurrency = min(DEFAULT_BOOK_CONCURRENCY, len(pending))

        if effective_concurrency <= 1 or len(pending) <= 1:
            print_log(f"  [BookManager] 章を直列処理します（対象{len(pending)}章）。")
            for idx, ch, ch_session_id, ch_state_dir in pending:
                run_one_chapter(idx, ch, ch_session_id, ch_state_dir)
        else:
            # 1章あたりのVLM同時実行数: 複数章が同時にVLMを叩くとメモリ（同時に載る画像枚数）
            # とAPIレート負荷が積み上がるため、章並列数に応じて絞る。OCRManager.
            # VLM_SEMAPHORE_LIMIT=20 は単一章前提の値なので、これを章並列数で割った値を
            # 目安にする。下限4は1章あたりの並列性を極端に潰さないための保守的な床
            # （判断根拠は docs/model_optimization.md §10 を参照）。
            chapter_vlm_concurrency = (
                vlm_concurrency if vlm_concurrency is not None
                else max(4, 20 // effective_concurrency)
            )
            print_log(
                f"  [BookManager] 章並列処理を有効化: {effective_concurrency}並列"
                f"（有料キー1本を共有 / 処理対象{len(pending)}章、"
                f"章あたりVLM同時実行数={chapter_vlm_concurrency}）"
            )

            def worker(idx: int, ch: Dict[str, Any], ch_session_id: str, ch_state_dir: Path) -> None:
                set_log_prefix(f"[ch{idx+1}] ")
                try:
                    run_one_chapter(idx, ch, ch_session_id, ch_state_dir, chapter_vlm_concurrency)
                finally:
                    set_log_prefix(None)

            with concurrent.futures.ThreadPoolExecutor(max_workers=effective_concurrency) as executor:
                futures = [
                    executor.submit(worker, idx, ch, ch_session_id, ch_state_dir)
                    for idx, ch, ch_session_id, ch_state_dir in pending
                ]
                for f in concurrent.futures.as_completed(futures):
                    # run_one_chapter は例外を内部で吸収するため、ここに到達する例外は
                    # worker() 自体の想定外のバグのみ。ログに残しつつ他章の完了は妨げない。
                    exc = f.exception()
                    if exc is not None:
                        print_log(f"  [BookManager] ⚠️ 章ワーカーで想定外の例外: {exc}")

        # 失敗章のサマリー
        if failed_chapters:
            print_log(f"\n  [BookManager] ⚠️ 失敗した章 ({len(failed_chapters)}件):")
            for t in failed_chapters:
                print_log(f"    - {t}")
            print_log("  再実行すると完了済みの章はスキップされ、失敗した章のみ再処理されます。")

        # 3. 統合
        # chapter_sessions は StateIntegrator.integrate_to_book() へ渡す順序がそのまま本の
        # 並び順になるため、完了順ではなく [None]*N をインデックスで埋める方式で並び順を維持する。
        if any(s is not None for s in chapter_sessions):
            print_log("\n--- Consolidating Chapters ---")
            integrator = StateIntegrator(book_title=self.book_title, session_dir=str(self.session_dir))
            resolved_sessions = [
                s if s is not None else {"title": "unknown", "output_paths": []}
                for s in chapter_sessions
            ]
            output_paths = integrator.integrate_to_book(resolved_sessions, global_resume=self.global_resume)
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
