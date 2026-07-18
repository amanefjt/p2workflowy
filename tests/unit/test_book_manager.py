"""
BookManager のユニットテスト

テスト対象:
  - 章単位 resume: output_paths.json によるスキップ
  - 失敗章の記録と継続
  - book_sessions クリーンアップ
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call


# ============================================================
# helpers
# ============================================================

def make_manager(tmp_path: Path):
    """BookManager を実 I/O なしで生成する。"""
    with patch("core.book_manager.PDFSplitter"), \
         patch("core.book_manager.BookManager._get_pdf_fingerprint", return_value="abc123"), \
         patch("core.book_manager.fitz.open"):
        from core.book_manager import BookManager
        m = BookManager.__new__(BookManager)
        m.input_path = str(tmp_path / "book.pdf")
        m.api_key = "test_key"
        m.model = None
        m.book_title = "testbook"
        m.fingerprint = "abc123"
        m.session_dir = tmp_path / "book_sessions" / "testbook_abc123"
        m.session_dir.mkdir(parents=True, exist_ok=True)
        m.global_resume = "global summary"
        m.global_glossary = []
        return m


def make_session_dir(base: Path, session_id: str) -> Path:
    """章セッションディレクトリを作成して返す。"""
    from core.config import STATE_DIR
    d = base / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ============================================================
# 章単位 resume（output_paths.json によるスキップ）
# ============================================================

class TestChapterResume:
    def test_skips_chapter_with_valid_output_paths(self, tmp_path):
        """output_paths.json が存在し、指定ファイルが実在する章はスキップされる。"""
        from core.book_manager import BookManager

        manager = make_manager(tmp_path)

        # 既完了の章 PDF と出力ファイルを準備
        ch_pdf = tmp_path / "ch1.pdf"
        ch_pdf.write_bytes(b"dummy")
        md_file = tmp_path / "ch1_p2.md"
        md_file.write_text("content")
        txt_file = tmp_path / "ch1_p2.txt"
        txt_file.write_text("content")

        # chapter セッションの output_paths.json を作成
        from core.config import STATE_DIR
        session_id = f"testbook_abc123_ch1"
        from core.config import SessionState
        ch_state = SessionState(session_id=session_id)
        cache = ch_state.session_dir / "output_paths.json"
        cache.write_text(json.dumps([str(md_file), str(txt_file)]))

        chapters = [{"title": "Chapter 1", "path": str(ch_pdf), "role": "chapter"}]
        chapter_sessions = []
        skipped = []

        # BookManager のループロジックを単体で検証
        for i, ch in enumerate(chapters):
            ch_session_id = f"testbook_abc123_ch{i+1}"
            from core.config import SessionState
            ch_state = SessionState(session_id=ch_session_id)
            output_paths_cache = ch_state.session_dir / "output_paths.json"

            if output_paths_cache.exists():
                saved = json.loads(output_paths_cache.read_text(encoding="utf-8"))
                if saved and all(Path(p).exists() for p in saved):
                    skipped.append(ch["title"])
                    chapter_sessions.append({"title": ch["title"], "output_paths": saved})
                    continue

        assert "Chapter 1" in skipped
        assert chapter_sessions[0]["output_paths"] == [str(md_file), str(txt_file)]

    def test_does_not_skip_when_output_files_missing(self, tmp_path):
        """output_paths.json はあるが実ファイルが存在しない場合はスキップしない。"""
        from core.config import SessionState
        session_id = "testbook_abc123_ch1"
        ch_state = SessionState(session_id=session_id)
        cache = ch_state.session_dir / "output_paths.json"
        # 存在しないファイルを指定
        cache.write_text(json.dumps(["/nonexistent/ch1_p2.md"]))

        saved = json.loads(cache.read_text(encoding="utf-8"))
        should_skip = saved and all(Path(p).exists() for p in saved)
        assert should_skip is False

    def test_does_not_skip_when_cache_absent(self, tmp_path):
        """output_paths.json がない場合はスキップしない。"""
        from core.config import SessionState
        session_id = "testbook_abc123_ch99"
        ch_state = SessionState(session_id=session_id)
        assert not (ch_state.session_dir / "output_paths.json").exists()


# ============================================================
# 失敗章の記録と継続
# ============================================================

class TestChapterFailure:
    def test_failure_recorded_and_continues(self, tmp_path):
        """1章が失敗しても他章の処理を継続し、失敗リストに記録される。"""
        manager = make_manager(tmp_path)

        good_md = tmp_path / "good_p2.md"
        good_md.write_text("ok")
        good_txt = tmp_path / "good_p2.txt"
        good_txt.write_text("ok")

        chapters = [
            {"title": "Good Chapter", "path": str(tmp_path / "good.pdf"), "role": "chapter"},
            {"title": "Bad Chapter",  "path": str(tmp_path / "bad.pdf"),  "role": "chapter"},
        ]

        failed_chapters = []
        chapter_sessions = []

        def fake_run_pipeline(**kwargs):
            if "bad" in kwargs.get("input_path", ""):
                raise RuntimeError("OCR failed")
            return [good_md, good_txt]

        for i, ch in enumerate(chapters):
            try:
                paths = fake_run_pipeline(input_path=ch["path"])
                chapter_sessions.append({"title": ch["title"], "output_paths": [str(p) for p in paths]})
            except Exception as e:
                failed_chapters.append(ch["title"])
                chapter_sessions.append({"title": ch["title"], "output_paths": []})

        assert "Bad Chapter" in failed_chapters
        assert len(chapter_sessions) == 2
        assert chapter_sessions[0]["output_paths"] != []  # Good chapter has output
        assert chapter_sessions[1]["output_paths"] == []  # Bad chapter has no output


# ============================================================
# 書籍全体レジュメの章パイプラインへの受け渡し
# ============================================================

class TestGlobalResumeHandoff:
    def test_run_passes_global_resume_to_chapter_pipeline(self, tmp_path):
        """Phase 0 で生成した global_resume が章の run_pipeline へ resume_content として渡る（I-9 断絶の解消）。"""
        manager = make_manager(tmp_path)
        manager.book_title = "resumebook"
        manager.fingerprint = "resumefp"
        manager.session_dir = tmp_path / "book_sessions" / "resumebook_resumefp"
        manager.session_dir.mkdir(parents=True, exist_ok=True)

        # Phase 0 キャッシュを用意して _generate_global_context をスキップさせる
        (manager.session_dir / "global_context.json").write_text(
            json.dumps({"resume": "GLOBAL_RESUME_TEXT", "glossary": [], "book_title": manager.book_title}),
            encoding="utf-8",
        )

        ch_pdf = tmp_path / "ch1.pdf"
        ch_pdf.write_bytes(b"%PDF-1.4 dummy")

        splitter = MagicMock()
        splitter.split.return_value = [{"title": "Ch1", "path": str(ch_pdf), "role": "chapter"}]

        captured = {}

        def fake_run_pipeline(**kwargs):
            captured.update(kwargs)
            return []

        with patch("core.book_manager.PDFSplitter", return_value=splitter), \
             patch("core.book_manager.apply_tier_settings"), \
             patch("core.engine.p1_ingest.pdf_ingester.diagnose_pdf_quality", return_value=True), \
             patch("core.engine.p1_ingest.spread_splitter.is_spread_pdf", return_value=False), \
             patch("core.pipeline.run_pipeline", side_effect=fake_run_pipeline):
            try:
                manager.run(max_chapters=1)
            except Exception:
                pass  # 統合フェーズ以降の失敗は本テストの関心外

        assert captured.get("resume_content") == "GLOBAL_RESUME_TEXT"


# ============================================================
# 書籍単位ルーティングの run() 配線（I-16 上層）
# ============================================================

def _run_with_routing_mocks(manager, is_spread, is_docling_ok, **run_kwargs):
    """is_spread_pdf / is_docling_viable をモックして manager.run() を実行し、
    run_pipeline に渡された kwargs を captured で返す。"""
    ch_pdf = Path(manager.input_path).parent / "ch1.pdf"
    ch_pdf.write_bytes(b"%PDF-1.4 dummy")
    splitter = MagicMock()
    splitter.split.return_value = [{"title": "Ch1", "path": str(ch_pdf), "role": "chapter"}]

    captured = {}

    def fake_run_pipeline(**kwargs):
        captured.update(kwargs)
        return []

    with patch("core.book_manager.PDFSplitter", return_value=splitter), \
         patch("core.book_manager.apply_tier_settings"), \
         patch("core.engine.p1_ingest.pdf_ingester.diagnose_pdf_quality", return_value=True), \
         patch("core.engine.p1_ingest.spread_splitter.is_spread_pdf", return_value=is_spread), \
         patch("core.engine.p1_ingest.spread_splitter.split_spread_pdf", return_value=str(ch_pdf)), \
         patch("core.engine.p1_ingest.docling_ingester.is_docling_viable", return_value=is_docling_ok), \
         patch("core.pipeline.run_pipeline", side_effect=fake_run_pipeline):
        try:
            manager.run(max_chapters=1, **run_kwargs)
        except Exception:
            pass  # 統合フェーズ以降の失敗は本テストの関心外
    return captured


class TestBookLevelRouting:
    def _make_ready_manager(self, tmp_path, title, fp):
        manager = make_manager(tmp_path)
        manager.book_title = title
        manager.fingerprint = fp
        manager.session_dir = tmp_path / "book_sessions" / f"{title}_{fp}"
        manager.session_dir.mkdir(parents=True, exist_ok=True)
        (manager.session_dir / "global_context.json").write_text(
            json.dumps({"resume": "R", "glossary": [], "book_title": title}),
            encoding="utf-8",
        )
        return manager

    def test_explicit_pdf_mode_is_respected_not_discarded(self, tmp_path):
        """I-16: 明示指定された pdf_mode が pop されて捨てられず章パイプラインに渡る。"""
        manager = self._make_ready_manager(tmp_path, "routebook1", "fp1")
        captured = _run_with_routing_mocks(
            manager, is_spread=False, is_docling_ok=True, pdf_mode="full_vlm"
        )
        assert captured.get("pdf_mode") == "full_vlm"

    def test_default_docling_viable_uses_hybrid_not_hardcoded_full_vlm(self, tmp_path):
        """I-16: pdf_mode 未指定・非見開き・Docling可能な書籍は hybrid（Doclingルート）になる。"""
        manager = self._make_ready_manager(tmp_path, "routebook2", "fp2")
        captured = _run_with_routing_mocks(manager, is_spread=False, is_docling_ok=True)
        assert captured.get("pdf_mode") == "hybrid"

        routing_file = manager.session_dir / "routing_decision.json"
        assert routing_file.exists()
        data = json.loads(routing_file.read_text(encoding="utf-8"))
        assert data["pdf_mode"] == "hybrid"
        assert data["reason"] == "docling_viable"

    def test_spread_pdf_forces_full_vlm_even_if_docling_viable(self, tmp_path):
        """I-16: 見開きスキャンはDocling可能でもVLMルートを優先する（規則②>③）。"""
        manager = self._make_ready_manager(tmp_path, "routebook3", "fp3")
        captured = _run_with_routing_mocks(manager, is_spread=True, is_docling_ok=True)
        assert captured.get("pdf_mode") == "full_vlm"


# ============================================================
# book_sessions クリーンアップ
# ============================================================

class TestBookSessionCleanup:
    def test_cleanup_removes_oldest_when_over_limit(self, tmp_path):
        """書籍セッションが MAX_BOOK_SESSIONS を超えたとき、最古のものが削除される。"""
        import time
        from core.book_manager import BookManager

        book_sessions_dir = tmp_path / "book_sessions"
        book_sessions_dir.mkdir()

        # 7セッション作成（上限5を超える）
        session_dirs = []
        for i in range(7):
            d = book_sessions_dir / f"book_{i}"
            d.mkdir()
            (d / "dummy.txt").write_text("x")
            time.sleep(0.01)  # mtime に差をつける
            session_dirs.append(d)

        manager = make_manager(tmp_path)
        manager.MAX_BOOK_SESSIONS = 5

        with patch("core.config.STATE_DIR", tmp_path):
            manager._cleanup_old_book_sessions()

        remaining = list(book_sessions_dir.iterdir())
        assert len(remaining) == 5
        # 最古の2件（book_0, book_1）が消えているはず
        names = {d.name for d in remaining}
        assert "book_0" not in names
        assert "book_1" not in names
        assert "book_6" in names

    def test_cleanup_noop_when_under_limit(self, tmp_path):
        """セッション数が上限以内ならクリーンアップは何もしない。"""
        book_sessions_dir = tmp_path / "book_sessions"
        book_sessions_dir.mkdir()
        for i in range(3):
            (book_sessions_dir / f"book_{i}").mkdir()

        manager = make_manager(tmp_path)
        manager.MAX_BOOK_SESSIONS = 10  # 上限を大きくして何も消えないことを確認

        before = len(list(book_sessions_dir.iterdir()))
        with patch("core.config.STATE_DIR", tmp_path):
            manager._cleanup_old_book_sessions()

        assert len(list(book_sessions_dir.iterdir())) == before


# ============================================================
# 書籍単位ルーティング規則（①〜④）
# ============================================================

class TestDecideBookPdfMode:
    def test_explicit_pdf_mode_takes_priority(self):
        """規則①: ユーザー明示指定は他の判定より優先される。"""
        from core.book_manager import _decide_book_pdf_mode
        mode, reason = _decide_book_pdf_mode("full_vlm", is_spread=False, is_docling_ok=True)
        assert (mode, reason) == ("full_vlm", "explicit_pdf_mode")

    def test_spread_pdf_forces_vlm_even_if_docling_viable(self):
        """規則②>③: 見開きスキャンはDocling可能でもVLMを優先する。"""
        from core.book_manager import _decide_book_pdf_mode
        mode, reason = _decide_book_pdf_mode(None, is_spread=True, is_docling_ok=True)
        assert (mode, reason) == ("full_vlm", "spread_pdf")

    def test_docling_viable_non_spread_uses_hybrid(self):
        """規則③: 見開きでなくDocling可能ならDoclingルート（hybrid=Docling優先）。"""
        from core.book_manager import _decide_book_pdf_mode
        mode, reason = _decide_book_pdf_mode(None, is_spread=False, is_docling_ok=True)
        assert (mode, reason) == ("hybrid", "docling_viable")

    def test_non_viable_non_spread_falls_back_to_vlm(self):
        """規則④: 見開きでもDocling可能でもない（劣化スキャン等）ならVLM。"""
        from core.book_manager import _decide_book_pdf_mode
        mode, reason = _decide_book_pdf_mode(None, is_spread=False, is_docling_ok=False)
        assert (mode, reason) == ("full_vlm", "docling_not_viable")
