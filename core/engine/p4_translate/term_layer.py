"""翻訳コンテキストの「② 術語＝統合用語レイヤー」。

本文抽出キーワードと glossary CSV を単一の en→ja 対応レイヤーに統合する。

注: 当初（Stage 2）は語の「定義文」も翻訳プロンプトに注入していたが、NST/AL 2 本の
A/B（定義あり vs なし・他条件固定）で定義注入に翻訳品質上の効果が確認できず（むしろ
過剰適合・忠実性低下の副作用）、定義レイヤーは撤去した。訳語対応（en→ja）は両版で機能
していたため維持する。詳細は
docs/superpowers/specs/2026-07-11-translation-context-stage2-term-layer-design.md（結果節）参照。
"""
from dataclasses import dataclass


@dataclass
class TermEntry:
    en: str
    ja: str
    source: str = "local"   # "local"（本文抽出）| "glossary"（glossary CSV）


def build_term_layer(keywords_data, glossary_entries):
    """本文抽出（keywords_data）と glossary CSV（glossary_entries）を統合する。

    - dedup キー: en.lower()
    - 訳語 ja: glossary CSV 優先（ユーザー/書籍が権威）
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
            source="local",
        )

    # 2. glossary CSV を重ねる（ja は CSV 優先）
    for g in glossary_entries or []:
        en = (g.get("en") or "").strip()
        if not en:
            continue
        key = en.lower()
        g_ja = (g.get("ja") or "").strip()
        if key in merged:
            if g_ja:
                merged[key].ja = g_ja
        else:
            merged[key] = TermEntry(en=en, ja=g_ja, source="glossary")

    return list(merged.values())


def format_term_layer(entries) -> str:
    """用語レイヤーを翻訳プロンプトの <glossary> 用に整形する（en→ja 対応表）。"""
    if not entries:
        return ""
    lines = [
        "# 用語集 (Glossary)",
        "指定された日本語訳を優先的に使用してください。",
    ]
    for e in entries:
        lines.append(f"- {e.en} → {e.ja}")
    return "\n".join(lines)
