"""
p2workflowy V2 Phase 1: Ingest Engine (Orchestrator)
アトミック化されたエンジン群（OCRManager, LayoutEngine, PhysicalIngester）を
統合制御する、300行以下のスリムなオーケストレーター。
"""

import asyncio
from typing import Any, List, Dict, Optional
import fitz
from PIL import Image

from .config import print_log
from .engine.p1_ingest.ocr_manager import OCRManager
from .engine.p1_ingest.layout_engine import LayoutEngine
from .engine.p1_ingest.physical_ingester import PhysicalIngester

async def run_pdf_ingestion_async(
    pdf_path: str,
    api_key: Optional[str] = None,
    state: Any = None,
    pdf_mode: str = "hybrid",
    model: Optional[str] = None,
    is_book: bool = False,
    heavy_ocr: bool = False,
) -> str:
    """PDF からテキストを抽出する（ハイブリッド解析オーケストレーター）。"""
    if state: state.update_status("PDF解析中 (Pass 1)...", 5)
    
    # 1. エンジンのインスタンス化
    ocr = OCRManager(api_key=api_key, model=model)
    layout = LayoutEngine(is_book=is_book)
    ingester = PhysicalIngester()

    doc = fitz.open(pdf_path)
    if not doc:
        print_log(f"  [ERROR] PDF を開けませんでした: {pdf_path}")
        return ""
    total_pages = len(doc)
    
    # 2. Pass 1: Global Layout Scan (Book mode のみ重複要素を検知)
    ignored_patterns = set()
    if is_book:
        ignored_patterns = layout.detect_repeating_elements(doc)

    # 3. ルーティング & 抽出判定
    page_texts: Dict[int, str] = {}
    vlm_tasks = []

    for i in range(total_pages):
        page = doc[i]
        
        # 脚注座標の特定
        clip_y = layout.find_footnote_separator(page) if heavy_ocr else None
        has_footnote = (clip_y is not None) or layout.has_footnotes_simple(page)

        # 抽出ルートの決定
        use_vlm = (pdf_mode == "full_vlm") or \
                  (not is_book and ocr.should_use_vlm(page, i, is_book, heavy_ocr, has_footnote))

        if use_vlm:
            # VLM ルート (非同期タスクとして登録)
            async def _vlm_job(idx: int):
                text = await ocr.process_page_vlm(pdf_path, idx)
                return idx, text
            vlm_tasks.append(_vlm_job(i))
        else:
            # Python ルート (高速抽出)
            text = ingester.extract_page_text(page, ignored_patterns=ignored_patterns, clip_y=clip_y)
            page_texts[i] = text
            print_log(f"  [Ingester] ページ {i+1}: Python ルート")

    doc.close()

    # 4. 非同期 VLM 処理の実行
    if vlm_tasks:
        print_log(f"  [OCR] {len(vlm_tasks)} ページの VLM 処理を開始します...")
        results = await asyncio.gather(*vlm_tasks)
        for idx, text in results:
            page_texts[idx] = text
            if state:
                p = int((len(page_texts) / total_pages) * 100)
                state.update_status(f"VLM OCR中... ({len(page_texts)}/{total_pages})", p)

    # 5. ページ間結合 (Post-process)
    final_text = ""
    sorted_indices = sorted(page_texts.keys())
    for i in sorted_indices:
        current = page_texts[i].strip()
        if not current: continue
        
        if is_book:
            current = layout.remove_running_headers(current)
            
        if not final_text:
            final_text = current
        else:
            action = ingester.join_page_boundary(final_text, current)
            final_text += action + current
            
    return final_text

