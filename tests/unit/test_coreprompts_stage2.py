from core.config import load_coreprompts


def test_keyword_prompt_has_stage2_markers():
    p = load_coreprompts()["KEYWORD_EXTRACTION_PROMPT"]
    # プレースホルダ維持
    assert "{expertise}" in p and "{text}" in p
    # 特殊用法（平準化対策）の抽出指示
    assert "特殊" in p
    # グラウンディング（定義できない語は空）
    assert '""' in p or "空" in p
    # 件数上限
    assert "30" in p
    # 出力フォーマット維持
    assert '"definition"' in p


def test_hybrid_defaults_are_baked_in():
    p = load_coreprompts()
    assert p["DEFAULT_MODEL"] == "gemini-3.1-flash-lite"        # 翻訳等は lite
    assert p["DEFAULT_MODEL_RESUME"] == "gemini-3.5-flash"      # レジュメのみ強モデル
