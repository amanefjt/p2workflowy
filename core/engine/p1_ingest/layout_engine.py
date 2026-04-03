import re
import statistics
import fitz
from typing import List, Set, Dict, Any, Optional
from core.config import print_log

class LayoutEngine:
    """
    PDF のレイアウト解析（脚注、ヘッダー/フッター、ノイズ検知）を専門に扱うエンジン。
    """
    HEADER_MARGIN_RATIO = 0.08
    FOOTER_MARGIN_RATIO = 0.10
    FOOTNOTE_AREA_RATIO = 0.80
    FOOTNOTE_FONT_RATIO = 0.60
    
    MIN_DUPLICATE_PAGES = 3
    MIN_DUPLICATE_RATIO = 0.05

    def __init__(self, is_book: bool = False):
        self.is_book = is_book
        self.ignored_patterns: Set[str] = set()

    def detect_repeating_elements(self, doc: fitz.Document) -> Set[str]:
        """Pass 1: 全ページをスキャンし、反復するヘッダー・フッター（ノイズ）を特定する。"""
        total_pages = len(doc)
        if total_pages < 2: return set()

        candidates = []
        for i in range(total_pages):
            page = doc[i]
            h_limit = page.rect.height * 0.10
            f_limit = page.rect.height * 0.90
            
            blocks = page.get_text("blocks")
            for b in blocks:
                if b[1] < h_limit or b[3] > f_limit:
                    text = b[4].strip()
                    norm = re.sub(r'\d+', '', text).strip()
                    if len(norm) > 3: candidates.append(norm)

        from collections import Counter
        counts = Counter(candidates)
        threshold = max(self.MIN_DUPLICATE_PAGES, int(total_pages * self.MIN_DUPLICATE_RATIO))
        
        for text, count in counts.items():
            if count >= threshold:
                self.ignored_patterns.add(text)
        
        return self.ignored_patterns

    def find_footnote_separator(self, page: fitz.Page) -> Optional[float]:
        """OpenCV を用いて脚注セパレーター（罫線）の Y 座標を特定する。"""
        try:
            import cv2
            import numpy as np
        except ImportError:
            return None

        pix = page.get_pixmap(dpi=150)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        _, binary = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
        
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
        detect = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=2)
        lines = cv2.HoughLinesP(detect, 1, np.pi/180, 100, minLineLength=50, maxLineGap=10)
        
        if lines is None: return None
        
        ys = [line[0][1] for line in lines if line[0][1] > pix.height * 0.60 and abs(line[0][1] - line[0][3]) < 2]
        if not ys: return None
        
        return min(ys) * (page.rect.height / pix.height)

    def has_footnotes_simple(self, page: fitz.Page) -> bool:
        """フォントサイズと位置に基づいて脚注の存在を判定する。"""
        text_dict = page.get_text("dict")
        font_sizes = []
        for b in text_dict.get("blocks", []):
            if b.get("type") != 0: continue
            for l in b.get("lines", []):
                for s in l.get("spans", []):
                    if s.get("text", "").strip(): font_sizes.append(s["size"])
        
        if not font_sizes: return False
        
        median_size = statistics.median(font_sizes)
        threshold = median_size * self.FOOTNOTE_FONT_RATIO
        area_top = page.rect.height * self.FOOTNOTE_AREA_RATIO
        
        for b in text_dict.get("blocks", []):
            if b.get("type") == 0 and b["bbox"][1] >= area_top:
                for l in b.get("lines", []):
                    for s in l.get("spans", []):
                        if s.get("text", "").strip() and s["size"] <= threshold:
                            return True
        return False

    def remove_running_headers(self, text: str) -> str:
        """Pass 1 で検知したキーワードを用いて、本文先頭に癒着した柱を動的に除去する。"""
        if not text or not self.ignored_patterns: return text

        lines = text.split('\n')
        processed = []
        check_limit = 2
        non_empty = 0
        
        for line in lines:
            line_strip = line.strip()
            if not line_strip:
                processed.append(line)
                continue
            
            if non_empty < check_limit:
                removed = False
                for kw in self.ignored_patterns:
                    pattern = rf"^({re.escape(kw)}\s*\d+|\d+\s*{re.escape(kw)})(\s+|$)"
                    match = re.search(pattern, line, re.I)
                    if match:
                        rem = line[match.end():]
                        if not rem or rem[0].islower():
                            processed.append(rem)
                            removed = True
                            break
                if removed: 
                    non_empty += 1
                    continue
            
            non_empty += 1
            processed.append(line)
            
        return '\n'.join(p for p in processed if p.strip() or not p)
