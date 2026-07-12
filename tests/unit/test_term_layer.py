from core.engine.p4_translate.term_layer import TermEntry, build_term_layer


def test_local_only():
    kw = [{"en": "displace", "ja": "ずらす", "definition": "秩序からの転位"}]
    entries = build_term_layer(kw, [])
    assert entries == [TermEntry("displace", "ずらす", "秩序からの転位", "local")]


def test_csv_ja_overrides_local_ja():
    kw = [{"en": "agency", "ja": "エージェンシー", "definition": "行為の力"}]
    csv = [{"en": "agency", "ja": "行為主体性", "definition": ""}]
    entries = build_term_layer(kw, csv)
    e = {t.en: t for t in entries}["agency"]
    assert e.ja == "行為主体性"          # 訳語は CSV 優先
    assert e.definition == "行為の力"     # 定義は local を保持


def test_definition_filled_from_csv_when_local_empty():
    kw = [{"en": "ethos", "ja": "エートス", "definition": ""}]
    csv = [{"en": "ethos", "ja": "", "definition": "書籍全体での含意"}]
    entries = build_term_layer(kw, csv)
    e = {t.en: t for t in entries}["ethos"]
    assert e.ja == "エートス"             # CSV ja が空なら上書きしない
    assert e.definition == "書籍全体での含意"  # local が空なら CSV 定義で補完


def test_local_definition_wins_over_csv_definition():
    kw = [{"en": "field", "ja": "フィールド", "definition": "章での特定用法"}]
    csv = [{"en": "field", "ja": "", "definition": "書籍レベルの一般定義"}]
    entries = build_term_layer(kw, csv)
    assert {t.en: t for t in entries}["field"].definition == "章での特定用法"


def test_csv_only_entry_added():
    entries = build_term_layer([], [{"en": "habitus", "ja": "ハビトゥス", "definition": "d"}])
    assert entries == [TermEntry("habitus", "ハビトゥス", "d", "glossary")]


def test_dedup_case_insensitive():
    kw = [{"en": "Agency", "ja": "行為主体", "definition": "x"}]
    csv = [{"en": "agency", "ja": "行為主体性", "definition": ""}]
    entries = build_term_layer(kw, csv)
    assert len(entries) == 1
    assert entries[0].ja == "行為主体性"


def test_blank_and_missing_en_skipped():
    kw = [{"en": "", "ja": "x", "definition": ""}, {"ja": "y"}]
    assert build_term_layer(kw, []) == []


def test_none_inputs_safe():
    assert build_term_layer(None, None) == []


def test_format_empty_returns_empty():
    from core.engine.p4_translate.term_layer import format_term_layer
    assert format_term_layer([]) == ""


def test_format_with_and_without_definition_ordering():
    from core.engine.p4_translate.term_layer import format_term_layer
    entries = [
        TermEntry("plain", "ふつう", "", "local"),           # 定義なし
        TermEntry("displace", "転位", "秩序からずらす", "local"),  # 定義あり
    ]
    out = format_term_layer(entries)
    assert "# 用語集 (Glossary)" in out
    assert "- displace → 転位：秩序からずらす" in out
    assert "- plain → ふつう" in out
    # 定義あり（displace）が定義なし（plain）より前
    assert out.index("displace") < out.index("plain")
    # 定義なし行に全角コロンの定義区切りが付かない
    assert "- plain → ふつう：" not in out
