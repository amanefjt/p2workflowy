"""
llm_client のユニットテスト

テスト内容:
  - TPS計算: usage_metadata のトークン数が None でも TypeError にならない
  - クライアントキャッシュ: reset_pipeline_state() が _CLIENTS もクリアする
    (genai.Client の非同期トランスポートは生成時のイベントループに紐付くため、
    パイプラインをまたいでキャッシュを使い回すと "Event loop is closed" になる)
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.llm_client import call_gemini, call_gemini_async, reset_pipeline_state, _get_client


class _FakeUsage:
    def __init__(self, prompt_token_count=None, candidates_token_count=None):
        self.prompt_token_count = prompt_token_count
        self.candidates_token_count = candidates_token_count


class _FakeChunk:
    def __init__(self, text, usage_metadata=None):
        self.text = text
        self.usage_metadata = usage_metadata


async def _fake_stream():
    yield _FakeChunk("hello", usage_metadata=_FakeUsage(prompt_token_count=None, candidates_token_count=None))


@pytest.mark.asyncio
async def test_call_gemini_async_handles_none_token_counts():
    """usage_metadata.candidates_token_count が None でも TPS 計算で例外にならない。"""
    fake_client = MagicMock()
    fake_client.aio.models.generate_content_stream = AsyncMock(return_value=_fake_stream())

    with patch("core.llm_client._get_client", return_value=fake_client):
        result = await call_gemini_async("prompt", max_retries=1)

    assert result == "hello"


def test_call_gemini_raises_on_empty_text_response():
    """sync: チャンクは届くが text が空（finish_reason=MALFORMED_RESPONSE / MAX_TOKENS で
    0 トークン）の場合、無言で "" を返さず RuntimeError を送出する（リトライ枯渇後）。"""
    fake_client = MagicMock()
    empty_chunk = _FakeChunk("", usage_metadata=_FakeUsage(prompt_token_count=100, candidates_token_count=0))
    fake_client.models.generate_content_stream = MagicMock(side_effect=lambda **k: iter([empty_chunk]))

    with patch("core.llm_client._get_client", return_value=fake_client):
        with pytest.raises(RuntimeError):
            call_gemini("prompt", model="gemini-3.1-flash-lite", max_retries=1)


def test_call_gemini_returns_text_on_normal_response():
    """sync: 通常の非空レスポンスはそのまま返す（空チェック拡張が正常系を壊さない）。"""
    fake_client = MagicMock()
    fake_client.models.generate_content_stream = MagicMock(
        side_effect=lambda **k: iter([_FakeChunk("hello", _FakeUsage(10, 5))])
    )

    with patch("core.llm_client._get_client", return_value=fake_client):
        assert call_gemini("prompt", model="gemini-3.1-flash-lite", max_retries=1) == "hello"


@pytest.mark.asyncio
async def test_call_gemini_async_raises_on_empty_text_response():
    """async: text が空のレスポンスは無言で "" を返さず RuntimeError を送出する。"""
    async def _empty_stream():
        yield _FakeChunk("", usage_metadata=_FakeUsage(prompt_token_count=100, candidates_token_count=0))

    fake_client = MagicMock()
    fake_client.aio.models.generate_content_stream = AsyncMock(return_value=_empty_stream())

    with patch("core.llm_client._get_client", return_value=fake_client):
        with pytest.raises(RuntimeError):
            await call_gemini_async("prompt", model="gemini-3.1-flash-lite", max_retries=1)


def test_rotation_to_paid_key_restores_tier_to_paid():
    """429/503 でダウンシフト後、ローテーション先が有料キーなら TierManager を PAID に戻す。
    旧実装は無条件ダウンシフトのまま戻す処理がなく、有料キーへ切り替わった後も以降の
    リクエストが不必要に Lite モデル・縮小バッチで処理され続けていた（2026-07-21 レビュー指摘）。
    """
    from core.llm_client import key_rotator, tier_manager, GeminiTier, _maybe_restore_tier_after_rotation

    key_rotator.configure(["free-key", "paid-key"], tiers=["free", "paid"])
    try:
        key_rotator.advance()  # free-key -> paid-key
        tier_manager.downgrade()  # 429検知でFREEに落ちた状態を再現
        assert tier_manager.current_tier == GeminiTier.FREE

        _maybe_restore_tier_after_rotation()

        assert tier_manager.current_tier == GeminiTier.PAID
    finally:
        key_rotator.configure([])
        tier_manager.set_tier(GeminiTier.UNKNOWN)


def test_rotation_between_free_keys_keeps_tier_free():
    """無料キー同士のローテーション（free1→free2）では FREE のまま据え置く（有料キーに
    切り替わったときだけ復元するのが正しい）。"""
    from core.llm_client import key_rotator, tier_manager, GeminiTier, _maybe_restore_tier_after_rotation

    key_rotator.configure(["free-1", "free-2", "paid-key"], tiers=["free", "free", "paid"])
    try:
        key_rotator.advance()  # free-1 -> free-2 (still free)
        tier_manager.downgrade()

        _maybe_restore_tier_after_rotation()

        assert tier_manager.current_tier == GeminiTier.FREE
    finally:
        key_rotator.configure([])
        tier_manager.set_tier(GeminiTier.UNKNOWN)


def test_reset_pipeline_state_clears_client_cache():
    """reset_pipeline_state() はクライアントキャッシュ（呼び出しスレッドごとに独立した辞書）
    もクリアし、次のパイプラインが新しい genai.Client（＝新しい非同期トランスポート）を
    生成するようにする。"""
    from core import llm_client

    _get_client(api_key="dummy-test-key")
    assert "dummy-test-key" in llm_client._get_clients_dict()

    reset_pipeline_state()

    assert "dummy-test-key" not in llm_client._get_clients_dict()


def test_get_client_reuses_instance_within_same_loop():
    """同一イベントループ内での呼び出しはキャッシュされたクライアントをそのまま返す。"""
    from core import llm_client

    async def _run():
        c1 = _get_client(api_key="loop-test-key")
        c2 = _get_client(api_key="loop-test-key")
        return c1, c2

    try:
        c1, c2 = asyncio.run(_run())
        assert c1 is c2
    finally:
        llm_client._get_clients_dict().pop("loop-test-key", None)


def test_get_client_recreates_when_event_loop_differs():
    """2026-07-21: reset_pipeline_state() は run_pipeline() 呼び出しごとに1回しかキャッシュを
    クリアしないが、1回の run_pipeline() 内でも Phase 1 と Phase 4 はそれぞれ別の
    asyncio.run() ループを使う。前のループで生成されたクライアントを新しいループでそのまま
    再利用すると初回呼び出しが "RuntimeError: Event loop is closed" になっていた
    （書籍モードで章ごとに毎回再現）。カレントループが生成時と異なれば再生成すべき。"""
    from core import llm_client

    async def _get_in_new_loop():
        return _get_client(api_key="loop-mismatch-key")

    try:
        client_a = asyncio.run(_get_in_new_loop())  # Phase 1 相当のループ
        client_b = asyncio.run(_get_in_new_loop())  # Phase 4 相当の別ループ
        assert client_a is not client_b
    finally:
        llm_client._get_clients_dict().pop("loop-mismatch-key", None)
