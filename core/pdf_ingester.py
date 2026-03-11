"""
p2workflowy V2: Phase 0 (PDF Ingestion)
Gemini 3.1 Flash Lite を用いてPDFの画像を解析し、クリーンなテキストを抽出する。
"""

import asyncio
import re
from pathlib import Path
from typing import List

import fitz
from PIL import Image

from .llm_client import call_gemini_async
from .config import print_log

# プロンプト定義
OCR_PROMPT = """You are an expert OCR and text extraction system. Your task is to transcribe the text from the provided image EXACTLY as it appears, word-for-word.

CRITICAL INSTRUCTIONS:
1. FULL TRANSCRIPTION: You MUST transcribe all body text, headings, block quotes, and in-text citations. Do NOT summarize. Do NOT skip any paragraphs or sentences.
2. NOISE REMOVAL: Do NOT extract headers, footers, standalone page numbers, or journal names at the margins. Ignore text inside figures/tables and footnotes at the very bottom.
3. PARAGRAPH FORMATTING: Remove line breaks within the same paragraph. Each paragraph must be a single, continuous long line.
4. READING ORDER: For multi-column layouts, read the left column top-to-bottom first, then the right column.
5. HYPHENATION: If a word is split across a line break with a hyphen, combine it into a single word without the hyphen.
6. NO MARKDOWN: Output raw text only. Do NOT wrap the output in markdown code blocks.
"""

async def process_pdf_page(
    pdf_path: str,
    page_num: int,
    total_pages: int,
    api_key: str | None,
    semaphore: asyncio.Semaphore,
    state: "SessionState" = None,
) -> tuple[int, str]:
    """1ページをGeminiで処理し、(ページ番号, 抽出テキスト)を返す"""
    async with semaphore:
        # メモリ節約：必要な時だけページを画像に変換する
        doc = fitz.open(pdf_path)
        page = doc[page_num]
        
        # 200 DPI は Gemini の視覚認識には十分かつメモリ消費を大幅に抑えられる
        pix = page.get_pixmap(dpi=200)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        doc.close()

        prompt = [img, OCR_PROMPT]
        
        try:
            result = await call_gemini_async(
                prompt=prompt,
                model="gemini-3.1-flash-lite-preview",
                api_key=api_key,
                temperature=0.0,
                max_retries=3,
                retry_delay=5.0,
            )
            if state:
                # Page 1 -> 5%, Page Last -> 15% 程度とする
                current_percent = 5 + int((page_num + 1) / total_pages * 10)
                state.update_status(f"Phase 0: Extracting page {page_num+1}/{total_pages}...", current_percent)

            # 明示的に画像を破棄
            del img
            del pix

            # Markdown Code Block の除去
            result = re.sub(r"^```[a-zA-Z]*\n", "", result)
            result = re.sub(r"\n```$", "", result)
            return page_num, result.strip()
        except Exception as e:
            print_log(f"  [PDF Ingester] ページ {page_num+1} の処理に失敗しました: {e}")
            return page_num, ""

async def run_pdf_ingestion_async(pdf_path: str, api_key: str | None = None, state: "SessionState" = None) -> str:
    """PDFを画像化し、非同期でGeminiに渡してテキスト化する"""
    if state:
        state.update_status("Phase 0: Reading PDF...", 5)
    print_log(f"  [PDF Ingester] PDF読み込み開始: {pdf_path}")
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    print_log(f"  [PDF Ingester] 総ページ数: {total_pages}")
    doc.close()
    
    tasks = []
    # Render 無料枠 (512MB RAM) のため、同時実行数を抑え、メモリ負荷を下げる
    semaphore = asyncio.Semaphore(2)
    
    for i in range(total_pages):
        # 画像をここで作らず、パスとインデックスだけを渡す
        tasks.append(process_pdf_page(pdf_path, i, total_pages, api_key, semaphore, state))
        
    print_log(f"  [PDF Ingester] 全 {total_pages} ページの非同期抽出を開始...")
    results_with_idx = await asyncio.gather(*tasks)
    
    # ページ順にソート (元々順序は保持されるが念のため)
    results_with_idx = sorted(results_with_idx, key=lambda x: x[0])
    results = [text for _, text in results_with_idx]
    
    print_log("  [PDF Ingester] ページ間テキストの結合処理を開始...")
    
    full_text = ""
    for i, text in enumerate(results):
        text = text.strip()
        if not text:
            continue
        
        if full_text:
            # 前のテキストの末尾が文末記号(.!?)や閉じ括弧等でない場合、文が続いているとみなしてスペースで繋ぐ
            if not re.search(r"""[.!?\)\]\"']$""", full_text.strip()):
                full_text += " " + text
            else:
                full_text += "\n\n" + text
        else:
            full_text = text

    return full_text

def run_pdf_ingestion(pdf_path: str, api_key: str | None = None, state: "SessionState" = None) -> str:
    """同期呼び出しラッパー"""
    return asyncio.run(run_pdf_ingestion_async(pdf_path, api_key, state))
