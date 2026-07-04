"""
heading_matcher（旧 phase3_structure 内の見出し処理）の移設後検証。

旧 test_heading_detector.py（孤児モジュール用）の観点を、本番実体である
engine/p3_structure/heading_matcher の関数群に対して書き直したもの。
アサーション値はすべて現行実装の実挙動に一致させてある（挙動固定が目的）。
"""

from core.engine.p3_structure.heading_matcher import (
    normalize_heading,
    is_excluded_heading,
    match_heading,
    extract_headings_from_resume,
    merge_role_headings,
)


def test_normalize_strips_chapter_numbering():
    """章番号・節番号・ローマ数字の接頭辞を剥離する。"""
    assert normalize_heading("1. Introduction") == "introduction"
    assert normalize_heading("Chapter 3: Methods") == "methods"
    assert normalize_heading("III. Comparisons") == "comparisons"
    assert normalize_heading("2.1 Background") == "background"


def test_normalize_keeps_numeric_only_title():
    """正規化で2文字未満になる場合は数字を活かすフォールバックが効く。"""
    # "3.1" は番号剥離で空になるため、記号だけ除いた "31" を返す
    assert normalize_heading("3.1") == "31"


def test_normalize_case_and_symbols():
    """記号除去・小文字化・空白正規化。"""
    assert normalize_heading("RESULTS & Discussion!") == "results discussion"
    assert normalize_heading("References") == "references"


def test_is_excluded_heading():
    """除外キーワードを部分一致（小文字）で判定する。"""
    assert is_excluded_heading("References", ["references", "bibliography"]) is True
    assert is_excluded_heading("Introduction", ["references", "bibliography"]) is False


def test_match_heading_exact():
    """見出しと完全一致する行は、見出しと空の残余を返す。"""
    assert match_heading("Introduction", ["Introduction"]) == ("Introduction", "")


def test_match_heading_long_heading_allows_trailing_body():
    """20文字以上の見出しは、後続本文が連結していても分離できる。"""
    head, remaining = match_heading(
        "Materials and Methods used in this study were",
        ["Materials and Methods"],
    )
    assert head == "Materials and Methods"
    assert remaining == "used in this study were"


def test_match_heading_misfire_filter_rejects_short_heading_in_long_line():
    """短い見出しが長い行の冒頭に過ぎない場合は誤爆として却下する。"""
    # "Methods"（短い見出し）に長い本文が続く行はマッチさせない
    assert match_heading("Methods We did many things here", ["Methods"]) is None


def test_extract_headings_from_resume_brackets_and_markdown():
    """Markdown 見出しから、英語ブラケット見出しと通常見出しを抽出する。"""
    resume = "# Introduction\n## [Background]\n本文テキスト"
    assert extract_headings_from_resume(resume) == ["Introduction", "Background"]


def test_merge_role_headings_appends_missing_heading():
    """レジュメに無い role 由来の見出し（例: Conclusion）は末尾に追加される（I-8 回帰防止）。"""
    assert merge_role_headings(["Conclusion"], ["Introduction", "Discussion"]) == [
        "Introduction", "Discussion", "Conclusion",
    ]


def test_merge_role_headings_dedupes_against_resume_variants():
    """番号プレフィックス・大小文字が違っても正規化一致すれば重複追加しない。"""
    assert merge_role_headings(["3. conclusion"], ["Introduction", "Conclusion"]) == [
        "Introduction", "Conclusion",
    ]


def test_merge_role_headings_empty_role_list_returns_resume_unchanged():
    """role 由来の見出しが空ならレジュメのリストがそのまま返る。"""
    assert merge_role_headings([], ["Introduction", "Conclusion"]) == [
        "Introduction", "Conclusion",
    ]


def test_merge_role_headings_preserves_resume_order_first():
    """出力順序: レジュメ由来が先頭、role 由来のみの見出しは後方に追記される。"""
    result = merge_role_headings(["Background", "Conclusion"], ["Introduction"])
    assert result == ["Introduction", "Background", "Conclusion"]
