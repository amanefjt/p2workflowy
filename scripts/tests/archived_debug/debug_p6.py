import fitz
import re
from core.pdf_ingester import _detect_repeating_elements

doc = fitz.open("data/Booksample/pse/psdpdf.pdf")
ignored_patterns = _detect_repeating_elements(doc)
page = doc[5] # p6
blocks = page.get_text("blocks")
page_height = page.rect.height
extended_limit_top = page_height * 0.15
extended_limit_bottom = page_height * 0.85

for b in blocks:
    block_bbox = b[:4]
    block_text = b[4].strip().replace("\n", " ")
    if not block_text: continue
    
    norm_text = re.sub(r'\d+', '', block_text).strip()
    block_y0 = block_bbox[1]
    block_y1 = block_bbox[3]
    is_near_margin = (block_y1 <= extended_limit_top or block_y0 >= extended_limit_bottom)
    
    status = "KEEP"
    if norm_text in ignored_patterns and is_near_margin:
        status = "SKIP (NOISE)"
        
    print(f"[{status}] Y:[{block_y0:.1f}-{block_y1:.1f}] Margin:{is_near_margin} Text: {repr(block_text)}")
