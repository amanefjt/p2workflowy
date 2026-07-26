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


def test_model_rotator_best_available_returns_current_model_if_available():
    """KeyRotator.best_available() と同じ設計（§9）。現在のモデルが available なら
    無駄な切替をせずそのまま返す。"""
    from core.llm_client import model_rotator

    try:
        model_rotator.reset()
        pool_first = model_rotator.current()
        assert model_rotator.best_available(lambda m: True) == pool_first
    finally:
        model_rotator.reset()


def test_model_rotator_best_available_picks_next_available_when_current_is_not():
    from core.llm_client import model_rotator

    try:
        model_rotator.reset()
        pool_first = model_rotator.current()
        pool_second = model_rotator.pool_models()[1]
        avail = {pool_first: False, pool_second: True}
        assert model_rotator.best_available(lambda m: avail.get(m, False)) == pool_second
    finally:
        model_rotator.reset()


def test_model_rotator_best_available_returns_to_recovered_model():
    """旧 advance() の forward-only と異なり、一度進んだ先のモデルもクールダウン中に
    なり、かつ以前のモデルが回復していれば戻れる（I-31系と同じ片側フォールバックの
    再発防止と対をなす、モデル軸のクールダウン考慮漏れの修正）。"""
    from core.llm_client import model_rotator

    try:
        model_rotator.reset()
        pool_first = model_rotator.current()
        pool_second = model_rotator.pool_models()[1]

        avail = {pool_first: False, pool_second: True}
        assert model_rotator.best_available(lambda m: avail.get(m, False)) == pool_second

        # pool_second もクールダウンに入り、pool_first は既に回復済み
        avail = {pool_first: True, pool_second: False}
        assert model_rotator.best_available(lambda m: avail.get(m, False)) == pool_first
    finally:
        model_rotator.reset()


def test_model_rotator_best_available_all_unavailable_falls_back_to_forward_only_advance():
    """全モデルが使用不可（＝全レーンがクールダウン中）なら、新しい判断材料が無いので
    従来の forward-only な advance() にフォールバックする。"""
    from core.llm_client import model_rotator

    try:
        model_rotator.reset()
        pool_first = model_rotator.current()
        result = model_rotator.best_available(lambda m: False)
        assert result != pool_first
        assert result == model_rotator.pool_models()[1]
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


# --- §8: キー × モデルの2軸ラウンドロビン（KeyRotator.pool_keys / key_pinned / restrict_to） ---


def test_pool_keys_returns_only_free_keys():
    """pool_keys() は tier が "free" のキーだけを並び順どおりに返す（有料キーは含めない）。
    無料枠専用ペースのリミッタを有料キーに適用すると有料ユーザーを不必要に遅くするため。"""
    from core.llm_client import key_rotator

    key_rotator.configure(
        ["f1", "f2", "f3", "f4", "paid"], tiers=["free", "free", "free", "free", "paid"]
    )
    try:
        assert key_rotator.pool_keys() == ["f1", "f2", "f3", "f4"]
    finally:
        key_rotator.configure([])


def test_pool_keys_skips_unset_keys():
    """未設定（None）のキーは configure() が除外するため、2本しか設定していない環境でも壊れない。"""
    from core.llm_client import key_rotator

    key_rotator.configure(["f1", None, None, "f4", "paid"],
                          tiers=["free", "free", "free", "free", "paid"])
    try:
        assert key_rotator.pool_keys() == ["f1", "f4"]
        assert key_rotator.count == 3
    finally:
        key_rotator.configure([])


def test_pool_keys_empty_when_tiers_unknown():
    """tiers を渡していない場合はキー種別が判定できないため空リスト（キー軸RRは自然に無効）。"""
    from core.llm_client import key_rotator

    key_rotator.configure(["a", "b"])
    try:
        assert key_rotator.pool_keys() == []
    finally:
        key_rotator.configure([])


