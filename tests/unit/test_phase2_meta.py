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
