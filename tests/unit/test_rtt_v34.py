"""
translate_batch のユニットテスト（旧 test_rtt_v34.py）

テスト内容:
  - 末尾切断: レスポンスが途中で切れても解析済みチャンクは返る
  - 中間閉じタグ欠落: 次の開始タグが境界になりコンテンツが混入しない
  - 大文字小文字: IGNORECASE で大文字タグをパース
  - 不明 ID 無視: known_ids 外のタグはスキップ
  - ティアダウングレード: downgrade() が FREE ティアに切り替える
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.llm_client import translate_batch, tier_manager, GeminiTier, reset_pipeline_state


def _make_limiter():
    m = MagicMock()
    m.__aenter__ = AsyncMock(return_value=None)
    m.__aexit__ = AsyncMock(return_value=False)
    return m


COMMON = dict(
    glossary_content="",
    previous_translation="",
    prompt_template="{expertise}{context_guide}{glossary_content}{resume_content}{previous_translation}{chunk_json}",
    resume_content="",
    section_name="sec",
    max_parse_retries=0,
)


@pytest.mark.asyncio
async def test_truncated_response_partial_recovery():
    """末尾切断: chunk_1 は正常抽出、chunk_2 は開始タグのみで中身なし → 翻訳失敗マーカー。"""
    response = (
        "<p2w_chunk_1>\n翻訳1\n</p2w_chunk_1>\n"
        "<p2w_chunk_2>"  # 中身なしで切れている
    )
    chunks = [
        {"id": "1", "text": "Text 1", "seq_index": 0.0},
        {"id": "2", "text": "Text 2", "seq_index": 1.0},
    ]
    with patch("core.llm_client.call_gemini_async", new=AsyncMock(return_value=response)):
        results = await translate_batch(chunks=chunks, rate_limiter=_make_limiter(), **COMMON)

    assert results[0].text == "翻訳1"
    assert "【翻訳失敗】" in results[1].text


@pytest.mark.asyncio
async def test_missing_closing_tag_no_bleed():
    """中間閉じタグ欠落: chunk_1 の閉じタグがなくても chunk_2 のコンテンツに混入しない。"""
    response = (
        "<p2w_chunk_1>\n翻訳1\n"            # 閉じタグなし
        "<p2w_chunk_2>\n翻訳2\n</p2w_chunk_2>\n"
    )
    chunks = [
        {"id": "1", "text": "T1", "seq_index": 0.0},
        {"id": "2", "text": "T2", "seq_index": 1.0},
    ]
    with patch("core.llm_client.call_gemini_async", new=AsyncMock(return_value=response)):
        results = await translate_batch(chunks=chunks, rate_limiter=_make_limiter(), **COMMON)

    assert results[0].text == "翻訳1"
    assert results[1].text == "翻訳2"


@pytest.mark.asyncio
async def test_uppercase_tags_parsed():
    """大文字小文字: IGNORECASE により大文字タグも正常にパースされる。"""
    response = "<P2W_CHUNK_1>\n翻訳1\n</P2W_CHUNK_1>\n"
    chunks = [{"id": "1", "text": "Text 1", "seq_index": 0.0}]
    with patch("core.llm_client.call_gemini_async", new=AsyncMock(return_value=response)):
        results = await translate_batch(chunks=chunks, rate_limiter=_make_limiter(), **COMMON)

    assert results[0].text == "翻訳1"


@pytest.mark.asyncio
async def test_unknown_id_ignored():
    """不明 ID 無視: known_ids にない ID のタグはスキップされ、対象チャンクに影響しない。"""
    response = (
        "<p2w_chunk_999>\nゴーストタグ\n</p2w_chunk_999>\n"
        "<p2w_chunk_1>\n翻訳1\n</p2w_chunk_1>\n"
    )
    chunks = [{"id": "1", "text": "Text 1", "seq_index": 0.0}]
    with patch("core.llm_client.call_gemini_async", new=AsyncMock(return_value=response)):
        results = await translate_batch(chunks=chunks, rate_limiter=_make_limiter(), **COMMON)

    assert len(results) == 1
    assert results[0].text == "翻訳1"
    assert "ゴーストタグ" not in results[0].text


def test_tier_downgrade_switches_to_free():
    """ティアダウングレード: downgrade() が FREE ティアに切り替え was_downgraded フラグを立てる。"""
    reset_pipeline_state()
    tier_manager.set_tier(GeminiTier.PAID)

    assert tier_manager.current_tier == GeminiTier.PAID

    tier_manager.downgrade()

    assert tier_manager.current_tier == GeminiTier.FREE
    assert tier_manager.was_downgraded is True
