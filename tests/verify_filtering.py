
from src.utils import Utils
from src.constants import EXCLUDE_SECTION_KEYWORDS

def test_extract_structure_from_resume():
    resume = """
# リサーチ・クエスチョン
What is X?
# 核心的主張（Thesis）
X is Y.
# ## Introduction：概要
### 中心的な主張
Intro claim.
### Introductionの論理展開
- Point 1
# ## Abstract：抄録
### 中心的な主張
Abstract claim.
# ## References：参考文献
### 参考文献のリスト
- Ref 1
# ## Notes：注釈
### 注釈の内容
- Note 1
"""
    outline = Utils.extract_structure_from_resume(resume)
    print("--- Outline ---")
    print(outline)
    
    # Assertions
    assert "リサーチ・クエスチョン" not in outline
    assert "核心的主張" not in outline
    assert "中心的な主張" not in outline
    assert "論理展開" not in outline
    assert "## Introduction" in outline
    assert "## Abstract" in outline
    assert "## Notes" in outline
    assert "## References" not in outline

def test_remove_unwanted_sections():
    markdown = """
# Title
## Abstract
This is abstract.
## Introduction
This is intro.
## References
- Ref A
- Ref B
## Notes
These are notes.
## Appendix
Extra info.
"""
    # Simulate EXCLUDE_SECTION_KEYWORDS
    keywords = ["References", "Appendix", "Acknowledgements"]
    
    filtered = Utils.remove_unwanted_sections(markdown, keywords)
    print("\n--- Filtered Markdown ---")
    print(filtered)
    
    assert "## Abstract" in filtered
    assert "## Introduction" in filtered
    assert "## Notes" in filtered
    assert "## References" not in filtered
    assert "## Appendix" not in filtered
    assert "Ref A" not in filtered

if __name__ == "__main__":
    try:
        test_extract_structure_from_resume()
        test_remove_unwanted_sections()
        print("\nVerification successful!")
    except AssertionError as e:
        print(f"\nVerification failed: {e}")
    except Exception as e:
        print(f"\nAn error occurred: {e}")
