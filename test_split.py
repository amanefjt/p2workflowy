
from unittest.mock import MagicMock
from src.skills import PaperProcessorSkills, MAX_TRANSLATION_CHUNK_SIZE

# APIキーなしでテストできるように __init__ を無効化
PaperProcessorSkills.__init__ = lambda self: None

def test_split_logic():
    skills = PaperProcessorSkills()
    
    # テストケース1: 短いテキスト（分割されないこと）
    short_text = "Short text.\nSecond line."
    chunks = skills._split_text_by_length(short_text, max_length=100)
    assert len(chunks) == 1
    assert chunks[0] == short_text
    print("Test 1 Passed: No split for short text")

    # テストケース2: 指定長を超えるテキスト（分割されること）
    # 10文字制限でテスト
    # "12345" (5) + \n (1) + "67890" (5) = 11文字 > 10 -> 分割されるはず？
    # line1: "12345" (len 6 with newline)
    # line2: "67890" (len 5)
    # total 11
    
    line1 = "12345"
    line2 = "67890"
    text = f"{line1}\n{line2}"
    
    # max_length=10
    # loop 1: "12345" (6) -> current=6, chunk=["12345"]
    # loop 2: "67890" (6) -> 6+6=12 > 10 -> split! 
    # expected: ["12345", "67890"]
    
    chunks = skills._split_text_by_length(text, max_length=10)
    assert len(chunks) == 2
    assert chunks[0] == "12345"
    assert chunks[1] == "67890"
    print("Test 2 Passed: Split by length")
    
    # テストケース3: 見出し分割 + 長文分割の組み合わせ
    # Heading 1 (short)
    # Heading 2 (long body)
    
    # Make a dummy long text
    long_body = "A" * 5000 # 5000 chars
    text_with_headers = f"# Header 1\nShort body\n# Header 2\n{long_body}"
    
    # Default MAX is 4000
    # Expected: 
    # Chunk 1: "# Header 1\nShort body"
    # Chunk 2: "# Header 2\n" + part of long body
    # Chunk 3: rest of long body
    
    # 注意: _split_text_by_length は改行単位でしか分割しないので、
    # "A"*5000 のように改行がない長い文字列は分割されない（仕様通り）。
    # テストのために改行を入れる。
    long_body_lines = ("A" * 100 + "\n") * 50 # 101 * 50 = 5050 chars
    text_with_headers = f"# Header 1\nShort body\n# Header 2\n{long_body_lines}"
    
    chunks = skills._split_markdown_by_headers(text_with_headers)
    
    print(f"Total chunks: {len(chunks)}")
    for i, c in enumerate(chunks):
        print(f"Chunk {i} length: {len(c)}")
        assert len(c) <= MAX_TRANSLATION_CHUNK_SIZE + 200 # マージン（行単位なので多少超えることはある）
        
    assert len(chunks) >= 3 # At least 3 chunks expected
    print("Test 3 Passed: Combined split logic")

if __name__ == "__main__":
    try:
        test_split_logic()
        print("ALL TESTS PASSED")
    except Exception as e:
        print(f"TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
