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
