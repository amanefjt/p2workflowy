from core.pdf_ingester import remove_inline_running_headers

def test_header_with_blank_lines():
    #Case 1: Attached header after blank lines (Should be removed if lowercase)
    text1 = "\n\nThe Ethnographic Effect I 17\ncontent starts here"
    keywords = {"The Ethnographic Effect I"}
    res1 = remove_inline_running_headers(text1, keywords)
    print("Result 1:", repr(res1))
    assert "The Ethnographic Effect I 17" not in res1
    assert "content starts here" in res1

    #Case 2: Independent header line (Should be removed)
    text2 = "\n\nThe Ethnographic Effect I 17\nContent starts here"
    res2 = remove_inline_running_headers(text2, keywords)
    print("Result 2:", repr(res2))
    assert "The Ethnographic Effect I 17" not in res2
    # independent header removed, "Content" (capitalized) should be preserved
    assert "Content starts here" in res2

    #Case 3: Attached header with text (Current Implementation Guard: Cap protection)
    text3 = "17 The Ethnographic Effect I This is a Subtitle"
    res3 = remove_inline_running_headers(text3, keywords)
    print("Result 3:", repr(res3))
    assert "The Ethnographic Effect I" in res3 # Should be preserved (Subtitle)

if __name__ == "__main__":
    test_header_with_blank_lines()
