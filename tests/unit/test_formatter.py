import pytest
from core.engine.p1_ingest.formatter import Formatter
from core.models import RawChunk

def test_smart_unwrap_basic_flow():
    """基本的な段落結合と文末分割のテスト"""
    elements = [
        {"id": 1, "text": "This is a sentence", "page_idx": 0, "font_size": 10.0, "is_bold": False},
        {"id": 2, "text": "end.", "page_idx": 0, "font_size": 10.0, "is_bold": False},
        {"id": 3, "text": "New sentence starts", "page_idx": 0, "font_size": 10.0, "is_bold": False},
        {"id": 4, "text": "here.", "page_idx": 0, "font_size": 10.0, "is_bold": False},
    ]
    
    chunks = Formatter.smart_unwrap(elements)
    
    # 期待される結果: 2つのチャンク
    # 1. "This is a sentence end."
    # 2. "New sentence starts here."
    assert len(chunks) == 2
    assert chunks[0].text == "This is a sentence end."
    assert chunks[1].text == "New sentence starts here."

def test_smart_unwrap_hyphenation():
    """ハイフネーション除去のテスト"""
    elements = [
        {"id": 1, "text": "This is a hyphen-", "page_idx": 0, "font_size": 10.0, "is_bold": False},
        {"id": 2, "text": "ated word.", "page_idx": 0, "font_size": 10.0, "is_bold": False},
    ]
    
    chunks = Formatter.smart_unwrap(elements)
    assert len(chunks) == 1
    # 結合時にスペースが入らないことを確認
    assert chunks[0].text == "This is a hyphenated word."

def test_smart_unwrap_trailing_words():
    """強制結合単語 (the, a, of 等) のテスト"""
    elements = [
        {"id": 1, "text": "The results of the", "page_idx": 0, "font_size": 10.0, "is_bold": False},
        {"id": 2, "text": "experiment are clear.", "page_idx": 0, "font_size": 10.0, "is_bold": False},
    ]
    
    chunks = Formatter.smart_unwrap(elements)
    assert len(chunks) == 1
    assert chunks[0].text == "The results of the experiment are clear."

def test_smart_unwrap_physical_boundary():
    """物理属性（フォントサイズ・太字）の変化による強制分割のテスト"""
    elements = [
        {"id": 1, "text": "Normal text here.", "page_idx": 0, "font_size": 10.0, "is_bold": False},
        {"id": 2, "text": "Heading Line", "page_idx": 0, "font_size": 14.0, "is_bold": True},
        {"id": 3, "text": "Body text follows.", "page_idx": 0, "font_size": 10.0, "is_bold": False},
    ]
    
    chunks = Formatter.smart_unwrap(elements)
    
    # [!] 規約 01/03 により、フォントサイズ変化や太字の変化は境界となるはず
    assert len(chunks) == 3
    assert chunks[1].text == "Heading Line"
    assert chunks[1].is_bold is True
    assert chunks[1].font_size == 14.0

def test_smart_unwrap_citation_and_superscript():
    """引用ブラケットや上付き文字を含む文末判定のテスト"""
    elements = [
        {"id": 1, "text": "As noted by Smith [1, 2].", "page_idx": 0, "font_size": 10.0, "is_bold": False},
        {"id": 2, "text": "Next paragraph.", "page_idx": 0, "font_size": 10.0, "is_bold": False},
    ]
    
    chunks = Formatter.smart_unwrap(elements)
    assert len(chunks) == 2
    assert chunks[0].text == "As noted by Smith [1, 2]."
