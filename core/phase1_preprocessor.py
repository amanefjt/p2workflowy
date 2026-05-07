"""
p2workflowy Phase 1: Ingest & Preprocess
PDF（VLM/物理）とプレーンテキストの両入力に対応する統合前処理エンジン。
"""

import re
from pathlib import Path
from typing import List, Any, Optional

import wordninja

from .models import RawChunk, save_chunks_to_json
from .config import load_glossary_csv, print_log, PROJECT_ROOT
from .text_utils import _SENTENCE_END_RE, _TRAILING_WORDS
from .engine.p1_ingest.formatter import Formatter
from .engine.p1_ingest.docling_ingester import docling_pdf_to_chunks, is_docling_viable
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
        if re.match(r"^\d+$", p_strip):
            continue
        filtered.append(p_strip)
    return filtered


_LONG_WORD_RE = re.compile(r"\b[A-Za-z]{15,}\b")

def glossary_aware_word_split(text: str, glossary_keys: List[str]) -> str:
    """用語集の単語を保護しつつ、長い癒着単語を wordninja で分割する。"""
    if not text:
        return ""

    placeholders = {}
    sorted_keys = sorted(glossary_keys, key=len, reverse=True)

    for idx, key in enumerate(sorted_keys):
        placeholder = f"__GLOS_{idx}__"
        if key in text:
            text = text.replace(key, placeholder)
            placeholders[placeholder] = key

    def _split_long_word(match: re.Match) -> str:
        word = match.group(0)
        if "__GLOS" in word:
            return word
        split_result = wordninja.split(word)
        if len(split_result) > 1:
            return " ".join(split_result)
        return word

    text = _LONG_WORD_RE.sub(_split_long_word, text)

    for placeholder, original in placeholders.items():
        text = text.replace(placeholder, original)

    return text


# ============================================================
# 統合エントリーポイント
# ============================================================

def run_phase1_unified(
    input_path: str,
    state_path: str | Path,
    api_key: str | None = None,
    save_state: bool = True,
    state: "Any" = None,
    pdf_mode: str = "hybrid",
    model: str | None = None,
    is_book: bool = False,
    heavy_ocr: bool = False,
    max_pages: Optional[int] = None,
) -> List[RawChunk]:
    """
    Phase 1 統合エントリーポイント: .pdf なら PDF ルート、それ以外はテキストルートに振り分ける。
    両ルートとも role 付き RawChunk[] を出力するという契約を守る。
    """
    is_text = not str(input_path).lower().endswith(".pdf")

    if is_text:
        return _run_phase1_text(
            input_path, state_path,
            api_key=api_key, state=state, save_state=save_state, model=model,
        )
    else:
        return _run_phase1_pdf(
            input_path, state_path,
            api_key=api_key, state=state, save_state=save_state,
            pdf_mode=pdf_mode, model=model,
            is_book=is_book, heavy_ocr=heavy_ocr, max_pages=max_pages,
        )


# ============================================================
# PDF ルート
# ============================================================