@pytest.mark.asyncio
async def test_call_gemini_async_key_pinned_bypasses_key_rotator():
    """key_pinned=True のとき、呼び出し元が渡した api_key が key_rotator.current() に
    上書きされない（model_pinned の完全な鏡写し）。429 に遭遇してもキーは前進しない。"""
    from core.llm_client import key_rotator

    used_keys = []
    call_count = {"n": 0}

    def _fake_get_client(api_key=None):
        used_keys.append(api_key)
        return fake_client

    async def _stream_side_effect(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded, retry in 0.1s")

        async def _gen():
            yield _FakeChunk("ok", usage_metadata=_FakeUsage(10, 5))
        return _gen()

    fake_client = MagicMock()
    fake_client.aio.models.generate_content_stream = AsyncMock(side_effect=_stream_side_effect)

    key_rotator.configure(["f1", "f2", "paid"], tiers=["free", "free", "paid"])
    try:
        with patch("core.llm_client._get_client", side_effect=_fake_get_client), \
             patch("core.llm_client.asyncio.sleep", new=AsyncMock()):
            result = await call_gemini_async(
                "prompt", api_key="f2", model_pinned=True, key_pinned=True,
                max_retries=3, retry_delay=0.01,
            )

        assert result == "ok"
        # current() は f1 のままだが、渡した f2 がそのまま使われる
        assert used_keys == ["f2"]
        # 429 でもキーは前進していない（リアクティブなフォールバックを混ぜない）
        assert key_rotator.current() == "f1"
    finally:
        key_rotator.configure([])


@pytest.mark.asyncio
async def test_call_gemini_async_without_key_pinned_still_rotates():
    """key_pinned=False（既定）のときは従来どおり key_rotator.current() に上書きされ、
    429 でキーローテーションが働く（既存挙動の回帰確認）。"""
    from core.llm_client import key_rotator, model_rotator

    used_keys = []
    call_count = {"n": 0}

    def _fake_get_client(api_key=None):
        used_keys.append(api_key)
        return fake_client

    async def _stream_side_effect(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded, retry in 0.1s")

        async def _gen():
            yield _FakeChunk("ok", usage_metadata=_FakeUsage(10, 5))
        return _gen()

    fake_client = MagicMock()
    fake_client.aio.models.generate_content_stream = AsyncMock(side_effect=_stream_side_effect)

    key_rotator.configure(["f1", "f2", "paid"], tiers=["free", "free", "paid"])
    model_rotator.reset()
    try:
        with patch("core.llm_client._get_client", side_effect=_fake_get_client), \
             patch("core.llm_client.asyncio.sleep", new=AsyncMock()):
            # model_pinned=True にしてモデル軸のローテーションを抑止し、キー軸だけを見る
            result = await call_gemini_async(
                "prompt", api_key="ignored", model="explicit-model", model_pinned=True,
                max_retries=3, retry_delay=0.01,
            )

        assert result == "ok"
        assert used_keys == ["f1", "f2"]
        assert key_rotator.current() == "f2"
    finally:
        key_rotator.configure([])
        model_rotator.reset()


def test_restrict_to_is_thread_local():
    """restrict_to() は呼び出したスレッドにしか効かない（書籍モードの章並列化フック）。
    制限を設定していないスレッドの current()/pool_keys()/count はグローバル状態のまま。"""
    import threading
    from core.llm_client import key_rotator

    key_rotator.configure(["f1", "f2", "f3", "f4", "paid"],
                          tiers=["free"] * 4 + ["paid"])
    observed = {}
    barrier = threading.Barrier(2)

    def worker():
        key_rotator.restrict_to(["f3"], tiers=["free"])
        try:
            observed["worker_current"] = key_rotator.current()
            observed["worker_pool"] = key_rotator.pool_keys()
            observed["worker_has_next"] = key_rotator.has_next()
            observed["worker_count"] = key_rotator.count
            barrier.wait(timeout=5)   # メインスレッドが観測するまで制限を保持
            barrier.wait(timeout=5)
        finally:
            key_rotator.clear_restriction()

    t = threading.Thread(target=worker)
    try:
        t.start()
        barrier.wait(timeout=5)
        # メインスレッドから見て一切影響がない
        assert key_rotator.current() == "f1"
        assert key_rotator.pool_keys() == ["f1", "f2", "f3", "f4"]
        assert key_rotator.count == 5
        assert key_rotator.is_restricted() is False
        barrier.wait(timeout=5)
        t.join(timeout=5)

        assert observed["worker_current"] == "f3"
        assert observed["worker_pool"] == ["f3"]
        assert observed["worker_has_next"] is False
        assert observed["worker_count"] == 1
    finally:
        t.join(timeout=5)
        key_rotator.configure([])


def test_restrict_to_advance_does_not_touch_global_index():
    """制限中の advance() はスレッドローカルなインデックスだけを動かし、
    プロセスグローバルな _index には触れない。"""
    from core.llm_client import key_rotator

    key_rotator.configure(["f1", "f2", "f3"], tiers=["free"] * 3)
    try:
        key_rotator.restrict_to(["f2", "f3"], tiers=["free", "free"])
        assert key_rotator.current() == "f2"
        assert key_rotator.advance() == "f3"
        assert key_rotator.has_next() is False
        key_rotator.clear_restriction()
        # 解除後はグローバル状態が無傷のまま戻る
        assert key_rotator.current() == "f1"
        assert key_rotator.index == 0
    finally:
        key_rotator.clear_restriction()
        key_rotator.configure([])


# --- §9: レーン単位のクールダウン（circuit breaker） ---


def test_lane_cooldown_registry_expires_after_seconds():
    """mark() 直後は is_cooling() が True。クールダウン秒数が経過すると False に戻る
    （時刻は time.time() をパッチして注入し、sleep には依存しない）。"""
    from core.llm_client import LaneCooldownRegistry

    reg = LaneCooldownRegistry()
    with patch("core.llm_client.time.time", return_value=1000.0):
        reg.mark("k1", "m1", 5.0)
        assert reg.is_cooling("k1", "m1") is True

    with patch("core.llm_client.time.time", return_value=1004.9):
        assert reg.is_cooling("k1", "m1") is True  # まだ5秒経っていない

    with patch("core.llm_client.time.time", return_value=1005.1):
        assert reg.is_cooling("k1", "m1") is False  # 5秒経過して復帰


def test_lane_cooldown_registry_keeps_longest_when_marked_twice():
    """同一レーンに短いクールダウンを後から重ねても、既存の長い方を短縮しない
    （複数スレッドが同時に同じレーンで429を踏んでも安全側に倒す）。"""
    from core.llm_client import LaneCooldownRegistry

    reg = LaneCooldownRegistry()
    with patch("core.llm_client.time.time", return_value=1000.0):
        reg.mark("k1", "m1", 10.0)
        reg.mark("k1", "m1", 2.0)
        assert reg.remaining("k1", "m1") == pytest.approx(10.0)


def test_lane_cooldown_registry_shared_across_threads():
    """プロセスグローバル + Lock: 別スレッドで mark() したクールダウンが、
    メインスレッドの is_cooling() からも即座に見える（TierManager/ModelRotatorの
    スレッドローカル設計とは意図的に異なる。§9 の設計判断）。"""
    import threading
    from core.llm_client import LaneCooldownRegistry

    reg = LaneCooldownRegistry()

    def worker():
        reg.mark("thread-key", "thread-model", 999.0)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=5)

    assert reg.is_cooling("thread-key", "thread-model") is True


def test_classify_quota_violation_tpm():
    """quotaId に "Token" を含む場合は TPM 起因と判定する。"""
    from core.llm_client import _classify_quota_violation

    exc = RuntimeError(
        "429 RESOURCE_EXHAUSTED: quotaId=GenerateContentInputTokensPerModelPerMinute-FreeTier"
    )
    assert _classify_quota_violation(exc) == "tpm"


def test_classify_quota_violation_rpd():
    """quotaId に "Day" を含む場合は RPD 起因と判定する。"""
    from core.llm_client import _classify_quota_violation

    exc = RuntimeError(
        "429 RESOURCE_EXHAUSTED: quotaId=GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    )
    assert _classify_quota_violation(exc) == "rpd"


def test_classify_quota_violation_rpm():
    """quotaId に "Minute"/"Requests" を含み Token を含まない場合は RPM 起因と判定する。"""
    from core.llm_client import _classify_quota_violation

    exc = RuntimeError(
        "429 RESOURCE_EXHAUSTED: quotaId=GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
    )
    assert _classify_quota_violation(exc) == "rpm"


def test_classify_quota_violation_unknown_for_503():
    """quotaMetric の手がかりが無い 503 は unknown 扱い（安全側デフォルトへ）。"""
    from core.llm_client import _classify_quota_violation

    exc = RuntimeError("503 UNAVAILABLE: The model is overloaded. Please try again later.")
    assert _classify_quota_violation(exc) == "unknown"


class _FakeAPIError(Exception):
    """google.genai.errors.APIError を模した最小限のフェイク。`.details` に
    レスポンスJSON全体（'error'キー配下にdetails配列）を保持する実装を再現する。"""

    def __init__(self, code, details):
        self.code = code
        self.details = details
        super().__init__(f"{code} . {details}")


# 2026-07-26、書籍モード章並列化の実走行検証（relationspdf.pdf、直列ベースライン）で
# 実際に踏んだ429のレスポンスJSONそのもの（book_sessions/relationspdf_.../Phase 0
# グロッサリー生成中、10:27:34発生）。作成したテスト用データではなく実データ。
REAL_TPM_429_DETAILS = {
    "error": {
        "code": 429,
        "message": (
            "You exceeded your current quota, please check your plan and billing details. "
            "For more information on this error, head to: "
            "https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, "
            "head to: https://ai.dev/rate-limit. \n"
            "* Quota exceeded for metric: "
            "generativelanguage.googleapis.com/generate_content_free_tier_input_token_count, "
            "limit: 250000, model: gemini-3.1-flash-lite\n"
            "Please retry in 25.708951197s."
        ),
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {
                "@type": "type.googleapis.com/google.rpc.Help",
                "links": [
                    {
                        "description": "Learn more about Gemini API quotas",
                        "url": "https://ai.google.dev/gemini-api/docs/rate-limits",
                    }
                ],
            },
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [
                    {
                        "quotaMetric": (
                            "generativelanguage.googleapis.com/"
                            "generate_content_free_tier_input_token_count"
                        ),
                        "quotaId": "GenerateContentInputTokensPerModelPerMinute-FreeTier",
                        "quotaDimensions": {
                            "location": "global",
                            "model": "gemini-3.1-flash-lite",
                        },
                        "quotaValue": "250000",
                    }
                ],
            },
            {
                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                "retryDelay": "25s",
            },
        ],
    }
}