async def run_pdf_ingestion_v3_async(
    pdf_path: str,
    api_key: Optional[str] = None,
    state: Any = None,
    pdf_mode: str = "hybrid",
    model: Optional[str] = None,
    is_book: bool = False,
    heavy_ocr: bool = False,
    max_pages: Optional[int] = None, # 追加
) -> List[Dict[str, Any]]:
    """PDF から詳細な要素（スパンまたは VLM ブロック）を抽出する (V3 対応)。"""
    if state: state.update_status(1, "PDF解析中 (Pass 1)...", 5)
    
    from .engine.p1_ingest.spread_splitter import SpreadSplitter
    ocr = OCRManager(api_key=api_key, model=model)
    ingester = PhysicalIngester()
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    if max_pages:
        total_pages = min(total_pages, max_pages)

    all_elements = []
    
    if pdf_mode == "full_vlm":
        print_log(f"  [Ingester] V3: Full VLM Route Enabled (Sliding Window)")
        session_dir = state.session_dir if state else None
        
        # 1. 全ページの画像を先にリスト化（高速。見開き分割を含む）
        images = []
        for i in range(total_pages):
            page = doc[i]
            pix = page.get_pixmap(dpi=200)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            
            # 見開き判定と分割 (LtoR)
            if is_book and SpreadSplitter.is_spread(img):
                print_log(f"  [Ingester] 見開き検出 (Physical Page {i+1}): 分割を実行します。")
                split_pages = SpreadSplitter.split_spread_ltr(img)
                images.extend(split_pages)
            else:
                images.append(img)
        
        # 分割後の総論理ページ数
        total_logical_pages = len(images)
        print_log(f"  [Ingester] 総論理ページ数: {total_logical_pages}")

        completed_count = 0
        async def _vlm_slice_job(lc_idx: int, curr_img: Image.Image, prev_img: Optional[Image.Image]):
            nonlocal completed_count
            try:
                # 3ccac82 の ocr_manager はテキスト（Markdown）を返す
                vlm_res = await ocr.process_page_vlm_v3(curr_img, prev_img=prev_img, page_idx=lc_idx, session_dir=session_dir)
                if not vlm_res:
                    raise ValueError("VLM returned empty output.")
            except Exception as e:
                # VLM が全リトライ失敗 → ネイティブPDFテキストにフォールバック
                print_log(f"  [Ingester] VLM 失敗 (Page {lc_idx}): {e}. ネイティブPDFテキストにフォールバック。")
                native_text = ""
                try:
                    # 見開き分割がある場合、インデックスがずれるため近似
                    if lc_idx < len(doc):
                        native_text = doc[lc_idx].get_text().strip()
                except Exception:
                    pass
                vlm_res = native_text if native_text else "[VLM抽出失敗]"

            completed_count += 1
            if state:
                p = int((completed_count / (total_logical_pages - 1)) * 100) if total_logical_pages > 1 else 100
                state.update_status(1, f"VLM スライディングOCR中... ({completed_count}/{total_logical_pages - 1})", p)
            return lc_idx, vlm_res

        # 全スライスのタスクを生成（1-2枚目, 2-3枚目...）
        tasks = []
        if total_logical_pages == 1:
            tasks.append(_vlm_slice_job(1, images[0], None))
        else:
            for i in range(1, total_logical_pages):
                tasks.append(_vlm_slice_job(i, images[i], images[i-1]))
        
        if tasks:
            print_log(f"  [Ingester] {len(tasks)} 個のスライディング VLM タスクを並列実行中 (Semaphore={ocr.semaphore._value})...")
            results = await asyncio.gather(*tasks)
            # インデックス順にソートして結果を格納
            results.sort(key=lambda x: x[0])

            for idx, text in results:
                id_str = "page_0_1" if idx == 1 else f"page_{idx}"
                all_elements.append({
                    "text": text,
                    "page_idx": idx,
                    "role": "vlm_page_source",
                    "id": id_str
                })
                if idx == 1:
                    preview = text[:100].strip() if idx == 1 else ""
                    print_log(f"  [Ingester] Front Matter (P1+P2) 解析完了。先頭 100 文字: {preview}...")
        
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

def run_pdf_ingestion_v3(pdf_path: str, **kwargs) -> List[Dict[str, Any]]:
    """run_pdf_ingestion_v3_async の同期版。"""
    from .llm_client import run_async
    return run_async(run_pdf_ingestion_v3_async(pdf_path, **kwargs))

def run_pdf_ingestion(pdf_path: str, **kwargs) -> str:
    """run_pdf_ingestion_async の同期版。"""
    from .llm_client import run_async
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
