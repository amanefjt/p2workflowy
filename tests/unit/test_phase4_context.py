from core.phase4_translate import build_translation_context


def test_paper_mode_uses_document_resume():
    ctx = build_translation_context("", "PAPER_RESUME", is_book=False)
    assert ctx == "PAPER_RESUME"

def test_paper_mode_empty_resume():
    assert build_translation_context("", "", is_book=False) == ""

def test_book_mode_combines_both():
    ctx = build_translation_context("BOOK", "CHAPTER", is_book=True)
    assert "【書籍全体の要約】" in ctx and "BOOK" in ctx
    assert "【この章の要約】" in ctx and "CHAPTER" in ctx
    assert ctx.index("BOOK") < ctx.index("CHAPTER")  # 全体→章の順

def test_book_mode_without_book_resume():
    ctx = build_translation_context("", "CHAPTER", is_book=True)
    assert "BOOK" not in ctx and "CHAPTER" in ctx
