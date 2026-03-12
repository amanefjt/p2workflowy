"""
p2workflowy V2: Phase 0 (PDF Ingestion) - ハイブリッド方式
PyMuPDF による高速テキスト抽出をデフォルトとし、
特定条件のページのみ Gemini VLM OCR にフォールバックする。
"""

import asyncio
import re
import statistics
from pathlib import Path
from typing import Any, List

import fitz
from PIL import Image

from .llm_client import call_gemini_async
from .config import print_log

# ===== 設定値（調整しやすいよう外出し） =====
FOOTNOTE_FONT_RATIO = 0.60      # 本文中央値の60%以下 → 脚注とみなす
MIN_TEXT_CHARS = 100             # これ未満のテキスト量 → VLMフォールバック
HEADER_MARGIN_RATIO = 0.08      # ページ上部8%をノイズ除外
FOOTER_MARGIN_RATIO = 0.10      # ページ下部10%をノイズ除外
FOOTNOTE_AREA_RATIO = 0.80      # ページ下部20%に脚注検出
VLM_SEMAPHORE_LIMIT = 2         # VLM同時実行数の上限

# VLM用プロンプト（XMLタグ形式）
VLM_PROMPT = """<role>学術論文のデジタル化専門のシニア・エディター</role>
<task>
画像からノイズを除去し、高品質なテキストを再構築してください。
1. 文末(.!?)以外での改行は削除し、各段落は「改行を含まない単一の長い行」として出力すること。
2. 2段組み等の場合、論理的な順序で読み取ること。
3. 行末のハイフン分割は結合すること。
4. ヘッダー、フッター、ページ番号、ジャーナル名、図表内テキスト、ページ下部の脚注は絶対に抽出しないこと。
出力は純粋なテキストのみとし、マークダウン記法(```)で囲まないこと。
</task>"""


# ===== ページルーティング =====

def should_use_vlm(page: fitz.Page, page_num: int) -> bool:
    """ページを VLM で処理すべきかどうかを判定する。

    以下のいずれか1つでも満たすと True:
    1. 1ページ目（page_num == 0）
    2. テキスト量が MIN_TEXT_CHARS 未満
    3. 脚注が検出された
    """
    # 条件1: 1ページ目は無条件でVLM
    if page_num == 0:
        return True

    # 条件2: テキスト量不足
    raw_text = page.get_text()
    if len(raw_text.strip()) < MIN_TEXT_CHARS:
        return True

    # 条件3: 脚注の検出（フォントサイズベース）
    if _has_footnotes(page):
        return True

    return False


def _has_footnotes(page: fitz.Page) -> bool:
    """ページ下部に脚注が存在するかフォントサイズで判定する。"""
    page_height = page.rect.height

    # 全テキストspanのフォントサイズを収集
    text_dict = page.get_text("dict")
    font_sizes: List[float] = []
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:  # テキストブロックのみ
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if span.get("text", "").strip():
                    font_sizes.append(span["size"])

    # ガード: フォントサイズが取れなければ脚注なしとみなす
    if not font_sizes:
        return False

    median_size = statistics.median(font_sizes)
    footnote_threshold = median_size * FOOTNOTE_FONT_RATIO

    # ページ下部20%の領域に小さいフォントがあるか確認
    footnote_area_top = page_height * FOOTNOTE_AREA_RATIO
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if not span.get("text", "").strip():
                    continue
                # spanのy座標（bbox[1] = y0）
                span_y = span["bbox"][1]
                if span_y > footnote_area_top and span["size"] <= footnote_threshold:
                    return True

    return False


# 短行判定の閾値: ブロック幅の85%未満なら段落候補
SHORT_LINE_RATIO = 0.85

def extract_text_fast(page: fitz.Page) -> str:
    """PyMuPDFの dict 抽出による段落レベルのテキスト取得。
    ヘッダー/フッターを除外し、短行+文末判定で段落を分割する。
    """
    page_height = page.rect.height
    header_limit = page_height * HEADER_MARGIN_RATIO
    footer_limit = page_height * (1.0 - FOOTER_MARGIN_RATIO)

    text_dict = page.get_text("dict")
    # 読み順（y0, x0）でブロックをソート
    blocks = []
    for b in text_dict.get("blocks", []):
        if b.get("type") == 0:
            blocks.append(b)
    blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))

    paragraphs = []
    for block in blocks:
        block_bbox = block["bbox"]
        # ヘッダー/フッター除外
        if block_bbox[3] <= header_limit:
            continue
        if block_bbox[1] >= footer_limit:
            continue

        block_width = block_bbox[2] - block_bbox[0]
        if block_width <= 0:
            continue

        lines = block.get("lines", [])
        if not lines:
            continue

        current_paragraph_lines = []
        for line in lines:
            line_text = "".join(span.get("text", "") for span in line.get("spans", []))
            if not line_text.strip():
                if current_paragraph_lines:
                    paragraphs.append(_finalize_paragraph(current_paragraph_lines))
                    current_paragraph_lines = []
                continue

            current_paragraph_lines.append(line_text)

            # 短行判定 + 文末判定
            # 行幅がブロックの85%未満かつ、文末記号（. ! ? ) ] ” 」 等）で終わる場合のみ段落を区切る
            line_width = line["bbox"][2] - line["bbox"][0]
            is_short = (line_width / block_width < SHORT_LINE_RATIO)
            is_sentence_end = re.search(r"""[.!?\)\]\"'”。]\d*$""", line_text.strip())

            if is_short and is_sentence_end:
                paragraphs.append(_finalize_paragraph(current_paragraph_lines))
                current_paragraph_lines = []

        if current_paragraph_lines:
            paragraphs.append(_finalize_paragraph(current_paragraph_lines))

    return "\n\n".join(paragraphs)