def test_classify_quota_violation_real_429_payload_is_tpm():
    """実際に踏んだ429（REAL_TPM_429_DETAILS）を _classify_quota_violation に食わせると
    'tpm' と判定される（§9で「未確認」としていた、実レスポンスでの動作確認）。"""
    from core.llm_client import _classify_quota_violation

    exc = _FakeAPIError(429, REAL_TPM_429_DETAILS)
    assert _classify_quota_violation(exc) == "tpm"


def test_extract_retry_delay_seconds_real_429_payload():
    """実際の429（REAL_TPM_429_DETAILS）から retryDelay '25s' を 25.0 として抽出できる
    （§9で「未確認」としていた、実レスポンスでの動作確認）。"""
    from core.llm_client import _extract_retry_delay_seconds

    exc = _FakeAPIError(429, REAL_TPM_429_DETAILS)
    assert _extract_retry_delay_seconds(exc) == 25.0


def test_is_model_scoped_quota_true_for_real_429_payload():
    """実際の429（quotaId='...PerModelPerMinute-FreeTier'、quotaDimensions.model明示）は
    per-model スコープと判定される。この実データが根拠となり、§9の「TPMはLiteプール2モデルで
    共有」という前提を訂正した（コード修正: TPM起因でもPerModelが確認できればモデル
    ローテーションを試みる）。"""
    from core.llm_client import _is_model_scoped_quota

    exc = _FakeAPIError(429, REAL_TPM_429_DETAILS)
    assert _is_model_scoped_quota(exc) is True


