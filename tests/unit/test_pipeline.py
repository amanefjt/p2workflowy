"""
core/pipeline.py::run_pipeline の配線を検証するテスト。

is_book=True で run_pipeline を呼んでも、内部の run_phase3(...) 呼び出しに
is_book が渡らなければ Phase 3 の書籍モード分岐（ChapterParser/Route C/Route D）が
一切発火しない（常にペーパーモードとして処理される）という実運用バグが過去にあった。
このバグはユニットテストが run_phase3 を直接 is_book=True で叩いていたため検出できず、
唯一の本番呼び出し元である run_pipeline を経由するテストがなかったことが原因。
このファイルはその再発防止テスト。
"""

from unittest.mock import patch


def test_run_pipeline_forwards_is_book_to_run_phase3(tmp_path, monkeypatch):
    """run_pipeline(is_book=True) は run_phase3(..., is_book=True) を呼ぶ。"""
    monkeypatch.setattr("core.config.STATE_DIR", tmp_path)

    input_path = tmp_path / "chapter.txt"
    input_path.write_text("Chapter body text.", encoding="utf-8")

    captured = {}

    def fake_run_phase3(*args, **kwargs):
        captured.update(kwargs)
        return [], {}

    with patch("core.pipeline.run_phase1_unified", return_value=None), \
         patch("core.pipeline.run_phase2", return_value={}), \
         patch("core.pipeline.run_phase3", side_effect=fake_run_phase3):
        from core.pipeline import run_pipeline
        run_pipeline(
            input_path=str(input_path),
            api_key="dummy",
            is_book=True,
            structure_only=True,
        )

    assert captured.get("is_book") is True


def test_run_pipeline_forwards_is_book_false_for_paper_mode(tmp_path, monkeypatch):
    """is_book=False（デフォルト、論文モード）でも明示的に False が渡ることを確認する。"""
    monkeypatch.setattr("core.config.STATE_DIR", tmp_path)

    input_path = tmp_path / "paper.txt"
    input_path.write_text("Paper body text.", encoding="utf-8")

    captured = {}

    def fake_run_phase3(*args, **kwargs):
        captured.update(kwargs)
        return [], {}

    with patch("core.pipeline.run_phase1_unified", return_value=None), \
         patch("core.pipeline.run_phase2", return_value={}), \
         patch("core.pipeline.run_phase3", side_effect=fake_run_phase3):
        from core.pipeline import run_pipeline
        run_pipeline(
            input_path=str(input_path),
            api_key="dummy",
            structure_only=True,
        )

    assert captured.get("is_book") is False


def test_run_pipeline_forces_full_vlm_for_spread_scan_paper_pdf(tmp_path, monkeypatch):
    """見開きスキャンPDF（is_spread_pdf=True）の論文（非書籍）PDF は pdf_mode 未指定でも
    Route C (full_vlm) を強制する。入力ルーティング優先順位②（見開きスキャン→VLM）は
    BookManager 経由の書籍モードにしか適用されておらず、論文モードでは is_spread_pdf を
    一度も見ていなかった（2026-07-21 レビュー指摘）。"""
    monkeypatch.setattr("core.config.STATE_DIR", tmp_path)
    input_path = tmp_path / "paper.pdf"
    input_path.write_bytes(b"%PDF-1.4 dummy")

    captured = {}

    def fake_run_phase1(*args, **kwargs):
        captured["pdf_mode"] = kwargs.get("pdf_mode")
        return []

    with patch("core.engine.p1_ingest.spread_splitter.is_spread_pdf", return_value=True), \
         patch("core.pipeline.run_phase1_unified", side_effect=fake_run_phase1), \
         patch("core.pipeline.run_phase2", return_value={}), \
         patch("core.pipeline.run_phase3", return_value=([], {})):
        from core.pipeline import run_pipeline
        run_pipeline(
            input_path=str(input_path),
            api_key="dummy",
            structure_only=True,
        )

    assert captured.get("pdf_mode") == "full_vlm"


def test_run_pipeline_skips_spread_check_for_book_chapters(tmp_path, monkeypatch):
    """is_book=True（BookManager が呼ぶ章単位パイプライン）では is_spread_pdf を呼ばない。
    BookManager が書籍全体を既に単一ページへ分割済みの PDF を渡すため、章ごとに再判定する
    必要がない（無駄な呼び出しを避ける）。"""
    monkeypatch.setattr("core.config.STATE_DIR", tmp_path)
    input_path = tmp_path / "chapter.pdf"
    input_path.write_bytes(b"%PDF-1.4 dummy")

    with patch("core.engine.p1_ingest.spread_splitter.is_spread_pdf") as mock_is_spread, \
         patch("core.engine.p1_ingest.pdf_ingester.diagnose_pdf_quality", return_value=True), \
         patch("core.pipeline.run_phase1_unified", return_value=[]), \
         patch("core.pipeline.run_phase2", return_value={}), \
         patch("core.pipeline.run_phase3", return_value=([], {})):
        from core.pipeline import run_pipeline
        run_pipeline(
            input_path=str(input_path),
            api_key="dummy",
            is_book=True,
            structure_only=True,
        )

    assert not mock_is_spread.called