def _finalize_paragraph(lines: list[str]) -> str:
    """行リストを1つの段落テキストに整形する。"""
    joined = " ".join(line.strip() for line in lines)
    # ハイフン分割の結合
    joined = re.sub(r'(\w)-\s+(\w)', r'\1\2', joined)
    return joined.strip()


# ===== VLMルート（Geminiフォールバック） =====

async def process_page_vlm(
    pdf_path: str,
    page_num: int,
    api_key: str | None,
    semaphore: asyncio.Semaphore,
    model: str | None = None,
) -> str:
    """1ページをGemini VLM OCRで処理して抽出テキストを返す。"""
    async with semaphore:
        doc = fitz.open(pdf_path)
        try:
            page = doc[page_num]

            # 200 DPI: Gemini の視覚認識に十分かつメモリ消費を抑える
            pix = page.get_pixmap(dpi=200)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        finally:
            doc.close()

        prompt = [img, VLM_PROMPT]

        try:
            result = await call_gemini_async(
                prompt=prompt,
                model=model or "gemini-3.1-flash-lite-preview",
                api_key=api_key,
                temperature=0.0,
                max_retries=3,
                retry_delay=5.0,
            )
            # Markdown Code Block の除去
            result = re.sub(r"^```[a-zA-Z]*\n", "", result)
            result = re.sub(r"\n```$", "", result)
            return result.strip()
        except Exception as e:
            print_log(f"  [PDF Ingester] ページ {page_num+1} のVLM処理に失敗: {e}")
            return ""
        finally:
            del img
            del pix


# ===== メインオーケストレーター =====

async def run_pdf_ingestion_async(
    pdf_path: str,
    api_key: str | None = None,
    state: Any = None,
    pdf_mode: str = "full_vlm",
    model: str | None = None,
) -> str:
    """PDFからテキストを抽出する。

    pdf_mode:
        - "full_vlm": 全ページをGemini VLMで処理（高精度、低速）
        - "hybrid": 高速抽出を基本としつつ、必要なページだけVLMを使用（実用精度、高速）
    """
    if state:
        state.update_status("PDF解析中...", 5)

    print_log(f"  [PDF Ingester] PDF読み込み開始: {pdf_path}")
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    print_log(f"  [PDF Ingester] 総ページ数: {total_pages}")

    # --- ルーティング判定 ---
    fast_pages: dict[int, str] = {}      # ページ番号 → 抽出テキスト
    vlm_page_nums: list[int] = []         # VLMに送るページ番号リスト
    for i in range(total_pages):
        page = doc[i]
        
        # モードによる分岐
        if pdf_mode == "full_vlm" or should_use_vlm(page, i):
            vlm_page_nums.append(i)
            if pdf_mode == "full_vlm":
                reason = "モード指定(full_vlm)"
            else:
                reason = "1ページ目" if i == 0 else "テキスト不足/脚注検出"
            print_log(f"  [PDF Ingester] ページ {i+1}/{total_pages}: VLM ({reason})")
        else:
            # 高速ルートで即座に抽出
            fast_pages[i] = extract_text_fast(page)
            print_log(f"  [PDF Ingester] ページ {i+1}/{total_pages}: Python (高速抽出)")

    doc.close()

    print_log(f"  [PDF Ingester] ルーティング結果: Python={len(fast_pages)}ページ, VLM={len(vlm_page_nums)}ページ")

    # --- 高速ルート分の進捗を反映 ---
    completed = len(fast_pages)
    if state:
        current_percent = 5 + int(completed / total_pages * 10)
        state.update_status(f"PDF解析中... ({completed}/{total_pages} ページ)", current_percent)

    # --- VLMルートの非同期処理 ---
    vlm_results: dict[int, str] = {}
    if vlm_page_nums:
        semaphore = asyncio.Semaphore(VLM_SEMAPHORE_LIMIT)
        print_log(f"  [PDF Ingester] VLM対象 {len(vlm_page_nums)} ページの非同期抽出を開始...")

        async def _process_vlm_page(page_num: int) -> tuple[int, str]:
            text = await process_page_vlm(pdf_path, page_num, api_key, semaphore, model=model)
            return page_num, text

        tasks = [_process_vlm_page(pn) for pn in vlm_page_nums]

        for coro in asyncio.as_completed(tasks):
            page_num, text = await coro
            vlm_results[page_num] = text
            completed += 1
            if state:
                current_percent = 5 + int(completed / total_pages * 10)
                state.update_status(f"PDF解析中... ({completed}/{total_pages} ページ)", current_percent)
            print_log(f"  [PDF Ingester] VLM完了: ページ {page_num+1}/{total_pages}")

    # --- ページ順にテキストを結合 ---
    print_log("  [PDF Ingester] ページ間テキストの結合処理を開始...")
    all_results: dict[int, str] = {**fast_pages, **vlm_results}

    full_text = ""
    for i in range(total_pages):
        text = all_results.get(i, "").strip()
        if not text:
            continue

        if full_text:
            # 前のテキストの末尾が文末記号や閉じ括弧等でない場合、
            # 文が続いているとみなしてスペースで繋ぐ
            if not re.search(r"""[.!?\)\]\"']\d*$""", full_text.strip()):
                full_text += " " + text
            else:
                full_text += "\n\n" + text
        else:
            full_text = text

    return full_text


def run_pdf_ingestion(pdf_path: str, api_key: str | None = None, state: Any = None) -> str:
    """同期呼び出しラッパー"""
    return asyncio.run(run_pdf_ingestion_async(pdf_path, api_key, state))
