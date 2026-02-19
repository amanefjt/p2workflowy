import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from src.book_processor import map_book_toc
from src.skills import PaperProcessorSkills
from src.constants import EXCLUDE_SECTION_KEYWORDS

@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Google API Keyをモック"""
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy_key")

@pytest.mark.asyncio
async def test_map_book_toc_filtering():
    """map_book_toc が除外キーワードを含む章をフィルタリングすることを確認"""
    mock_llm = MagicMock()
    # モックの応答: 正常な章と、除外すべき章（Acknowledgements）を含む
    mock_response = """
    [
        {"chapter_title": "Introduction", "anchor_text": "This is intro."},
        {"chapter_title": "Chapter 1", "anchor_text": "This is ch1."},
        {"chapter_title": "Acknowledgements", "anchor_text": "Thanks to everyone."},
        {"chapter_title": "Notes on Contributors", "anchor_text": "Adam is a ..."}
    ]
    """
    mock_llm.call_api = MagicMock(return_value=mock_response)

    # 実行
    mappings = await map_book_toc(mock_llm, "dummy text")

    # 検証: 除外キーワードが含まれる章は消えているはず
    titles = [m["chapter_title"] for m in mappings]
    assert "Introduction" in titles
    assert "Chapter 1" in titles
    assert "Acknowledgements" not in titles
    assert "Notes on Contributors" not in titles
    assert len(titles) == 2

@pytest.mark.asyncio
async def test_translate_academic_meta_commentary_filtering():
    """translate_academic がメタコメンタリー（翻訳拒否）を空文字として返すことを確認"""
    skills = PaperProcessorSkills()
    skills.llm = MagicMock()
    
    # モックの応答: メタコメンタリー
    mock_response = "申し訳ありませんが、ご提示いただいたテキストは翻訳対象となる本文が含まれていないようです。"
    skills.llm.call_api = MagicMock(return_value=mock_response)

    # チャンクを用意
    chunk = "# Heading\nSome text."
    
    # 実行
    # translate_academic は内部で並列実行するが、1チャンクならそのまま
    # ただし _split_markdown_hierarchically をバイパスするために
    # 内部メソッドをモックするか、あるいは単に短いテキストを渡す
    
    # ここでは _split_markdown_hierarchically がそのままリストを返すようにモックしてもいいが、
    # シンプルに呼び出してみる
    
    # 注: translate_academic は内部で _split_markdown_hierarchically を呼ぶ
    # 短いテキストなら分割されないはず
    
    result = await skills.translate_academic(chunk)
    
    # 検証: 結果は空文字（または改行のみ）になるはず
    assert result.strip() == ""

@pytest.mark.asyncio
async def test_translate_academic_valid_translation():
    """通常の翻訳はそのまま返されることを確認"""
    skills = PaperProcessorSkills()
    skills.llm = MagicMock()
    
    mock_response = "# Heading\nこれは翻訳です。"
    skills.llm.call_api = MagicMock(return_value=mock_response)

    chunk = "# Heading\nThis is translation."
    result = await skills.translate_academic(chunk)
    
    assert "これは翻訳です" in result

@pytest.mark.asyncio
async def test_translate_academic_with_context_guide():
    """context_guide がプロンプトに含まれることを確認"""
    skills = PaperProcessorSkills()
    skills.llm = MagicMock()
    
    start_event = asyncio.Event()

    # call_api の引数をキャプチャ
    async def mock_call_api(prompt, callback=None):
        # プロンプト内に context_guide の内容が含まれているかチェック
        assert "This is context guide" in prompt
        # Target Text が先頭に来ているか簡易チェック（[Target Text] ... [Context] の順）
        # ただし辞書の順序は保証されないが、プロンプト文字列内の出現順序をチェック
        target_idx = prompt.find("[Target Text]")
        context_idx = prompt.find("[Context: Summary")
        # 見つからない場合は -1 になるので注意
        assert target_idx != -1
        # [Target Text] が [Context] より前にあることを期待
        if context_idx != -1:
            assert target_idx < context_idx
        
        return "Translated with context"

    # asyncio.to_thread は第1引数が関数、第2引数以降がその引数
    # skills.py: await asyncio.to_thread(self.llm.call_api, prompt_text, inner_cb)
    # なので、self.llm.call_api を置き換える必要があるが、
    # asyncio.to_thread は関数を別スレッドで実行する。
    # モック関数をそのまま渡しても動くはず。
    
    # ただし MagicMock は同期関数として振る舞うので、side_effect に async 関数を設定できない（to_threadは同期関数を期待する）
    # ここでは side_effect に同期関数を設定する
    
    def sync_mock_call_api(prompt, callback=None):
        assert "This is context guide" in prompt
        # 順序チェック
        target_idx = prompt.find("[Target Text]")
        context_idx = prompt.find("[Context: Summary")
        assert target_idx != -1
        assert target_idx < context_idx
        return "Translated with context"

    skills.llm.call_api = MagicMock(side_effect=sync_mock_call_api)

    chunk = "# Heading\nText to translate."
    await skills.translate_academic(chunk, context_guide="This is context guide")


