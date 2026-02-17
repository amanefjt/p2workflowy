# -*- coding: utf-8 -*-
"""
定数とプロンプトの定義
shared/prompts.json から読み込むように変更
"""
import json
from pathlib import Path

# プロジェクトルートディレクトリの取得
# src/constants.py -> src/ -> p2workflowy/
PROJECT_ROOT = Path(__file__).parent.parent
SHARED_PROMPTS_PATH = PROJECT_ROOT / "shared" / "prompts.json"

def load_prompts():
    """shared/prompts.json からプロンプト設定を読み込む"""
    try:
        with open(SHARED_PROMPTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: Failed to load shared prompts from {SHARED_PROMPTS_PATH}: {e}")
        return {}

_prompts = load_prompts()

# 定数として展開（これまでのコードとの互換性のため）
DEFAULT_MODEL = _prompts.get("DEFAULT_MODEL", "gemini-3-flash-preview")
MAX_TRANSLATION_CHUNK_SIZE = _prompts.get("MAX_TRANSLATION_CHUNK_SIZE", 4000)

STRUCTURING_WITH_HINT_PROMPT = _prompts.get("STRUCTURING_WITH_HINT_PROMPT", "")
SUMMARY_PROMPT = _prompts.get("SUMMARY_PROMPT", "")
TRANSLATION_PROMPT = _prompts.get("TRANSLATION_PROMPT", "")


EXCLUDE_SECTION_KEYWORDS = _prompts.get("EXCLUDE_SECTION_KEYWORDS", [])