def test_is_model_scoped_quota_false_when_permodel_absent():
    """quotaIdに"PerModel"の手がかりが無い場合はFalse（判別できないケースは保守的に扱う）。"""
    from core.llm_client import _is_model_scoped_quota

    exc = RuntimeError(
        "429 RESOURCE_EXHAUSTED: quotaId=GenerateContentInputTokensPerMinute-FreeTier"
    )
    assert _is_model_scoped_quota(exc) is False


def test_is_model_scoped_quota_false_for_503_without_details():
    """quotaMetricの手がかりが一切無い503はFalse（不明な場合に真としない安全側デフォルト）。"""
    from core.llm_client import _is_model_scoped_quota

    exc = RuntimeError("503 UNAVAILABLE: The model is overloaded. Please try again later.")
    assert _is_model_scoped_quota(exc) is False


def test_extract_retry_delay_seconds_from_retry_info_style_message():
    from core.llm_client import _extract_retry_delay_seconds

    exc = RuntimeError("429 RESOURCE_EXHAUSTED. {'retryDelay': '19s', 'quotaId': '...'}")
    assert _extract_retry_delay_seconds(exc) == 19.0


def test_extract_retry_delay_seconds_legacy_format_still_supported():
    """既存のフェイクテスト例外（"retry in Xs"）形式にも後方互換で対応する。"""
    from core.llm_client import _extract_retry_delay_seconds

    exc = RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded, retry in 0.1s")
    assert _extract_retry_delay_seconds(exc) == 0.1


