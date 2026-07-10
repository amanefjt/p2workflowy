"""
DEFAULT_MODEL_RESUME ルーティングのユニットテスト

Phase 2 の generate_resume() だけが「resume」用途のモデル解決
(get_default_model("resume")) を使うようにする変更をテストする。

- coreprompts.json の DEFAULT_MODEL_RESUME キーが設定されていればそれを返す
- 環境変数 DEFAULT_MODEL_RESUME はキーより優先される
- どちらも空なら、通常の tier 追従デフォルト（"default" purpose と同じ動作）にフォールバックする
- generate_resume() は resume 用に解決されたモデルを call_gemini に渡す
"""

import os
import pytest
from unittest.mock import MagicMock

from core.llm_client import get_default_model, tier_manager, GeminiTier
from core import llm_client
from core import phase2_meta


@pytest.fixture(autouse=True)
def _restore_global_state():
    """DEFAULT_MODEL_RESUME env var と tier_manager の状態をテスト間でリークさせない。"""
    original_env = os.environ.get("DEFAULT_MODEL_RESUME")
    original_tier = tier_manager.current_tier
    original_downgraded = tier_manager.was_downgraded
    yield
    if original_env is None:
        os.environ.pop("DEFAULT_MODEL_RESUME", None)
    else:
        os.environ["DEFAULT_MODEL_RESUME"] = original_env
    tier_manager.current_tier = original_tier
    tier_manager.was_downgraded = original_downgraded


def test_get_default_model_resume_uses_coreprompts_key(monkeypatch):
    """coreprompts.json の DEFAULT_MODEL_RESUME が設定されていれば、それを返す。"""
    os.environ.pop("DEFAULT_MODEL_RESUME", None)
    fake_prompts = {
        "DEFAULT_MODEL": "gemini-3.5-flash",
        "DEFAULT_MODEL_FREE": "gemini-3.1-flash-lite",
        "DEFAULT_MODEL_RESUME": "gemini-resume-special",
    }
    monkeypatch.setattr(llm_client, "_get_prompts", lambda: fake_prompts)

    assert get_default_model("resume") == "gemini-resume-special"


def test_get_default_model_resume_env_var_takes_precedence(monkeypatch):
    """環境変数 DEFAULT_MODEL_RESUME は coreprompts.json のキーより優先される。"""
    fake_prompts = {
        "DEFAULT_MODEL": "gemini-3.5-flash",
        "DEFAULT_MODEL_FREE": "gemini-3.1-flash-lite",
        "DEFAULT_MODEL_RESUME": "gemini-resume-from-json",
    }
    monkeypatch.setattr(llm_client, "_get_prompts", lambda: fake_prompts)
    os.environ["DEFAULT_MODEL_RESUME"] = "gemini-resume-from-env"

    assert get_default_model("resume") == "gemini-resume-from-env"


def test_get_default_model_resume_falls_back_to_tier_aware_default_free(monkeypatch):
    """DEFAULT_MODEL_RESUME が env にも JSON にも無い場合、FREE tier なら lite モデルを返す。"""
    os.environ.pop("DEFAULT_MODEL_RESUME", None)
    fake_prompts = {
        "DEFAULT_MODEL": "gemini-3.5-flash",
        "DEFAULT_MODEL_FREE": "gemini-3.1-flash-lite",
        "DEFAULT_MODEL_RESUME": "",
    }
    monkeypatch.setattr(llm_client, "_get_prompts", lambda: fake_prompts)
    tier_manager.set_tier(GeminiTier.FREE)

    assert get_default_model("resume") == "gemini-3.1-flash-lite"


def test_get_default_model_resume_falls_back_to_tier_aware_default_paid(monkeypatch):
    """DEFAULT_MODEL_RESUME が env にも JSON にも無い場合、PAID tier なら DEFAULT_MODEL を返す。"""
    os.environ.pop("DEFAULT_MODEL_RESUME", None)
    fake_prompts = {
        "DEFAULT_MODEL": "gemini-3.5-flash",
        "DEFAULT_MODEL_FREE": "gemini-3.1-flash-lite",
        "DEFAULT_MODEL_RESUME": "",
    }
    monkeypatch.setattr(llm_client, "_get_prompts", lambda: fake_prompts)
    tier_manager.set_tier(GeminiTier.PAID)

    assert get_default_model("resume") == "gemini-3.5-flash"


def test_get_default_model_resume_missing_key_is_backward_compatible(monkeypatch):
    """DEFAULT_MODEL_RESUME キー自体が無くても KeyError にならず、tier 追従にフォールバックする。"""
    os.environ.pop("DEFAULT_MODEL_RESUME", None)
    fake_prompts = {
        "DEFAULT_MODEL": "gemini-3.5-flash",
        "DEFAULT_MODEL_FREE": "gemini-3.1-flash-lite",
        # DEFAULT_MODEL_RESUME キーなし
    }
    monkeypatch.setattr(llm_client, "_get_prompts", lambda: fake_prompts)
    tier_manager.set_tier(GeminiTier.PAID)

    assert get_default_model("resume") == "gemini-3.5-flash"


def test_generate_resume_passes_resume_routed_model_to_call_gemini(monkeypatch):
    """generate_resume() は model=None のとき、resume 用にルーティングされたモデルを
    call_gemini に渡す（明示的な --model 指定がない限り）。"""
    os.environ["DEFAULT_MODEL_RESUME"] = "gemini-3.5-flash"

    captured = {}

    def fake_call_gemini(*args, **kwargs):
        captured["model"] = kwargs.get("model")
        return "生成されたレジュメ"

    monkeypatch.setattr(phase2_meta, "call_gemini", fake_call_gemini)

    result = phase2_meta.generate_resume("dummy source text", model=None)

    assert result == "生成されたレジュメ"
    assert captured["model"] == "gemini-3.5-flash"


def test_generate_resume_explicit_model_overrides_resume_routing(monkeypatch):
    """呼び出し側が明示的に model を渡した場合はそちらが優先される。"""
    os.environ["DEFAULT_MODEL_RESUME"] = "gemini-3.5-flash"

    captured = {}

    def fake_call_gemini(*args, **kwargs):
        captured["model"] = kwargs.get("model")
        return "生成されたレジュメ"

    monkeypatch.setattr(phase2_meta, "call_gemini", fake_call_gemini)

    phase2_meta.generate_resume("dummy source text", model="explicit-override-model")

    assert captured["model"] == "explicit-override-model"
