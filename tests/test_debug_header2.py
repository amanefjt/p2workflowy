from core.pdf_ingester import remove_inline_running_headers

def test_chapter_title_at_top_of_page():
    # A chapter title at the top of the page with a long subtitle
    # Current implementation uses lowercase guard to identify body text attachment.
    # Capitalized subtitle should be protected.
    text2 = (
        "17 The Ethnographic Effect I This is a very long descriptive subtitle for the chapter that exceeds thirty characters in length.\n"
        "Here is the main text of the chapter."
    )
    keywords = {"The Ethnographic Effect I"}
    res2 = remove_inline_running_headers(text2, keywords)
    print("----- text2 res -----")
    print(res2)
    
    # Assertions to confirm protection
    assert "The Ethnographic Effect I" in res2
    assert "17" in res2
    assert "This is a very long descriptive subtitle" in res2

if __name__ == "__main__":
    test_chapter_title_at_top_of_page()
