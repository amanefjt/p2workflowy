import pytest
from core.models import RawChunk
from core.engine.p3_structure.heading_detector import HeadingDetector

@pytest.fixture
def detector():
    return HeadingDetector()

def test_normalize_heading():
    """記号や数字、番号剥離の正規化を検証"""
    # 番号剥離
    assert HeadingDetector.normalize_heading("1. Introduction") == "introduction"
    # 二重番号
    assert HeadingDetector.normalize_heading("2.1 Methods") == "methods"
    # 大文字・小文字、記号
    assert HeadingDetector.normalize_heading("Comparison of Comparisons—1") == "comparison of comparisons 1"

def test_is_heading_by_text_match():
    """既知の見出しリストとのテキスト一致による判定を検証"""
    detector = HeadingDetector(headings=["Introduction", "Methods", "Results"])
    
    # 正常系: 完全一致（正規化後）
    chunk = RawChunk(id="c1", text="1. Introduction", seq_index=1.0)
    is_h, title, remaining = detector.detect(chunk)
    assert is_h is True
    assert title == "Introduction"
    assert remaining == ""

    # 正常系: 連結された本文の分離
    # "Methods" (7 chars) に対して " This paper describes..." が続くケース
    chunk = RawChunk(id="c2", text="Methods This paper describes...", seq_index=2.0)
    is_h, title, remaining = detector.detect(chunk)
    assert is_h is True
    assert title == "Methods"
    assert remaining == "This paper describes..."

def test_is_heading_by_italic_and_small_font():
    """斜体やわずかなフォント差異による学術向け物理判定を検証"""
    detector = HeadingDetector(headings=[], body_font_size=10.0)
    
    # 斜体 (Italic) + 大文字始まり + 短い
    i_chunk = RawChunk(id="i1", text="Key Arguments", seq_index=3.0, font_size=10.0, is_italic=True)
    is_h, title, _ = detector.detect(i_chunk)
    assert is_h is True

    # 1.1倍のフォントサイズ (10.0 -> 11.0)
    s_chunk = RawChunk(id="s1", text="Conclusion", seq_index=4.0, font_size=11.0)
    is_h, _, _ = detector.detect(s_chunk)
    assert is_h is True

def test_is_heading_by_font_family():
    """フォントファミリー（サンセリフ系）による物理判定を検証"""
    detector = HeadingDetector(headings=[], body_font_size=10.0)
    
    # サンセリフ/ゴシック体
    g_chunk = RawChunk(id="g1", text="REFERENCES", seq_index=5.0, font_size=10.0, font_name="Arimo,Bold,Sans-serif")
    is_h, _, _ = detector.detect(g_chunk)
    assert is_h is True

    # セリフ体の本文
    p_chunk = RawChunk(id="p1", text="Introduction is important...", seq_index=6.0, font_size=10.0, font_name="TimesNewRoman")
    is_h, _, _ = detector.detect(p_chunk)
    assert is_h is False
