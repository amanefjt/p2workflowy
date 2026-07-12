"""翻訳コンテキストの「② 術語＝統合用語レイヤー」。

glossary（訳語対応）と local_definitions（本文抽出の術語定義）を単一の
構造化レイヤーに統合する。詳細は
docs/superpowers/specs/2026-07-11-translation-context-stage2-term-layer-design.md 参照。
"""
from dataclasses import dataclass


@dataclass
class TermEntry:
    en: str
    ja: str
    definition: str = ""
    source: str = "local"   # "local"（本文抽出）| "glossary"（glossary CSV）


def build_term_layer(keywords_data, glossary_entries):
    """本文抽出（keywords_data）と glossary CSV（glossary_entries）を統合する。

    - dedup キー: en.lower()
    - 訳語 ja: glossary CSV 優先（ユーザー/書籍が権威）
    - 定義 definition: local（本文抽出）優先。local が空なら CSV 定義で補完。
    """
    merged: dict[str, TermEntry] = {}

    # 1. 本文抽出を基層に（source=local）
    for kw in keywords_data or []:
        en = (kw.get("en") or "").strip()
        if not en:
            continue
        merged[en.lower()] = TermEntry(
            en=en,
            ja=(kw.get("ja") or "").strip(),
            definition=(kw.get("definition") or "").strip(),
            source="local",
        )

    # 2. glossary CSV を重ねる（ja は CSV 優先、definition は local 優先で空なら補完）
    for g in glossary_entries or []:
        en = (g.get("en") or "").strip()
        if not en:
            continue
        key = en.lower()
        g_ja = (g.get("ja") or "").strip()
        g_def = (g.get("definition") or "").strip()
        if key in merged:
            e = merged[key]
            if g_ja:
                e.ja = g_ja
            if not e.definition and g_def:
                e.definition = g_def
        else:
            merged[key] = TermEntry(en=en, ja=g_ja, definition=g_def, source="glossary")

    return list(merged.values())


def format_term_layer(entries) -> str:
    """用語レイヤーを翻訳プロンプトの <glossary> 用に整形する。

    定義付きの語（＝特殊用法・高価値）を先頭に、定義なしを後に並べる。
    """
    if not entries:
        return ""
    with_def = [e for e in entries if e.definition]
    without_def = [e for e in entries if not e.definition]
    lines = [
        "# 用語集 (Glossary)",
        "指定された日本語訳を優先的に使用してください。定義が付された語は、"
        "この文献での特定の含意を示すため、訳語選択の際に踏まえてください。",
    ]
    for e in with_def:
        lines.append(f"- {e.en} → {e.ja}：{e.definition}")
    for e in without_def:
        lines.append(f"- {e.en} → {e.ja}")
    return "\n".join(lines)
