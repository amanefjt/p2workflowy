from core.engine.p4_translate.term_layer import TermEntry, build_term_layer, format_term_layer


def test_local_only():
    kw = [{"en": "displace", "ja": "ずらす"}]
    entries = build_term_layer(kw, [])
    assert entries == [TermEntry("displace", "ずらす", "local")]


def test_csv_ja_overrides_local_ja():
    kw = [{"en": "agency", "ja": "エージェンシー"}]
    csv = [{"en": "agency", "ja": "行為主体性"}]
    entries = build_term_layer(kw, csv)
    e = {t.en: t for t in entries}["agency"]
    assert e.ja == "行為主体性"          # 訳語は CSV 優先


def test_csv_empty_ja_does_not_override_local():
    kw = [{"en": "ethos", "ja": "エートス"}]
    csv = [{"en": "ethos", "ja": ""}]
    entries = build_term_layer(kw, csv)
    e = {t.en: t for t in entries}["ethos"]
    assert e.ja == "エートス"             # CSV ja が空なら上書きしない


def test_csv_only_entry_added():
    entries = build_term_layer([], [{"en": "habitus", "ja": "ハビトゥス"}])
    assert entries == [TermEntry("habitus", "ハビトゥス", "glossary")]


def test_dedup_case_insensitive():
    kw = [{"en": "Agency", "ja": "行為主体"}]
    csv = [{"en": "agency", "ja": "行為主体性"}]
    entries = build_term_layer(kw, csv)
    assert len(entries) == 1
    assert entries[0].ja == "行為主体性"


def test_blank_and_missing_en_skipped():
    kw = [{"en": "", "ja": "x"}, {"ja": "y"}]
    assert build_term_layer(kw, []) == []


def test_none_inputs_safe():
    assert build_term_layer(None, None) == []


def test_format_empty_returns_empty():
    assert format_term_layer([]) == ""


def test_format_renders_en_ja_lines():
    entries = [
        TermEntry("plain", "ふつう", "local"),
        TermEntry("displace", "転位", "local"),
    ]
    out = format_term_layer(entries)
    assert "# 用語集 (Glossary)" in out
    assert "- displace → 転位" in out
    assert "- plain → ふつう" in out
