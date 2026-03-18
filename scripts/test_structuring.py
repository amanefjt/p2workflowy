import sys
import os
from typing import List

# Mocking the environment for testing
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.models import RawChunk
from core.phase3_structure import structure_nodes_by_markdown

def test_toc_validation():
    print("Running test_toc_validation...")
    
    # TOC exists in the list
    toc_list = ["Chapter 8 Fragmentation", "The New Modernities"]
    
    chunks = [
        RawChunk(id="1", text="# Chapter 8 Fragmentation", seq_index=1.0),
        RawChunk(id="2", text="This is some intro text.", seq_index=2.0),
        RawChunk(id="3", text="# BORROWING CATEGORIES", seq_index=3.0), # Not in TOC -> Should be demoted
        RawChunk(id="4", text="This should be a section under chapter 8.", seq_index=4.0),
        RawChunk(id="5", text="# The New Modernities", seq_index=5.0), # In TOC -> Should be Chapter
        RawChunk(id="6", text="Some content.", seq_index=6.0),
    ]
    
    # Run with is_book=True and toc_list
    tree, sections_dict = structure_nodes_by_markdown(chunks, is_book=True, toc_list=toc_list)
    
    # Validation
    assert len(tree) == 2, f"Expected 2 top-level chapters, got {len(tree)}"
    
    ch8 = tree[0]
    assert ch8.text == "Chapter 8 Fragmentation"
    assert ch8.role == "h2"
    
    # Check demotion of BORROWING CATEGORIES
    bor_cat = next((c for c in ch8.children if "BORROWING" in c.text), None)
    assert bor_cat is not None, "BORROWING CATEGORIES should be a child of Chapter 8"
    assert bor_cat.role == "h3", f"Expected BORROWING CATEGORIES to be h3, got {bor_cat.role}"
    
    ch9 = tree[1]
    assert "Modernities" in ch9.text
    assert ch9.role == "h2", "The New Modernities should be h2 because it matches TOC"

    print("Test passed: TOC validation works correctly!")

if __name__ == "__main__":
    try:
        test_toc_validation()
    except AssertionError as e:
        print(f"Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"An error occurred during testing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
