from src.utils import Utils

def test_sanitize_structured_output():
    # 案件 1: 複数の H1 がある場合
    markdown_text = """# Great Paper Title
Abstract content.

# Great Paper Title
Second section content.

## Introduction
Real introduction.
"""
    cleaned = Utils.sanitize_structured_output(markdown_text)
    print("Test 1 (Redundant H1):")
    print(f"Original H1 count: {markdown_text.count('# ')}")
    print(f"Cleaned H1 count: {cleaned.count('# ')}")
    print(f"Cleaned content:\n{cleaned}")
    assert cleaned.count('# ') == 1

    # 案件 2: 文中（10行目以降）に不自然な ## Introduction がある場合
    markdown_text_with_intro = "# Title\n" + "\n".join([f"Line {i}" for i in range(15)]) + "\n## Introduction\nSuspicious intro text."
    cleaned_intro = Utils.sanitize_structured_output(markdown_text_with_intro)
    print("\nTest 2 (Suspicious mid-text Introduction):")
    print(f"Found '## Introduction' in cleaned: {'## Introduction' in cleaned_intro}")
    assert '## Introduction' not in cleaned_intro

    # 案件 3: 冒頭の ## Introduction は許可する
    markdown_text_start_intro = "# Title\n## Introduction\nGood intro."
    cleaned_start_intro = Utils.sanitize_structured_output(markdown_text_start_intro)
    print("\nTest 3 (Legitimate start Introduction):")
    print(f"Found '## Introduction' in cleaned: {'## Introduction' in cleaned_start_intro}")
    assert '## Introduction' in cleaned_start_intro

if __name__ == "__main__":
    try:
        test_sanitize_structured_output()
        print("\nAll sanitize tests passed!")
    except Exception as e:
        print(f"\nTest failed: {e}")
