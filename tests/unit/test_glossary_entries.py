import csv
from core.config import load_glossary_entries


def _write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerows(rows)


def test_reads_three_columns_ignores_third(tmp_path):
    p = tmp_path / "g.csv"
    _write_csv(p, [["en", "ja", "definition"],
                   ["displace", "転位させる", "確立した秩序からずらす意"]])
    entries = load_glossary_entries(p)
    assert entries == [{"en": "displace", "ja": "転位させる"}]


def test_two_column_csv(tmp_path):
    p = tmp_path / "g.csv"
    _write_csv(p, [["agency", "行為主体性"]])   # ヘッダーなし・2 列
    entries = load_glossary_entries(p)
    assert entries == [{"en": "agency", "ja": "行為主体性"}]


def test_missing_file_returns_empty(tmp_path):
    assert load_glossary_entries(tmp_path / "nope.csv") == []


def test_skips_header_and_blank_keys(tmp_path):
    p = tmp_path / "g.csv"
    _write_csv(p, [["term", "ja", "definition"],   # ヘッダー
                   ["", "空キー", "x"],             # 空キーは除外
                   ["ethos", "エートス", ""]])
    entries = load_glossary_entries(p)
    assert entries == [{"en": "ethos", "ja": "エートス"}]
