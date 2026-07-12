import os
import pytest
from core import book_manager
from core.book_manager import BookManager


class _FakePage:
    def get_text(self):
        return "book body text. "


class _FakeDoc:
    def __iter__(self):
        return iter([_FakePage(), _FakePage()])
    def close(self):
        pass


@pytest.fixture(autouse=True)
def _restore_env():
    original = os.environ.get("DEFAULT_MODEL_RESUME")
    yield
    if original is None:
        os.environ.pop("DEFAULT_MODEL_RESUME", None)
    else:
        os.environ["DEFAULT_MODEL_RESUME"] = original


def test_global_resume_uses_resume_routing(monkeypatch, tmp_path):
    """書籍全体レジュメは resume ルーティング（DEFAULT_MODEL_RESUME）で生成される。"""
    os.environ["DEFAULT_MODEL_RESUME"] = "gemini-3.5-flash"
    monkeypatch.setattr(book_manager.fitz, "open", lambda p: _FakeDoc())

    captured = []
    def fake_call_gemini(prompt, **kw):
        captured.append(kw.get("model"))
        # 2 回目（用語集）は JSON を返す
        return "[]" if kw.get("response_mime_type") == "application/json" else "RESUME"
    monkeypatch.setattr(book_manager, "call_gemini", fake_call_gemini)

    dummy_pdf = tmp_path / "book.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4 dummy")
    bm = BookManager(input_path=str(dummy_pdf), api_key="k", model=None)
    bm.session_dir = tmp_path  # 実 state/ ではなく tmp に書かせる

    bm._generate_global_context()

    # 1 回目の呼び出し（全体レジュメ）が resume ルーティング先モデル
    assert captured[0] == "gemini-3.5-flash"
