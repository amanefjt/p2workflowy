import pytest


@pytest.fixture(autouse=True)
def _clear_lane_cooldown_registry():
    """core.llm_client.lane_cooldown はプロセスグローバル（§9、意図的にスレッドローカルに
    しない設計）。テスト間でクールダウン状態が漏れると、429シミュレーションを含むテストの
    後に実行される別テスト（例: ラウンドロビンの順序を厳密に検証するテスト）が、たまたま
    同じ (api_key, model) の組み合わせを使っていた場合に不安定になる。各テストの前後で
    必ずクリアし、独立性を保証する。"""
    from core.llm_client import lane_cooldown
    lane_cooldown.clear()
    yield
    lane_cooldown.clear()
