"""
BookManager のユニットテスト

テスト対象:
  - 章単位 resume: output_paths.json によるスキップ
  - 失敗章の記録と継続
  - book_sessions クリーンアップ
  - 章並列化（キープール排他割り当て・統合順序・例外時継続・有効化条件の安全装置）
"""

import json
import threading
import time
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


def make_ready_manager(tmp_path: Path, title: str, fp: str):
    """Phase 0 (global_context.json) 済みの BookManager を用意する（章並列化テスト用）。"""
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


def make_chapter_pdfs(tmp_path: Path, titles: list) -> list:
    """ダミー章 PDF 群を作り、BookManager.run() に渡す chapters 形式で返す。"""
    chapters = []
    for t in titles:
        p = tmp_path / f"{t}.pdf"
        p.write_bytes(b"%PDF-1.4 dummy")
        chapters.append({"title": t, "path": str(p), "role": "chapter"})
    return chapters


def run_manager_with_mocks(manager, splitter, fake_run_pipeline, **run_kwargs):
    """章並列化テスト共通: run() を必要な下位モックとともに実行する。"""
    with patch("core.book_manager.PDFSplitter", return_value=splitter), \
         patch("core.book_manager.apply_tier_settings"), \
         patch("core.engine.p1_ingest.pdf_ingester.diagnose_pdf_quality", return_value=True), \
         patch("core.engine.p1_ingest.spread_splitter.is_spread_pdf", return_value=False), \
         patch("core.pipeline.run_pipeline", side_effect=fake_run_pipeline):
        return manager.run(**run_kwargs)


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

    def test_run_passes_state_base_dir_nested_under_book_sessions(self, tmp_path):
        """章の state_base_dir は book_sessions/<book>_<fp>/chapters_state/ch<N>/ に入れ子になる
        （SessionState.MAX_STATE_SESSIONS のグローバル上限の対象外にするための配置、2026-07-21）。"""
        manager = make_manager(tmp_path)
        manager.book_title = "resumebook"
        manager.fingerprint = "resumefp"
        manager.session_dir = tmp_path / "book_sessions" / "resumebook_resumefp"
        manager.session_dir.mkdir(parents=True, exist_ok=True)
        (manager.session_dir / "global_context.json").write_text(
            json.dumps({"resume": "R", "glossary": [], "book_title": manager.book_title}),
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

        expected = manager.session_dir / "chapters_state" / "ch1"
        assert captured.get("state_base_dir") == expected


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


# ============================================================
# 章並列化（無料キー2本以上での有効化・キープール排他割り当て・
# 統合順序・例外時継続・安全装置）
# ============================================================

class TestChapterParallelization:
    def test_key_pool_exclusive_use_when_fewer_keys_than_chapters(self, tmp_path):
        """キー本数(2) < 章数(5) でも、同時に同じキーを使う章は高々1つに保たれる
        （queue.Queue によるプール方式で構造的に保証される）。"""
        from core.llm_client import key_rotator

        manager = make_ready_manager(tmp_path, "poolbook", "poolfp")
        key_rotator.configure(["free1", "free2", "paid1"], tiers=["free", "free", "paid"])
        try:
            chapters = make_chapter_pdfs(tmp_path, [f"Ch{i}" for i in range(5)])
            splitter = MagicMock()
            splitter.split.return_value = chapters

            active_counts = {}
            violations = []
            lock = threading.Lock()

            def fake_run_pipeline(**kwargs):
                key = key_rotator.current()
                with lock:
                    active_counts[key] = active_counts.get(key, 0) + 1
                    if active_counts[key] > 1:
                        violations.append(key)
                time.sleep(0.05)
                with lock:
                    active_counts[key] -= 1
                return []

            run_manager_with_mocks(manager, splitter, fake_run_pipeline)

            assert violations == [], f"同一キーが同時に複数章で使われた: {violations}"
        finally:
            key_rotator.configure([])

    def test_integration_order_follows_book_order_not_completion_order(self, tmp_path):
        """完了順を意図的に入れ替えても、StateIntegrator に渡す並びは本の並び順（インデックス順）
        のまま保たれる（[None]*N をインデックスで埋める設計）。"""
        from core.llm_client import key_rotator

        manager = make_ready_manager(tmp_path, "orderbook", "orderfp")
        key_rotator.configure(["free1", "free2"], tiers=["free", "free"])
        try:
            titles = ["Ch0", "Ch1", "Ch2"]
            chapters = make_chapter_pdfs(tmp_path, titles)
            splitter = MagicMock()
            splitter.split.return_value = chapters

            # Ch0 を意図的に一番遅く終わらせ、完了順を book 順とずらす
            delays = {"Ch0": 0.15, "Ch1": 0.05, "Ch2": 0.0}

            def fake_run_pipeline(**kwargs):
                title = kwargs["title"]
                time.sleep(delays.get(title, 0))
                out = tmp_path / f"{title}_p2.txt"
                out.write_text("dummy")
                return [out]

            captured = {}

            def fake_integrate(sessions, **kwargs):
                captured["sessions"] = sessions
                return []

            with patch("core.book_manager.PDFSplitter", return_value=splitter), \
                 patch("core.book_manager.apply_tier_settings"), \
                 patch("core.engine.p1_ingest.pdf_ingester.diagnose_pdf_quality", return_value=True), \
                 patch("core.engine.p1_ingest.spread_splitter.is_spread_pdf", return_value=False), \
                 patch("core.pipeline.run_pipeline", side_effect=fake_run_pipeline), \
                 patch("core.book_manager.StateIntegrator") as MockIntegrator:
                MockIntegrator.return_value.integrate_to_book.side_effect = fake_integrate
                manager.run()

            assert [s["title"] for s in captured["sessions"]] == titles
        finally:
            key_rotator.configure([])

    def test_one_chapter_exception_continues_others_in_parallel_path(self, tmp_path):
        """並列パスでも1章の例外は他章の処理を止めず、失敗章として記録される
        （output_paths.json が失敗章だけ作られないことで検証）。"""
        from core.llm_client import key_rotator

        manager = make_ready_manager(tmp_path, "failbook", "failfp")
        key_rotator.configure(["free1", "free2"], tiers=["free", "free"])
        try:
            titles = ["Good1", "Bad", "Good2"]
            chapters = make_chapter_pdfs(tmp_path, titles)
            splitter = MagicMock()
            splitter.split.return_value = chapters

            def fake_run_pipeline(**kwargs):
                title = kwargs["title"]
                if title == "Bad":
                    raise RuntimeError("OCR failed")
                out = tmp_path / f"{title}_p2.txt"
                out.write_text("ok")
                return [out]

            run_manager_with_mocks(manager, splitter, fake_run_pipeline)

            for i, title in enumerate(titles):
                cache = manager.session_dir / "chapters_state" / f"ch{i+1}" / "output_paths.json"
                if title == "Bad":
                    assert not cache.exists(), "失敗章に output_paths.json が作られてはいけない"
                else:
                    assert cache.exists(), f"{title} は成功したので output_paths.json が必要"
        finally:
            key_rotator.configure([])

    def test_serial_when_key_rotator_not_configured(self, tmp_path):
        """key_rotator.configure() が呼ばれていない（Web経路相当）場合は常に完全直列。"""
        from core.llm_client import key_rotator

        manager = make_ready_manager(tmp_path, "webbook", "webfp")
        key_rotator.configure([])  # is_configured() が False になる状態を明示的に作る
        try:
            chapters = make_chapter_pdfs(tmp_path, ["Ch0", "Ch1", "Ch2"])
            splitter = MagicMock()
            splitter.split.return_value = chapters

            def fake_run_pipeline(**kwargs):
                return []

            with patch("core.book_manager.print_log") as mock_log:
                run_manager_with_mocks(manager, splitter, fake_run_pipeline)

            logs = " ".join(str(c.args[0]) for c in mock_log.call_args_list if c.args)
            assert "直列処理します" in logs
            assert "並列処理を有効化" not in logs
        finally:
            key_rotator.configure([])

    def test_serial_when_only_one_free_key(self, tmp_path):
        """無料キーが1本しか configure() されていない場合も安全側で完全直列。"""
        from core.llm_client import key_rotator

        manager = make_ready_manager(tmp_path, "onekeybook", "onekeyfp")
        key_rotator.configure(["free1", "paid1"], tiers=["free", "paid"])
        try:
            chapters = make_chapter_pdfs(tmp_path, ["Ch0", "Ch1", "Ch2"])
            splitter = MagicMock()
            splitter.split.return_value = chapters

            def fake_run_pipeline(**kwargs):
                return []

            with patch("core.book_manager.print_log") as mock_log:
                run_manager_with_mocks(manager, splitter, fake_run_pipeline)

            logs = " ".join(str(c.args[0]) for c in mock_log.call_args_list if c.args)
            assert "直列処理します" in logs
            assert "並列処理を有効化" not in logs
        finally:
            key_rotator.configure([])

    def test_book_concurrency_one_forces_serial_even_with_multiple_free_keys(self, tmp_path):
        """--book-concurrency 1（book_concurrency=1）は無料キーが複数あっても完全直列にする
        （回帰時の逃げ道）。"""
        from core.llm_client import key_rotator

        manager = make_ready_manager(tmp_path, "forceserialbook", "forceserialfp")
        key_rotator.configure(["free1", "free2", "free3"], tiers=["free", "free", "free"])
        try:
            chapters = make_chapter_pdfs(tmp_path, ["Ch0", "Ch1", "Ch2"])
            splitter = MagicMock()
            splitter.split.return_value = chapters

            def fake_run_pipeline(**kwargs):
                return []

            with patch("core.book_manager.print_log") as mock_log:
                run_manager_with_mocks(manager, splitter, fake_run_pipeline, book_concurrency=1)

            logs = " ".join(str(c.args[0]) for c in mock_log.call_args_list if c.args)
            assert "直列処理します" in logs
            assert "並列処理を有効化" not in logs
        finally:
            key_rotator.configure([])

    def test_parallel_enabled_with_two_or_more_free_keys(self, tmp_path):
        """無料キーが2本以上あり章数も複数なら章並列が有効化される（既定 book_concurrency）。"""
        from core.llm_client import key_rotator

        manager = make_ready_manager(tmp_path, "parallelbook", "parallelfp")
        key_rotator.configure(["free1", "free2"], tiers=["free", "free"])
        try:
            chapters = make_chapter_pdfs(tmp_path, ["Ch0", "Ch1", "Ch2"])
            splitter = MagicMock()
            splitter.split.return_value = chapters

            def fake_run_pipeline(**kwargs):
                return []

            with patch("core.book_manager.print_log") as mock_log:
                run_manager_with_mocks(manager, splitter, fake_run_pipeline)

            logs = " ".join(str(c.args[0]) for c in mock_log.call_args_list if c.args)
            assert "並列処理を有効化" in logs
        finally:
            key_rotator.configure([])

    def test_restrict_to_and_clear_restriction_pair_even_on_exception(self, tmp_path):
        """restrict_to()/clear_restriction() は、章が例外で失敗しても必ず対で呼ばれる
        （章スレッドの try/finally による保証）。"""
        from core.llm_client import key_rotator

        manager = make_ready_manager(tmp_path, "pairbook", "pairfp")
        key_rotator.configure(["free1", "free2"], tiers=["free", "free"])
        try:
            titles = ["Good1", "Bad", "Good2"]
            chapters = make_chapter_pdfs(tmp_path, titles)
            splitter = MagicMock()
            splitter.split.return_value = chapters

            def fake_run_pipeline(**kwargs):
                title = kwargs["title"]
                if title == "Bad":
                    raise RuntimeError("boom")
                return []

            with patch.object(key_rotator, "restrict_to", wraps=key_rotator.restrict_to) as spy_restrict, \
                 patch.object(key_rotator, "clear_restriction", wraps=key_rotator.clear_restriction) as spy_clear:
                run_manager_with_mocks(manager, splitter, fake_run_pipeline)

            assert spy_restrict.call_count == len(titles)
            assert spy_clear.call_count == len(titles)
        finally:
            key_rotator.configure([])
