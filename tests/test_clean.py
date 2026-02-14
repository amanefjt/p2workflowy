from src.utils import Utils

def test_clean_header_noise():
    chapter_title = "Registers of Comparison"
    
    # 1. 完全一致
    text = "Line 1\nRegisters of Comparison\nLine 2"
    cleaned = Utils.clean_header_noise(text, chapter_title)
    print("Test 1 (Exact Match):")
    print(f"Original:\n{text}")
    print(f"Cleaned:\n{cleaned}")
    assert "Registers of Comparison" not in cleaned.splitlines()

    # 2. Levenshtein距離（わずかな違い）
    text = "Line 1\nRegisters of Comparision\nLine 2" # 'i' が入っている
    cleaned = Utils.clean_header_noise(text, chapter_title)
    print("\nTest 2 (Levenshtein):")
    print(f"Original:\n{text}")
    print(f"Cleaned:\n{cleaned}")
    assert "Registers of Comparision" not in cleaned.splitlines()

    # 3. 含有率 80% 以上
    text = "Line 1\nPage 45 Registers of Comparison\nLine 2"
    cleaned = Utils.clean_header_noise(text, chapter_title)
    print("\nTest 3 (80% occupancy):")
    line_lower = "Page 45 Registers of Comparison".lower()
    title_lower = chapter_title.lower()
    clean_title_len = len(title_lower.replace(" ", ""))
    clean_line_len = len(line_lower.replace(" ", ""))
    ratio = clean_title_len / clean_line_len if clean_line_len > 0 else 0
    print(f"Title: '{title_lower}' (len={clean_title_len})")
    print(f"Line:  '{line_lower}' (len={clean_line_len})")
    print(f"Ratio: {ratio:.4f}")
    print(f"Original:\n{text}")
    print(f"Cleaned:\n{cleaned}")
    assert "Page 45 Registers of Comparison" not in cleaned.splitlines()

    # 4. 含有率 80% 未満（本文の一部とみなすべきもの）
    text = "Line 1\nThis chapter is about Registers of Comparison and its implications.\nLine 2"
    cleaned = Utils.clean_header_noise(text, chapter_title)
    print("\nTest 4 (Below 80% - Keep it):")
    print(f"Original:\n{text}")
    print(f"Cleaned:\n{cleaned}")
    assert "This chapter is about Registers of Comparison and its implications." in cleaned.splitlines()

if __name__ == "__main__":
    try:
        test_clean_header_noise()
        print("\nAll tests passed!")
    except Exception as e:
        print(f"\nTest failed: {e}")