def test_extract_retry_delay_seconds_none_when_absent():
    from core.llm_client import _extract_retry_delay_seconds

    exc = RuntimeError("503 UNAVAILABLE")
    assert _extract_retry_delay_seconds(exc) is None


def test_lane_cooldown_seconds_clamps_oversized_retry_delay():
    """retryDelay が異常に大きい場合、RPM/TPM由来なら上限(120秒)にクランプする
    （retryDelayは不正確という報告があるため、回復スケールを超える値を信用しない）。"""
    from core.llm_client import _lane_cooldown_seconds, RETRY_DELAY_CLAMP_MAX_SECONDS

    exc = RuntimeError("429 RESOURCE_EXHAUSTED. {'retryDelay': '9999s'}")
    assert _lane_cooldown_seconds(exc, "rpm") == RETRY_DELAY_CLAMP_MAX_SECONDS


def test_lane_cooldown_seconds_clamps_undersized_retry_delay():
    """retryDelay が異常に小さい(ほぼ0)場合、下限(1秒)にクランプする。"""
    from core.llm_client import _lane_cooldown_seconds, COOLDOWN_MIN_SECONDS

    exc = RuntimeError("429 RESOURCE_EXHAUSTED. {'retryDelay': '0.0001s'}")
    assert _lane_cooldown_seconds(exc, "tpm") == COOLDOWN_MIN_SECONDS


