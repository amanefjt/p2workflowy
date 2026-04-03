"""
p2workflowy V2 Phase 1: Ingest & Preprocess (Golden Rewrite Edition)
物理データ主権に基づいた整形 (V3) と、従来のクレンジングユーティリティ。
"""

import re
import csv
import statistics
from pathlib import Path
from typing import List, Any, Optional

import wordninja

from .models import RawChunk, save_chunks_to_json
from .config import load_glossary_csv, print_log, PROJECT_ROOT
from .text_utils import _SENTENCE_END_RE, _TRAILING_WORDS
from .engine.p1_ingest.formatter import Formatter
from .pdf_ingester import run_pdf_ingestion_v3


# ============================================================
# ユーティリティ: クレンジング
# ============================================================

def normalize_line_endings(text: str) -> str:
    """改行コードの正規化: \r\n / \r → \n"""
    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")
    return text


def rejoin_hyphenated_words(text: str) -> str:
    """行またぎのハイフン結合: 'word-\nword' → 'wordword'"""
    return re.sub(r"(\w)-\n(\w)", r"\1\2", text)


def filter_paragraphs(paragraphs: List[str]) -> List[str]:
    """ノイズ（ページ番号等）の簡単な除去。"""
    filtered = []
    for p in paragraphs:
        p_strip = p.strip()
        if not p_strip:
            continue
        # 数字のみ（ページ番号等）は除外
        if re.match(r"^\d+$", p_strip):
            continue
        filtered.append(p_strip)
    return filtered


_LONG_WORD_RE = re.compile(r"\b[A-Za-z]{15,}\b")

def glossary_aware_word_split(text: str, glossary_keys: List[str]) -> str:
    """
    用語集の単語を保護しつつ、長い癒着単語（WordNinjaが必要なもの）を分割する。
    """
    if not text:
        return ""

    # ステップ 1: 用語集の単語を一時的にプレースホルダーに置換
    placeholders = {}
    sorted_keys = sorted(glossary_keys, key=len, reverse=True)

    for idx, key in enumerate(sorted_keys):
        placeholder = f"__GLOS_{idx}__"
        if key in text:
            text = text.replace(key, placeholder)
            placeholders[placeholder] = key

    # ステップ 2: 癒着単語を wordninja で分割
    def _split_long_word(match: re.Match) -> str:
        word = match.group(0)
        if "__GLOS" in word:
            return word
        split_result = wordninja.split(word)
        if len(split_result) > 1:
            return " ".join(split_result)
        return word

    text = _LONG_WORD_RE.sub(_split_long_word, text)

    # ステップ 3: 復元
    for placeholder, original in placeholders.items():
        text = text.replace(placeholder, original)

    return text


# ============================================================
# メイン実行関数 (V3 & V2)
# ============================================================

def run_phase1_v3(
    pdf_path: str,
    state_path: str | Path,
    api_key: str | None = None,
    save_state: bool = True,
    state: "Any" = None,
    pdf_mode: str = "hybrid",
    model: str | None = None,
    is_book: bool = False,
    heavy_ocr: bool = False,
    max_pages: Optional[int] = None, # 追加
) -> List[RawChunk]:
    """
    Phase 1 V3 (Golden Rewrite): PDF から物理情報を抽出し、Formatter を用いて段落を復元する。
    """
    print_log(f"--- Phase 1 V3: Preprocessor (Physical Sovereignty) ---")
    
    # 1. PDF 読み込み & 物理要素抽出
    elements = run_pdf_ingestion_v3(
        pdf_path, api_key=api_key, state=state, 
        pdf_mode=pdf_mode, model=model, 
        is_book=is_book, heavy_ocr=heavy_ocr,
        max_pages=max_pages # 追加
    )
    
    # 2. チャンク化 (VLM-First Logic or Physical-Smart-Unwrap)
    if elements and elements[0].get("role") == "vlm_page_source":
        # VLM 論理抽出ルート
        chunks = Formatter.logical_split(elements)
        print_log(f"  [Phase 1 V3] VLM 論理ブロックから {len(chunks)} チャンクを生成しました。")
    else:
        # 物理段落結合ルート (Legacy/Book Mode Hybrid)
        chunks = Formatter.smart_unwrap(elements)
        print_log(f"  [Phase 1 V3] 物理抽出により {len(chunks)} チャンクを生成しました。")
    
    # 3. ノイズ除去・フィルタリング
    # (将来的に Formatter に統合するが、一旦既存の簡易フィルタを通す)
    for c in chunks:
        c.text = c.text.strip()
    
    # State 保存
    if save_state:
        save_chunks_to_json(chunks, str(state_path))
        print_log(f"  [Phase 1 V3] State 保存: {state_path}")

    return chunks


def run_phase1(
    input_path: str,
    state_path: str | Path,
    glossary_path: str | None = None,
    save_state: bool = True,
    state: "Any" = None,
) -> List[RawChunk]:
    """
    Legacy run_phase1 for compatibility (non-PDF inputs).
    """
    path = Path(input_path)
    raw_text = path.read_text(encoding="utf-8")
    
    text = normalize_line_endings(raw_text)
    text = rejoin_hyphenated_words(text)
    
    # シンプルな改行区切り
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    paragraphs = filter_paragraphs(paragraphs)
    
    chunks: List[RawChunk] = []
    for idx, para in enumerate(paragraphs):
        chunk = RawChunk(id=idx, text=para, seq_index=float(idx))
        chunks.append(chunk)

    if save_state:
        save_chunks_to_json(chunks, str(state_path))
    return chunks
