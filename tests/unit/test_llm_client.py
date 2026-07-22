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


def test_model_rotator_resolve_advance_reset():
    """ModelRotator: プール外モデルは素通し、プール内モデルはローテーション先へ差し替わり、
    forward-only で最終要素より先には進まず、reset() で先頭に戻る。"""
    from core.llm_client import model_rotator

    try:
        model_rotator.reset()
        pool_first = model_rotator.current()

        # プール外モデルはそのまま
        assert model_rotator.resolve("gemini-3.6-flash") == "gemini-3.6-flash"
        assert not model_rotator.is_pool_member("gemini-3.6-flash")

        assert model_rotator.is_pool_member(pool_first)
        assert model_rotator.resolve(pool_first) == pool_first

        assert model_rotator.has_next()
        second = model_rotator.advance()
        assert second != pool_first
        assert model_rotator.resolve(pool_first) == second  # プール内モデルはどれを渡しても現在地に揃う

        # 最終要素で forward-only が止まる
        while model_rotator.has_next():
            model_rotator.advance()
        last = model_rotator.current()
        assert model_rotator.advance() == last  # これ以上進まない

        model_rotator.reset()
        assert model_rotator.current() == pool_first
    finally:
        model_rotator.reset()


@pytest.mark.asyncio
async def test_call_gemini_async_rotates_model_on_resource_limit():
    """429/503 を検知したら、無料枠Liteプールの次のモデルへ切り替えて即リトライする
    （キーローテーションと同じ forward-only 方式。同一キー内で完結するため client 再生成は不要）。"""
    from core.llm_client import model_rotator

    captured_models = []
    call_count = {"n": 0}

    async def _stream_side_effect(**kwargs):
        captured_models.append(kwargs.get("model"))
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded, retry in 0.1s")

        async def _gen():
            yield _FakeChunk("translated", usage_metadata=_FakeUsage(10, 5))
        return _gen()

    fake_client = MagicMock()
    fake_client.aio.models.generate_content_stream = AsyncMock(side_effect=_stream_side_effect)

    model_rotator.reset()
    pool_first = model_rotator.current()
    assert model_rotator.has_next(), "テスト前提: DEFAULT_MODEL_FREE_POOL に2要素以上必要"
    pool_second = model_rotator._local.pool[1]

    try:
        with patch("core.llm_client._get_client", return_value=fake_client), \
             patch("core.llm_client.asyncio.sleep", new=AsyncMock()):
            result = await call_gemini_async("prompt", model=pool_first, max_retries=3, retry_delay=0.01)

        assert result == "translated"
        assert captured_models == [pool_first, pool_second]
    finally:
        model_rotator.reset()


@pytest.mark.asyncio
async def test_call_gemini_async_model_pinned_bypasses_rotation():
    """model_pinned=True の場合、429を検知しても ModelRotator の resolve()/advance() を
    経由しない（Phase4のラウンドロビン割り当てが、共有のリアクティブなローテーション状態に
    よって上書きされないようにするための分離）。同一モデルのままダウンシフト+待機で
    リトライし、共有の model_rotator の index は変化しない。"""
    from core.llm_client import model_rotator

    captured_models = []
    call_count = {"n": 0}

    async def _stream_side_effect(**kwargs):
        captured_models.append(kwargs.get("model"))
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded, retry in 0.1s")

        async def _gen():
            yield _FakeChunk("translated", usage_metadata=_FakeUsage(10, 5))
        return _gen()

    fake_client = MagicMock()
    fake_client.aio.models.generate_content_stream = AsyncMock(side_effect=_stream_side_effect)

    model_rotator.reset()
    pool_second = model_rotator._local.pool[1]  # 先頭以外を明示的にピン留めして検証

    try:
        with patch("core.llm_client._get_client", return_value=fake_client), \
             patch("core.llm_client.asyncio.sleep", new=AsyncMock()):
            result = await call_gemini_async(
                "prompt", model=pool_second, model_pinned=True, max_retries=3, retry_delay=0.01
            )

        assert result == "translated"
        # ピン留めされたモデルのまま維持され、resolve()で先頭モデルへ引き戻されていない
        assert captured_models == [pool_second, pool_second]
        # 共有の ModelRotator 状態は変化していない（advance() が呼ばれていない）
        assert model_rotator.current() == model_rotator.pool_models()[0]
    finally:
        model_rotator.reset()


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