def test_lane_cooldown_seconds_rpd_ignores_unreliable_short_retry_delay():
    """RPD起因では、短い(不正確な)retryDelayを採用せず日次リセット計算値を優先する。"""
    from core.llm_client import _lane_cooldown_seconds, COOLDOWN_RPD_MIN_SECONDS

    exc = RuntimeError("429 RESOURCE_EXHAUSTED. {'retryDelay': '5s'}")
    seconds = _lane_cooldown_seconds(exc, "rpd")
    assert seconds >= COOLDOWN_RPD_MIN_SECONDS


def test_seconds_until_next_pacific_midnight_is_within_one_day():
    from core.llm_client import _seconds_until_next_pacific_midnight

    seconds = _seconds_until_next_pacific_midnight()
    assert 0 < seconds <= 24 * 3600


def test_pick_lane_skips_cooling_lane():
    """クールダウン中のレーンはラウンドロビンの候補から外れ、生きているレーンが選ばれる。"""
    from core.llm_client import pick_lane, lane_cooldown

    pool = ["m1", "m2"]
    keys = ["k1", "k2"]
    # rr_index=0 が本来選ぶはずのレーン (k1, m1) をクールダウンさせる
    lane_cooldown.mark("k1", "m1", 999.0)

    lane = pick_lane(pool, keys, 0)

    assert lane is not None
    assert lane != ("k1", "m1")
    key, model = lane
    assert lane_cooldown.is_cooling(key, model) is False


def test_pick_lane_returns_none_when_all_lanes_cooling():
    """全レーンがクールダウン中なら None を返す（例外を投げない・呼び出し元がフォールバック）。"""
    from core.llm_client import pick_lane, lane_cooldown

    pool = ["m1", "m2"]
    keys = ["k1", "k2"]
    for k in keys:
        for m in pool:
            lane_cooldown.mark(k, m, 999.0)

    assert pick_lane(pool, keys, 0) is None


def test_pick_lane_model_axis_only_skips_cooling_model():
    """キー軸が無い（keys=[]）場合はモデル軸だけで、クールダウン中のモデルを飛ばす。"""
    from core.llm_client import pick_lane, lane_cooldown

    pool = ["m1", "m2"]
    lane_cooldown.mark(None, "m1", 999.0)

    lane = pick_lane(pool, [], 0)

    assert lane == (None, "m2")


def test_best_available_returns_current_key_if_available():
    from core.llm_client import key_rotator

    key_rotator.configure(["f1", "f2", "paid"], tiers=["free", "free", "paid"])
    try:
        assert key_rotator.best_available(lambda k: True) == "f1"
        assert key_rotator.current() == "f1"  # 無駄な切替をしていない
    finally:
        key_rotator.configure([])


def test_best_available_picks_next_available_when_current_is_not():
    from core.llm_client import key_rotator

    key_rotator.configure(["f1", "f2", "paid"], tiers=["free", "free", "paid"])
    try:
        avail = {"f1": False, "f2": True, "paid": True}
        assert key_rotator.best_available(lambda k: avail.get(k, False)) == "f2"
        assert key_rotator.current() == "f2"
    finally:
        key_rotator.configure([])


def test_best_available_returns_to_recovered_free_key():
    """forward-only の不可逆性の解消（§6既知の限界 → §9で解消）:
    free1がクールダウン中にfree2へ切り替わった後、free1が回復すればfree1に戻れる。"""
    from core.llm_client import key_rotator

    key_rotator.configure(["f1", "f2", "paid"], tiers=["free", "free", "paid"])
    try:
        avail = {"f1": False, "f2": True, "paid": True}
        assert key_rotator.best_available(lambda k: avail.get(k, False)) == "f2"

        # f1が回復し、逆にf2がクールダウンに入った状況を再現
        avail = {"f1": True, "f2": False, "paid": True}
        assert key_rotator.best_available(lambda k: avail.get(k, False)) == "f1"
    finally:
        key_rotator.configure([])


