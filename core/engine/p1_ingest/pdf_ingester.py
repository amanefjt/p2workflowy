"""
p2workflowy V2 Phase 1: Ingest Engine (Orchestrator)
アトミック化されたエンジン群（OCRManager, PhysicalIngester）を
統合制御する、300行以下のスリムなオーケストレーター。
"""

import asyncio
from typing import Any, List, Dict, Optional
import fitz
from PIL import Image

from core.config import print_log
from .ocr_manager import OCRManager
from .physical_ingester import PhysicalIngester


def build_prev_contexts(
    native_texts: List[str], image_src_page: List[int], tail_chars: int
) -> List[str]:
    """各論理ページの「前ページ文脈」を組み立てる（I-21）。

    論理ページ j の文脈は、直前の論理ページ j-1 の由来物理ページ
    （image_src_page[j-1]）のネイティブテキスト末尾 tail_chars 字。
    先頭ページ（j==0）は前文脈なし（空文字）。

    見開き分割された半ページは物理ページ全体のテキストを共有する（近似。
    文脈は継続判定のヒントにすぎず抽出対象ではないため許容）。
    """
    contexts: List[str] = []
    for j in range(len(image_src_page)):
        if j == 0:
            contexts.append("")
            continue
        prev_phys = image_src_page[j - 1]
        text = native_texts[prev_phys] if 0 <= prev_phys < len(native_texts) else ""
        contexts.append(text[-tail_chars:] if tail_chars > 0 else text)
    return contexts


async def run_pdf_ingestion_async(
    pdf_path: str,
    api_key: Optional[str] = None,
    state: Any = None,
    pdf_mode: str = "hybrid",
    model: Optional[str] = None,
    is_book: bool = False,
    heavy_ocr: bool = False,
    max_pages: Optional[int] = None, # 追加
) -> List[Dict[str, Any]]:
    """PDF から詳細な要素（スパンまたは VLM ブロック）を抽出する。"""
    if state: state.update_status(1, "PDF解析中 (Pass 1)...", 5)
    
    from .spread_splitter import SpreadSplitter
    ocr = OCRManager(api_key=api_key, model=model)
    ingester = PhysicalIngester()
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    if max_pages:
        total_pages = min(total_pages, max_pages)

    all_elements = []
    
    if pdf_mode == "full_vlm":
        print_log(f"  [Ingester] Full VLM Route Enabled (Sliding Window)")
        session_dir = state.session_dir if state else None
        
        # 1. 全ページの画像を先にリスト化（高速。見開き分割を含む）
        #    あわせて、各論理画像がどの物理ページ由来か（image_src_page）と
        #    各物理ページのネイティブテキスト（native_texts）を収集する（I-21 前文脈用）。
        images = []
        image_src_page = []
        native_texts = []
        for i in range(total_pages):
            page = doc[i]
            native_texts.append(page.get_text())
            pix = page.get_pixmap(dpi=200)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            # 見開き判定と分割 (LtoR)
            if is_book and SpreadSplitter.is_spread(img):
                print_log(f"  [Ingester] 見開き検出 (Physical Page {i+1}): 分割を実行します。")
                split_pages = SpreadSplitter.split_spread_ltr(img)
                images.extend(split_pages)
                image_src_page.extend([i] * len(split_pages))
            else:
                images.append(img)
                image_src_page.append(i)

        # 分割後の総論理ページ数
        total_logical_pages = len(images)
        print_log(f"  [Ingester] 総論理ページ数: {total_logical_pages}")

        # 各論理ページの前ページ文脈（前ページのネイティブテキスト末尾）
        prev_contexts = build_prev_contexts(
            native_texts, image_src_page, OCRManager.CONTEXT_TAIL_CHARS
        )

        completed_count = 0
        async def _vlm_slice_job(lc_idx: int, curr_img: Image.Image, prev_context_text: str):
            nonlocal completed_count
            try:
                # 空文字列は「このページに印刷テキストが無い」という正当な結果
                # （OCRManager.NO_TEXT_MARKER 経由）であり、失敗ではない。
                # 例外のみを本物の VLM 失敗として扱いフォールバックする。
                vlm_res = await ocr.process_page_vlm(
                    curr_img, prev_context_text=prev_context_text,
                    page_idx=lc_idx, session_dir=session_dir,
                )
            except Exception as e:
                print_log(f"  [Ingester] VLM 失敗 (Page {lc_idx}): {e}. ネイティブPDFテキストにフォールバック。")
                native_text = ""
                try:
                    phys = image_src_page[lc_idx] if lc_idx < len(image_src_page) else lc_idx
                    if phys < len(native_texts):
                        native_text = native_texts[phys].strip()
                except Exception:
                    pass
                vlm_res = native_text if native_text else "[VLM抽出失敗]"

            completed_count += 1
            if state:
                p = int((completed_count / total_logical_pages) * 100) if total_logical_pages else 100
                state.update_status(1, f"VLM 単ページOCR中... ({completed_count}/{total_logical_pages})", p)
            return lc_idx, vlm_res

        # 全ページのタスクを生成（先頭ページも独立。前文脈は prev_contexts から）
        tasks = [
            _vlm_slice_job(i, images[i], prev_contexts[i])
            for i in range(total_logical_pages)
        ]

        if tasks:
            print_log(f"  [Ingester] {len(tasks)} 個の単ページ VLM タスクを並列実行中 (Semaphore={ocr.semaphore._value})...")
            results = await asyncio.gather(*tasks)
            results.sort(key=lambda x: x[0])

            for idx, text in results:
                all_elements.append({
                    "text": text,
                    "page_idx": idx,
                    "role": "vlm_page_source",
                    "id": f"page_{idx}",
                })
            if results:
                preview = results[0][1][:100].strip()
                print_log(f"  [Ingester] 先頭ページ解析完了。先頭 100 文字: {preview}...")

        doc.close()
        return all_elements

    ingester = PhysicalIngester()
    doc = fitz.open(pdf_path)
    all_elements = []
    
    for i in range(len(doc)):
        spans = ingester.extract_spans(doc[i], i)
        all_elements.extend(spans)
        
    doc.close()
    return all_elements

def run_pdf_ingestion(pdf_path: str, **kwargs) -> List[Dict[str, Any]]:
    """run_pdf_ingestion_async の同期版。"""
    from core.llm_client import run_async
    return run_async(run_pdf_ingestion_async(pdf_path, **kwargs))

def diagnose_pdf_quality(pdf_path: str) -> bool:
    """
    PDF のテキスト品質を診断する。
    文字化け（非表示文字の多さ）や抽出テキストの極端な少なさを検知し、
    Route C (Full VLM) が必要かどうかを判定する。
    """
    try:
        doc = fitz.open(pdf_path)
        # 最初の 5 ページ程度をサンプリング
        sample_text = ""
        for i in range(min(5, len(doc))):
            sample_text += doc[i].get_text()
        doc.close()
        
        if not sample_text.strip():
            return False # テキストが全くなければノイズ扱い（OCRが必要）
            
        # 異常な文字（Replacement Character 等）の割合をチェック
        garbage_chars = sample_text.count('\ufffd') + sample_text.count('\x00')
        if garbage_chars > len(sample_text) * 0.05:
            return False # 5% 以上が文字化けなら不健全
            
        return True
    except:
        return False
