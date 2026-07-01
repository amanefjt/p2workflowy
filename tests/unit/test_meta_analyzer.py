import pytest
from unittest.mock import patch, MagicMock
from core.engine.meta_analyzer import MetaAnalyzer
from core.models import RawChunk

@pytest.fixture
def mock_page1_chunks():
    return [
        RawChunk(id="0", text="A Deep Dive into Anthropology", page_idx=0, font_size=24.0, is_bold=True, seq_index=0.0),
        RawChunk(id="1", text="Jane Doe and John Smith", page_idx=0, font_size=12.0, is_bold=False, seq_index=1.0),
        RawChunk(id="2", text="Abstract", page_idx=0, font_size=14.0, is_bold=True, seq_index=2.0),
        RawChunk(id="3", text="This paper explores the nuances of human culture through a lens of...", page_idx=0, font_size=10.0, is_bold=False, seq_index=3.0),
        RawChunk(id="4", text="Keywords: Anthropology, Culture, Human", page_idx=0, font_size=10.0, is_bold=False, seq_index=4.0),
        RawChunk(id="5", text="1. Introduction", page_idx=0, font_size=14.0, is_bold=True, seq_index=5.0),
    ]

@patch("core.engine.meta_analyzer.call_gemini")
def test_analyze_dna_basic(mock_call, mock_page1_chunks):
    """LLM の応答に基づいた DNA 抽出の基本テスト"""
    # 期待される LLM の応答 (JSON)
    mock_call.return_value = """
    {
      "title": "A Deep Dive into Anthropology",
      "authors": ["Jane Doe", "John Smith"],
      "abstract": {
        "start_id": "2",
        "end_id": "3",
        "text_preview": "This paper explores..."
      },
      "keywords": {
        "id": "4",
        "text": "Anthropology, Culture, Human"
      },
      "intro_pre_heading": {
        "start_id": "",
        "end_id": "",
        "text_preview": ""
      }
    }
    """
    
    analyzer = MetaAnalyzer()
    dna = analyzer.analyze_dna(mock_page1_chunks)
    
    assert dna["title"] == "A Deep Dive into Anthropology"
    assert "Jane Doe" in dna["authors"]
    assert dna["abstract"]["start_id"] == "2"
    assert dna["keywords"]["id"] == "4"

@patch("core.engine.meta_analyzer.call_gemini")
def test_analyze_dna_error_handling(mock_call, mock_page1_chunks):
    """LLM が壊れた JSON を返した場合、例外を投げずフォールバック DNA が返されること。"""
    mock_call.return_value = "Invalid JSON response"

    analyzer = MetaAnalyzer()
    dna = analyzer.analyze_dna(mock_page1_chunks)

    # パイプライン継続優先のため例外は投げず、フォールバック値で戻る
    assert dna["title"] == "Unknown Title"
    assert dna["authors"] == []


@patch("core.engine.meta_analyzer.call_gemini")
def test_analyze_dna_null_optional_fields(mock_call, mock_page1_chunks):
    """プロンプト仕様上 LLM は欠落項目に null を返してよい。呼び出し側が
    dna.get('authors', []) のような形で安全に扱えるよう、null は型に応じた
    空値（[] / {}）へ正規化されなければならない。"""
    mock_call.return_value = """
    {
      "title": "A Deep Dive into Anthropology",
      "authors": null,
      "abstract": null,
      "keywords": null,
      "intro_pre_heading": null
    }
    """

    analyzer = MetaAnalyzer()
    dna = analyzer.analyze_dna(mock_page1_chunks)

    assert dna["authors"] == []
    assert dna["abstract"] == {}
    assert dna["keywords"] == {}
    assert dna["intro_pre_heading"] == {}