def test_best_available_falls_back_to_paid_only_as_last_resort():
    """無料キーが1本でも生きていれば有料キーには落ちない。全無料キーが不可の場合のみ
    有料キーへフォールバックする（既存の優先順位を維持）。"""
    from core.llm_client import key_rotator

    key_rotator.configure(["f1", "f2", "paid"], tiers=["free", "free", "paid"])
    try:
        avail = {"f1": False, "f2": False, "paid": True}
        assert key_rotator.best_available(lambda k: avail.get(k, False)) == "paid"
    finally:
        key_rotator.configure([])


def test_best_available_all_unavailable_falls_back_to_forward_only_advance():
    """全キーが使用不可（新しい情報が無い）場合は、既存の forward-only advance() と
    同じ挙動にフォールバックする（例外を投げない・処理を止めない）。"""
    from core.llm_client import key_rotator

    key_rotator.configure(["f1", "f2", "paid"], tiers=["free", "free", "paid"])
    try:
        result = key_rotator.best_available(lambda k: False)
        assert result == "f2"  # advance() と同じ: 現在地(f1)から1つだけ前進
        assert key_rotator.current() == "f2"
    finally:
        key_rotator.configure([])


@pytest.mark.asyncio
async def test_call_gemini_async_cools_lane_and_avoids_it_via_key_rotation():
    """429を検知したレーン(初期キー, model)がクールダウンに入り、KeyRotatorが
    (旧advance()のforward-onlyではなく)クールダウン中でない別のフリーキーへ切り替わる。"""
    from core.llm_client import key_rotator, model_rotator, lane_cooldown

    captured_keys = []
    call_count = {"n": 0}

    def _fake_get_client(api_key=None):
        captured_keys.append(api_key)
        return fake_client

    async def _stream_side_effect(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded, retry in 0.1s")

        async def _gen():
            yield _FakeChunk("ok", usage_metadata=_FakeUsage(10, 5))
        return _gen()

    fake_client = MagicMock()
    fake_client.aio.models.generate_content_stream = AsyncMock(side_effect=_stream_side_effect)

    key_rotator.configure(["f1", "f2", "paid"], tiers=["free", "free", "paid"])
    model_rotator.reset()
    try:
        with patch("core.llm_client._get_client", side_effect=_fake_get_client), \
             patch("core.llm_client.asyncio.sleep", new=AsyncMock()):
            result = await call_gemini_async(
                "prompt", api_key="f1", model="explicit-model", model_pinned=True,
                max_retries=3, retry_delay=0.01,
            )

        assert result == "ok"
        assert captured_keys == ["f1", "f2"]
        assert key_rotator.current() == "f2"
        # f1・explicit-model レーンがクールダウンに記録されている
        assert lane_cooldown.is_cooling("f1", "explicit-model") is True
    finally:
        key_rotator.configure([])
        model_rotator.reset()
        lane_cooldown.clear()


@pytest.mark.asyncio
async def test_call_gemini_async_model_scoped_tpm_quota_rotates_model_not_key():
    """quotaId に "PerModel" が確認できる TPM 起因の429では、モデルローテーションを
    試す（キー切替へは直行しない）。

    2026-07-26、書籍モード章並列化の実走行検証で実際に踏んだ429の quotaId が
    "GenerateContentInputTokensPerModelPerMinute-FreeTier"、quotaDimensions に
    {'model': 'gemini-3.1-flash-lite'} が明示されており、このTPMクォータはモデル単位で
    独立集計されていることが実データで確認できた（§9訂正）。旧実装は「TPM起因なら常に
    モデルローテーションをスキップ」だったが、これは誤りだったため、quotaIdに"PerModel"が
    確認できる場合はモデルローテーションを優先するよう修正した。この文字列は実際の429の
    quotaId をそのまま使っている（本テストのために作った値ではない）。
    """
    from core.llm_client import key_rotator, model_rotator, lane_cooldown

    captured_models = []
    call_count = {"n": 0}

    async def _stream_side_effect(**kwargs):
        captured_models.append(kwargs.get("model"))
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError(
                "429 RESOURCE_EXHAUSTED: quotaId=GenerateContentInputTokensPerModelPerMinute-FreeTier"
            )

        async def _gen():
            yield _FakeChunk("translated", usage_metadata=_FakeUsage(10, 5))
        return _gen()

    fake_client = MagicMock()
    fake_client.aio.models.generate_content_stream = AsyncMock(side_effect=_stream_side_effect)

    model_rotator.reset()
    pool_first = model_rotator.current()
    assert model_rotator.has_next(), "テスト前提: DEFAULT_MODEL_FREE_POOL に2要素以上必要"
    pool_second = model_rotator.pool_models()[1]
    key_rotator.configure(["f1", "f2", "paid"], tiers=["free", "free", "paid"])

    try:
        with patch("core.llm_client._get_client", return_value=fake_client), \
             patch("core.llm_client.asyncio.sleep", new=AsyncMock()):
            result = await call_gemini_async(
                "prompt", model=pool_first, api_key="f1", max_retries=3, retry_delay=0.01
            )

        assert result == "translated"
        # モデルが切り替わっている（PerModelスコープのTPMなのでモデルローテーションが有効）
        assert captured_models == [pool_first, pool_second]
        assert model_rotator.current() == pool_second
        # キーは前進していない（モデル切替だけで回復したため）
        assert key_rotator.current() == "f1"
    finally:
        model_rotator.reset()
        key_rotator.configure([])
        lane_cooldown.clear()


@pytest.mark.asyncio
async def test_call_gemini_async_unscoped_tpm_quota_skips_model_rotation_and_rotates_key():
    """quotaId に "PerModel" の手がかりが無い（モデル単位かプロジェクト単位か判別できない）
    TPM 起因の429では、根拠のない前提でモデルローテーションを試さず、保守的にキー切替へ
    直行する（§9 の元々の設計を、判別できないケースに限定して維持）。"""
    from core.llm_client import key_rotator, model_rotator, lane_cooldown

    captured_models = []
    call_count = {"n": 0}

    async def _stream_side_effect(**kwargs):
        captured_models.append(kwargs.get("model"))
        call_count["n"] += 1
        if call_count["n"] == 1:
            # "PerModel" を含まない TPM 起因のクォータ文字列（プロジェクト単位か
            # モデル単位か判別できないケースを模擬）
            raise RuntimeError(
                "429 RESOURCE_EXHAUSTED: quotaId=GenerateContentInputTokensPerMinute-FreeTier"
            )

        async def _gen():
            yield _FakeChunk("translated", usage_metadata=_FakeUsage(10, 5))
        return _gen()

    fake_client = MagicMock()
    fake_client.aio.models.generate_content_stream = AsyncMock(side_effect=_stream_side_effect)

    model_rotator.reset()
    pool_first = model_rotator.current()
    assert model_rotator.has_next(), "テスト前提: DEFAULT_MODEL_FREE_POOL に2要素以上必要"
    key_rotator.configure(["f1", "f2", "paid"], tiers=["free", "free", "paid"])

    try:
        with patch("core.llm_client._get_client", return_value=fake_client), \
             patch("core.llm_client.asyncio.sleep", new=AsyncMock()):
            result = await call_gemini_async(
                "prompt", model=pool_first, api_key="f1", max_retries=3, retry_delay=0.01
            )

        assert result == "translated"
        # モデルは切り替わっていない（PerModelの手がかりが無いのでモデルローテーションはスキップ）
        assert captured_models == [pool_first, pool_first]
        assert model_rotator.current() == pool_first  # 共有状態も変化していない
        # 代わりにキーが前進している
        assert key_rotator.current() == "f2"
    finally:
        model_rotator.reset()
        key_rotator.configure([])
        lane_cooldown.clear()