def _run_phase1_pdf(
    pdf_path: str,
    state_path: str | Path,
    api_key: str | None = None,
    save_state: bool = True,
    state: "Any" = None,
    pdf_mode: str = "hybrid",
    model: str | None = None,
    is_book: bool = False,
    heavy_ocr: bool = False,
    max_pages: Optional[int] = None,
) -> List[RawChunk]:
    """PDF から RawChunk を生成する。
    デジタルPDF → Docling ルート（低コスト）
    スキャンPDF / Docling不可 → VLM ルート（フォールバック）
    """
    print_log(f"--- Phase 1 [PDF]: Preprocessor ---")

    # Docling ルート: デジタルPDFかつ max_pages 未指定（部分処理モードでない）
    if max_pages is None and is_docling_viable(pdf_path):
        try:
            chunks = docling_pdf_to_chunks(pdf_path)
            if chunks:
                print_log(f"  [Phase 1 PDF] Docling ルート: {len(chunks)} チャンクを生成。")
                for c in chunks:
                    c.text = c.text.strip()
                if save_state:
                    save_chunks_to_json(chunks, str(state_path))
                    print_log(f"  [Phase 1 PDF] State 保存: {state_path}")
                return chunks
            print_log("  [Phase 1 PDF] Docling が空を返したため VLM にフォールバック。")
        except Exception as e:
            print_log(f"  [Phase 1 PDF] Docling エラー ({e})、VLM にフォールバック。")

    # VLM ルート（フォールバック）
    print_log("  [Phase 1 PDF] VLM ルートで処理します。")
    elements = run_pdf_ingestion_v3(
        pdf_path, api_key=api_key, state=state,
        pdf_mode=pdf_mode, model=model,
        is_book=is_book, heavy_ocr=heavy_ocr,
        max_pages=max_pages,
    )

    if elements and elements[0].get("role") == "vlm_page_source":
        chunks = Formatter.logical_split(elements)
        print_log(f"  [Phase 1 PDF] VLM ルート: {len(chunks)} チャンクを生成。")
    else:
        chunks = Formatter.smart_unwrap(elements)
        print_log(f"  [Phase 1 PDF] 物理ルート: {len(chunks)} チャンクを生成。")

    for c in chunks:
        c.text = c.text.strip()

    if save_state:
        save_chunks_to_json(chunks, str(state_path))
        print_log(f"  [Phase 1 PDF] State 保存: {state_path}")

    return chunks


# ============================================================
# テキストルート
# ============================================================

def _run_phase1_text(
    input_path: str,
    state_path: str | Path,
    api_key: str | None = None,
    save_state: bool = True,
    state: "Any" = None,
    model: str | None = None,
) -> List[RawChunk]:
    """
    プレーンテキスト（Acrobat 抽出等）を処理する。
    VLM 不使用のため画像トークンコストゼロ。
    LLM による構造認識（TextStructureExtractor）で role を付与する。
    """
    print_log(f"--- Phase 1 [Text]: Preprocessor ---")

    path = Path(input_path)
    raw_text = path.read_text(encoding="utf-8")

    text = normalize_line_endings(raw_text)
    text = rejoin_hyphenated_words(text)
    # Acrobat 抽出テキストの語間スペース修正: 文末記号(.!?)の直後に大文字が続くケースのみ
    text = re.sub(r'([.!?])([A-Z][a-z])', r'\1 \2', text)

    # 分割方式の自動判定: \n\n 分割と \n 分割を比較して適切な方を選ぶ。
    # Acrobat 抽出テキストは 1行=1段落 の形式（\n のみ）が多いが、
    # ファイルによっては数個の \n\n が存在する場合もある。
    # \n\n 分割後のチャンク数が \n 分割後の 1/10 未満 かつ 5未満 の場合は
    # \n\n が段落区切りでないと判断して \n 分割を使う。
    nn_paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    n_paras = [p.strip() for p in text.split("\n") if p.strip()]
    if len(nn_paras) < max(5, len(n_paras) // 10):
        paragraphs = n_paras
    else:
        paragraphs = nn_paras
    paragraphs = filter_paragraphs(paragraphs)

    chunks: List[RawChunk] = []
    for idx, para in enumerate(paragraphs):
        chunks.append(RawChunk(id=str(idx), text=para, seq_index=float(idx)))

    print_log(f"  [Phase 1 Text] {len(chunks)} チャンクを生成。")

    # LLM による構造認識: 見出しを抽出して role を付与
    if api_key and chunks:
        try:
            from .engine.p1_ingest.text_structure_extractor import TextStructureExtractor
            from .llm_client import get_default_model
            extractor = TextStructureExtractor(
                api_key=api_key,
                model=model or get_default_model("free"),
            )
            headings = extractor.extract_headings(chunks)
            chunks = extractor.assign_roles(chunks, headings)
        except Exception as e:
            print_log(f"  [Phase 1 Text] ⚠️ 構造認識をスキップ（{e}）。全チャンク role='p' で続行。")
    else:
        print_log("  [Phase 1 Text] API キーなし、または空入力のため構造認識をスキップ。")

    if save_state:
        save_chunks_to_json(chunks, str(state_path))
        print_log(f"  [Phase 1 Text] State 保存: {state_path}")

    return chunks
