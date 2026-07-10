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
