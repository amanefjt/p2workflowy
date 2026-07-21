"""
Phase 2 generate_resume のプロンプト選択・注入ロジックのテスト。
書籍分岐が CHAPTER_SUMMARY_PROMPT を使い、book_resume を <book_context> に注入すること、
論文分岐が SUMMARY_PROMPT_ronbun を必須参照すること、メトリクスの section 値を確認する。
"""


def test_generate_resume_book_mode_uses_chapter_prompt(monkeypatch):
    import core.phase2_meta as p2

    captured = {}
    def fake_call_gemini(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["metrics"] = kwargs.get("metrics_metadata")
        return "dummy resume"
    monkeypatch.setattr(p2, "call_gemini", fake_call_gemini)

    p2.generate_resume("CHAPTER_TEXT", is_book=True, resume_context="BOOK_RESUME")
    assert "書籍の一章" in captured["prompt"]          # CHAPTER_SUMMARY_PROMPT を使用
    assert "BOOK_RESUME" in captured["prompt"]          # book_context に注入
    assert "CHAPTER_TEXT" in captured["prompt"]
    assert captured["metrics"] == {"section": "chapter_resume"}


def test_generate_resume_book_mode_without_book_context(monkeypatch):
    import core.phase2_meta as p2
    captured = {}
    monkeypatch.setattr(p2, "call_gemini",
                        lambda prompt, **kw: captured.update(prompt=prompt) or "r")
    p2.generate_resume("CHAPTER_TEXT", is_book=True, resume_context=None)
    assert "<book_context>\nなし\n</book_context>" in captured["prompt"]


def test_generate_resume_paper_mode_metrics(monkeypatch):
    import core.phase2_meta as p2
    captured = {}
    def fake_call_gemini(prompt, **kwargs):
        captured["metrics"] = kwargs.get("metrics_metadata")
        return "r"
    monkeypatch.setattr(p2, "call_gemini", fake_call_gemini)
    p2.generate_resume("PAPER_TEXT", is_book=False)
    assert captured["metrics"] == {"section": "paper_resume"}


def test_generate_resume_falls_back_to_default_model_when_text_too_large(monkeypatch):
    """RESUME_MODEL_SAFE_CHAR_LIMIT 超・model 未指定なら resume モデルではなく既定モデルへ
    フォールバックする。gemini-3.5-flash の実効入力上限超過（I-20）を、論文・章単位の
    レジュメ生成でも book_manager.py と同じ考え方で回避する（2026-07-21 レビュー指摘）。"""
    import core.phase2_meta as p2
    captured = {}
    monkeypatch.setattr(p2, "call_gemini",
                        lambda prompt, **kw: captured.update(model=kw.get("model")) or "r")
    monkeypatch.setattr(p2, "get_default_model", lambda purpose="default": f"model-for-{purpose}")

    big_text = "x" * (p2.RESUME_MODEL_SAFE_CHAR_LIMIT + 1)
    p2.generate_resume(big_text, is_book=False)
    assert captured["model"] == "model-for-default"


def test_generate_resume_uses_resume_model_under_safe_limit(monkeypatch):
    import core.phase2_meta as p2
    captured = {}
    monkeypatch.setattr(p2, "call_gemini",
                        lambda prompt, **kw: captured.update(model=kw.get("model")) or "r")
    monkeypatch.setattr(p2, "get_default_model", lambda purpose="default": f"model-for-{purpose}")

    small_text = "x" * 100
    p2.generate_resume(small_text, is_book=False)
    assert captured["model"] == "model-for-resume"


def test_generate_resume_respects_explicit_model_even_over_safe_limit(monkeypatch):
    """--model 明示指定時はユーザーの選択を尊重しガードを適用しない
    （core/book_manager.py の RESUME_MODEL_SAFE_CHAR_LIMIT と同じ設計判断、I-20 踏襲）。"""
    import core.phase2_meta as p2
    captured = {}
    monkeypatch.setattr(p2, "call_gemini",
                        lambda prompt, **kw: captured.update(model=kw.get("model")) or "r")

    big_text = "x" * (p2.RESUME_MODEL_SAFE_CHAR_LIMIT + 1)
    p2.generate_resume(big_text, is_book=False, model="user-chosen-model")
    assert captured["model"] == "user-chosen-model"
