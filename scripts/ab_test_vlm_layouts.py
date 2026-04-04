import asyncio
import os
import json
import fitz
from pathlib import Path
from PIL import Image
import sys

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from core.engine.p1_ingest.ocr_manager import OCRManager, LayoutMode
from core.config import STATE_DIR, print_log, GEMINI_API_KEY

async def run_ab_test(pdf_path: str, pages_count: int = 4):
    pdf_path = Path(pdf_path)
    pdf_name = pdf_path.stem
    test_dir = STATE_DIR / "ab_test" / pdf_name
    test_dir.mkdir(parents=True, exist_ok=True)
    
    modes = [LayoutMode.NATIVE, LayoutMode.STITCH_DIVIDER]
    
    for mode in modes:
        print_log(f"--- Starting AB Test: {pdf_name} | Mode: {mode.value} ---")
        manager = OCRManager(api_key=GEMINI_API_KEY, layout_mode=mode)
        
        doc = fitz.open(str(pdf_path))
        results = []
        prev_img = None
        
        # セッションディレクトリをモードごとに分ける
        session_dir = test_dir / mode.value
        session_dir.mkdir(parents=True, exist_ok=True)
        
        for i in range(min(pages_count, len(doc))):
            page = doc[i]
            # 200 DPI でレンダリング
            pix = page.get_pixmap(dpi=200)
            current_img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            
            print_log(f"  Processing Page {i+1}/{pages_count} (Mode: {mode.value})...")
            blocks = await manager.process_page_vlm_v3(
                current_img=current_img,
                prev_img=prev_img,
                page_idx=i+1,
                session_dir=session_dir
            )
            
            results.append({
                "page_idx": i+1,
                "blocks": blocks
            })
            prev_img = current_img
            
        doc.close()
        
        # 結果の保存
        output_path = test_dir / f"results_{mode.value}.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print_log(f"  Saved results to {output_path}")

async def main():
    pdfs = [
        str(PROJECT_ROOT / "data" / "paper" / "ALpdf.pdf"),
        str(PROJECT_ROOT / "data" / "chap1relations.pdf")
    ]
    
    for pdf in pdfs:
        if os.path.exists(pdf):
            await run_ab_test(pdf)
        else:
            print_log(f"!!! Error: File not found: {pdf} !!!")

if __name__ == "__main__":
    asyncio.run(main())
