
from unittest.mock import MagicMock
from src.skills import PaperProcessorSkills, MAX_TRANSLATION_CHUNK_SIZE

# APIキーなしでテストできるように __init__ を無効化
PaperProcessorSkills.__init__ = lambda self: None

def test_split_logic():
    skills = PaperProcessorSkills()
    
    print("--- Test 1: Anchor Text Method (Plan A - Title + Snippet) ---")
    
    # Simulate a realistic book structure with a large gap between TOC and Body
    toc_section = """
    Index
    Chapter 1: Introduction ... 5
    Chapter 2: Methods ... 10
    """
    
    # Filler text to ensure TOC and Body are far apart (> 200 chars)
    filler = "x" * 500 
    
    body_section_1 = """
    Page 5
    Chapter 1: Introduction
    
    In recent years, the rapid development of AI...
    """
    
    body_section_2 = """
    Page 10
    Chapter 2: Methods
    
    In this study, we employed...
    """
    
    full_text = f"{toc_section}\n{filler}\n{body_section_1}\n{filler}\n{body_section_2}"
    
    structure_data = {
        "chapters": [
            {
                "title": "Chapter 1: Introduction",
                "start_snippet": "In recent years, the rapid development"
            },
            {
                "title": "Chapter 2: Methods", 
                "start_snippet": "In this study, we employed"
            }
        ]
    }
    
    chunks = skills.split_by_anchors(full_text, structure_data)
    
    print(f"Chunks found: {len(chunks)}")
    for i, chunk in enumerate(chunks):
        print(f"Chunk {i} Title: {chunk['title']}")
        print(f"Chunk {i} Start: {chunk['text'][:50]}...")
    
    assert len(chunks) == 2
    assert "Chapter 1: Introduction" in chunks[0]["title"]
    # The text should start around "Page 5..." or "Chapter 1..."
    # It should definitely NOT be the TOC entry
    assert "In recent years" in chunks[0]["text"]
    assert "Index" not in chunks[0]["text"]
    
    assert "Chapter 2: Methods" in chunks[1]["title"]
    assert "In this study" in chunks[1]["text"]
    print("Test 1 Passed: Plan A (Title + Snippet) worked with gap.")


    print("\n--- Test 2: Anchor Text Method (Plan B - Snippet Only) ---")
    # Title is missing or malformed in text, but snippet exists
    full_text_b = """
    Unknown Title
    The concept of gravity is fundamental...
    
    Another Section
    Quantum mechanics posits that...
    """
    
    structure_data_b = {
        "chapters": [
            {
                "title": "Gravity",
                "start_snippet": "The concept of gravity is fundamental"
            },
            {
                "title": "Quantum",
                "start_snippet": "Quantum mechanics posits that"
            }
        ]
    }
    
    chunks_b = skills.split_by_anchors(full_text_b, structure_data_b)
    assert len(chunks_b) == 2
    assert "The concept of gravity" in chunks_b[0]["text"]
    assert "Quantum mechanics" in chunks_b[1]["text"]
    print("Test 2 Passed: Plan B (Snippet Only) worked.")


    print("\n--- Test 3: Anchor Text Method (Plan C - Title Only) ---")
    # Snippet is wrong/missing in text, but title matches
    full_text_c = """
    Chapter 3: Results
    The data shows a significant increase...
    """
    
    structure_data_c = {
        "chapters": [
            {
                "title": "Chapter 3: Results",
                "start_snippet": "Wrong snippet text that does not exist"
            }
        ]
    }
    
    chunks_c = skills.split_by_anchors(full_text_c, structure_data_c)
    assert len(chunks_c) == 1
    assert "Chapter 3: Results" in chunks_c[0]["text"]
    print("Test 3 Passed: Plan C (Title Only) worked.")

    print("\n--- Test 4: Running Head Cleaning (Regex) ---")
    dirty_text = """
    Chapter 1
    123
    Content starts here.
    """
    cleaned = skills._clean_running_heads(dirty_text)
    # The "123" line should be removed (replaced by empty string)
    assert "123" not in cleaned
    assert "Content starts here" in cleaned
    print("Test 4 Passed: Running head cleaning.")

if __name__ == "__main__":
    try:
        test_split_logic()
        print("\nALL TESTS PASSED")
    except Exception as e:
        print(f"\nTEST FAILED: {e}")
        import traceback
        traceback.print_exc()
