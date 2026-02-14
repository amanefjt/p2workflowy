from src.utils import Utils

def test_new_sanitization():
    print("--- Testing New Sanitization Logic ---")
    
    # 1. Japanese removal and Meta-commentary removal
    dirty_md = """# Chapter 1
## Introduction
ご提示いただいたテキストに基づき、構造化を行いました。
This is the first paragraph.
## Introduction
This is a duplicate introduction which should be removed if it's far down.
## Methods
This is the methods section.
## Methods
This is a duplicate Methods section.
制約事項に基づいて、参考文献は除外しました。
Markdown format is maintained.
"""
    # 31 lines to trigger introduction removal
    dirty_md_long = "# Title\n" + "\n".join([f"Line {i}" for i in range(40)]) + "\n## Introduction\nShould be removed."
    
    print("\n[Input with Japanese and Duplicates]")
    print(dirty_md)
    
    sanitized = Utils.sanitize_structured_output(dirty_md)
    print("\n[Output]")
    print(sanitized)
    
    # Assertions
    assert "ご提示いただいた" not in sanitized
    assert "制約事項に基づいて" not in sanitized
    assert sanitized.count("## Methods") == 1
    
    print("\n[Input with Mid-text Introduction]")
    sanitized_long = Utils.sanitize_structured_output(dirty_md_long)
    assert "## Introduction" not in sanitized_long
    print("✓ Mid-text Introduction removed.")

    print("\nALL NEW SANITIZATION TESTS PASSED!")

if __name__ == "__main__":
    test_new_sanitization()
