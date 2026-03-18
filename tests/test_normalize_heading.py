from core.phase3_structure import normalize_heading

def test_normalize_variations():
    # Standard Chapter (Now preserves prefixes and spaces)
    assert normalize_heading("Chapter 1: Introduction") == "chapter 1 introduction"
    assert normalize_heading("1. Introduction") == "1 introduction"
    
    # Roman Numerals
    assert normalize_heading("III. Methods") == "iii methods"
    assert normalize_heading("Chapter III. Results") == "chapter iii results"
    
    # Part and Section
    assert normalize_heading("Part I: The Beginning") == "part i the beginning"
    assert normalize_heading("Section 2.1: Analysis") == "section 2 1 analysis"
    assert normalize_heading("Appendix A: Data") == "appendix a data"
    
    # Lowercase variations
    assert normalize_heading("chapter 1 introduction") == "chapter 1 introduction"
    assert normalize_heading("part ii results") == "part ii results"
    
    # No prefix
    assert normalize_heading("Discussion") == "discussion"
    
    # Complex numbering
    assert normalize_heading("1.1.2. Specific Details") == "1 1 2 specific details"
    
    # Regression check for BUG-002: Ensure "Introduction" isn't cut
    assert normalize_heading("Introduction") == "introduction"
    assert normalize_heading("I. Introduction") == "i introduction"

if __name__ == "__main__":
    test_normalize_variations()
    print("All normalize_heading tests passed!")
