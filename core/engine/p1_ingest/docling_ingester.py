"""
Docling ベースの PDF → RawChunk 変換。
デジタルPDF（テキスト埋め込み済み）に対して VLM OCR の代わりに使用する。
"""

import re
from typing import List

from ...models import RawChunk
from ...config import print_log

# Docling が検出した見出しのうち除外するパターン
_SKIP_HEADING_PATTERNS = re.compile(
    r"^(references|bibliography|acknowledgements?|acknowledgments?|"
    r"funding|declaration|notes?|appendix|index|glossary)\b",
    re.IGNORECASE,
)

# PUA (Private Use Area) 文字 — 埋め込みフォントのグリフ置換で発生
_PUA_RE = re.compile(r"[-]")


def _clean_pua(text: str) -> str:
    """PUA文字を除去してテキストをクリーンアップ。"""
    return _PUA_RE.sub("", text).strip()


def _is_skip_heading(text: str) -> bool:
    return bool(_SKIP_HEADING_PATTERNS.match(text.strip()))


def docling_pdf_to_chunks(pdf_path: str) -> List[RawChunk]:
    """
    Docling で PDF を処理し RawChunk リストを返す。

    見出し(section_header) → role="h1" or "h2"
    本文(text/paragraph) → role="p"
    REFERENCES/NOTES 等のセクション以降はスキップ。
    """
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        raise ImportError("docling がインストールされていません: pip install docling")

    print_log("  [Docling] PDF を変換中...")
    conv = DocumentConverter()
    result = conv.convert(pdf_path)
    doc = result.document

    chunks: List[RawChunk] = []
    idx = 0
    skip_rest = False  # REFERENCES 等以降をスキップするフラグ

    for item, level in doc.iterate_items():
        label = getattr(item, "label", None)
        raw_text = getattr(item, "text", "") or ""
        text = _clean_pua(raw_text)

        if not text:
            continue

        if label == "section_header":
            if _is_skip_heading(text):
                skip_rest = True
            if skip_rest:
                continue
            role = "h1" if level == 1 else "h2"
            chunks.append(RawChunk(id=f"chunk_{idx}", text=text, role=role, seq_index=float(idx)))
            idx += 1

        elif label in ("text", "paragraph", "list_item") and not skip_rest:
            chunks.append(RawChunk(id=f"chunk_{idx}", text=text, role="p", seq_index=float(idx)))
            idx += 1

    print_log(f"  [Docling] {len(chunks)} チャンクを生成（PUA除去・付属節スキップ済み）。")
    return chunks


def is_docling_viable(pdf_path: str) -> bool:
    """
    Docling ルートが使えるか判定する。
    - 埋め込みテキストがない（スキャンPDF）→ False
    - PUA文字比率が高い（スキャンOCR由来）→ False
    - 平均単語長が異常に短い（OCR荒れ）→ False
    """
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(pdf_path)
        sample = ""
        for i in range(min(5, len(doc))):
            sample += doc[i].get_text()
        doc.close()
    except Exception:
        return False

    if not sample.strip():
        return False  # テキストなし = スキャンPDF

    # PUA文字比率チェック（5%超 → スキャンOCRの疑い）
    pua_count = sum(1 for c in sample if "" <= c <= "")
    if pua_count > len(sample) * 0.05:
        return False

    # ゴミ文字チェック
    garbage = sample.count("�") + sample.count("\x00")
    if garbage > len(sample) * 0.05:
        return False

    # 平均単語長チェック（2文字未満 → OCR荒れの疑い）
    words = [w for w in sample.split() if w.isalpha()]
    if words and (sum(len(w) for w in words) / len(words)) < 2.0:
        return False

    return True
