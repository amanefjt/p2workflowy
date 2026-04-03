import re
import fitz
from typing import List, Dict, Any, Optional, Set
from core.config import print_log

class PhysicalIngester:
    """
    PyMuPDF を用いた物理データ抽出とページ境界の結合を専門に扱うエンジン。
    """
    HEADER_MARGIN_RATIO = 0.08
    FOOTER_MARGIN_RATIO = 0.10
    SHORT_LINE_RATIO = 0.85
    SENTENCE_END_RE = re.compile(r'[.!?\)\]"\'”。]\d*$', re.MULTILINE)

    def __init__(self):
        pass

    def extract_spans(self, page: fitz.Page, page_idx: int) -> List[Dict[str, Any]]:
        """PyMuPDF から詳細な物理情報付きのスパン要素を抽出する。"""
        elements = []
        text_dict = page.get_text("dict")
        blocks = [b for b in text_dict.get("blocks", []) if b.get("type") == 0]
        blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))

        for b in blocks:
            for l in b.get("lines", []):
                for s in l.get("spans", []):
                    text = s.get("text", "").strip()
                    if not text: continue
                    
                    font_name = s.get("font", "")
                    is_bold = bool(s.get("flags", 0) & 2**4) or "Bold" in font_name or "Heavy" in font_name
                    
                    elements.append({
                        "id": f"p{page_idx}_s{len(elements)}",
                        "text": text,
                        "page_idx": page_idx,
                        "font_size": s.get("size", 0.0),
                        "is_bold": is_bold,
                        "bbox": list(s.get("bbox", []))
                    })
        return elements

    def extract_page_text(self, page: fitz.Page, ignored_patterns: Set[str] = None, clip_y: float = None) -> str:
        """ページから構造化されたテキストを抽出する。"""
        h_limit = page.rect.height * self.HEADER_MARGIN_RATIO
        f_limit = page.rect.height * (1.0 - self.FOOTER_MARGIN_RATIO)
        
        text_dict = page.get_text("dict")
        blocks = [b for b in text_dict.get("blocks", []) if b.get("type") == 0]
        blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))

        paragraphs = []
        lines_buffer = []

        for b in blocks:
            y1 = b["bbox"][1]
            y3 = b["bbox"][3]
            if (clip_y and y1 > clip_y) or y3 <= h_limit or y1 >= f_limit: continue
            
            width = b["bbox"][2] - b["bbox"][0]
            if width <= 0: continue

            # 重複要素（ヘッダー等）の判定
            # (LayoutEngineの情報を利用するが、ここでは簡易的な文字列チェックを行う)
            block_text = " ".join("".join(s["text"] for s in l["spans"]) for l in b["lines"]).strip()
            if ignored_patterns:
                norm = re.sub(r'\d+', '', block_text).strip()
                if norm in ignored_patterns: continue

            for l in b["lines"]:
                line_text = "".join(s.get("text", "") for s in l.get("spans", []))
                if not line_text.strip():
                    if lines_buffer:
                        paragraphs.append(self._finalize_paragraph(lines_buffer))
                        lines_buffer = []
                    continue
                
                lines_buffer.append(line_text)
                l_width = l["bbox"][2] - l["bbox"][0]
                if (l_width / width < self.SHORT_LINE_RATIO) and self.SENTENCE_END_RE.search(line_text.strip()):
                    paragraphs.append(self._finalize_paragraph(lines_buffer))
                    lines_buffer = []

        if lines_buffer: paragraphs.append(self._finalize_paragraph(lines_buffer))
        return "\n\n".join(paragraphs)

    def _finalize_paragraph(self, lines: List[str]) -> str:
        text = " ".join(l.strip() for l in lines)
        return re.sub(r'(\w)-\s+(\w)', r'\1\2', text).strip()

    def join_page_boundary(self, prev_text: str, next_text: str) -> str:
        """ページを跨ぐテキストの結合方式を決定する。"""
        t1, t2 = prev_text.strip(), next_text.strip()
        if not t1 or not t2: return "\n\n"

        # ハイフネーション
        if t1.endswith("-") and t2[0].islower(): return ""
        # 箇条書き
        if re.search(r'(?:Item|\d+|[a-z])[\.\)]$', t1): return "\n\n"
        # 継続（小文字開始 or 非文末）
        if t2[0].islower() or not self.SENTENCE_END_RE.search(t1): 
            if t2[0] in ('"', "'", "「", "『"): return "\n\n"
            return " "
        
        return "\n\n"
