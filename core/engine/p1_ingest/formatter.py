"""
p2workflowy 黄金の再構築: Preprocessor (Phase 1)
規約 03_unwrapper_sanctuary.md に準拠した物理整形エンジン。
"""

import re
from typing import List, Dict, Any, Optional
from core.models import RawChunk
from core.exceptions import PreprocessorError

# --- 物理判定定数 ---
# 規約 03_unwrapper_sanctuary.md に準拠
_SENTENCE_END_RE = re.compile(r"""[.!?;:\"'](?:\[[\d,\s-]+\])?[\d\u2070\u00b9\u00b2\u00b3\u2074-\u2079]*\s*$""")

_TRAILING_WORDS = frozenset([
    "the", "a", "an", "of", "in", "on", "at", "to", "for",
    "and", "or", "but", "with", "by", "from", "as", "is",
    "was", "were", "are", "has", "had", "have", "that",
    "which", "who", "whom", "this", "these", "those",
])

class Formatter:
    """PDF 由来の断片的なテキスト行を論理的な段落 (RawChunk) に復元する。"""

    @staticmethod
    def smart_unwrap(raw_elements: List[Dict[str, Any]]) -> List[RawChunk]:
        """
        物理的な行 (dictionary 形式) を受け取り、段落単位の RawChunk を生成する。
        各 element は 'text', 'page_idx', 'font_size', 'is_bold', 'bbox' を持つことを期待。
        """
        if not raw_elements:
            return []

        chunks: List[RawChunk] = []
        current_buffer: List[str] = []
        current_meta = {
            "page_idx": raw_elements[0].get("page_idx", -1),
            "max_font_size": 0.0,
            "has_bold": False,
            "bboxes": [],
            "start_id": str(raw_elements[0].get("id", "0"))
        }

        for i, el in enumerate(raw_elements):
            text = el.get("text", "").strip()
            if not text:
                continue

            # 物理情報の集約
            current_meta["max_font_size"] = max(current_meta["max_font_size"], el.get("font_size", 0.0))
            if el.get("is_bold"):
                current_meta["has_bold"] = True
            if el.get("bbox"):
                current_meta["bboxes"].append(el["bbox"])

            # 文字列の集約（後でインテリジェントに結合）
            current_buffer.append(text)

            # --- 分割（チャンク化）の判定 ---
            should_split = False
            
            # 1. 文末記号による判定
            is_end = bool(_SENTENCE_END_RE.search(text))
            
            # 2. 強制結合単語のチェック
            last_word = re.sub(r'[^\w]', '', text.split()[-1]).lower() if text.split() else ""
            is_trailing = last_word in _TRAILING_WORDS or text.endswith("-")

            # 3. 文末かつ強制結合単語でない場合、または最終要素の場合は分割
            if (is_end and not is_trailing) or i == len(raw_elements) - 1:
                should_split = True
            
            # [特殊] 次の要素とのフォントサイズや太字属性に有意な差がある場合は、文末に関わらず分割
            if i + 1 < len(raw_elements):
                next_el = raw_elements[i+1]
                if abs(next_el.get("font_size", 0.0) - el.get("font_size", 0.0)) > 0.5 or \
                   next_el.get("is_bold") != el.get("is_bold"):
                    should_split = True

            if should_split:
                # バッファのインテリジェント結合 (Hyphen-aware Join)
                joined_text = ""
                for j, segment in enumerate(current_buffer):
                    if j == 0:
                        joined_text = segment
                    else:
                        prev_segment = current_buffer[j-1]
                        if prev_segment.endswith("-"):
                            # ハイフンを除去して直接結合
                            joined_text = joined_text[:-1] + segment
                        else:
                            # スペースを挟んで結合
                            joined_text += " " + segment
                
                # RawChunk 作成
                chunk = RawChunk(
                    id=f"chunk_{current_meta['start_id']}",
                    text=joined_text,
                    seq_index=float(i),
                    page_idx=current_meta["page_idx"],
                    font_size=current_meta["max_font_size"],
                    is_bold=current_meta["has_bold"],
                    bbox=Formatter._merge_bboxes(current_meta["bboxes"])
                )
                chunks.append(chunk)

                # バッファとメタデータの初期化
                current_buffer = []
                if i + 1 < len(raw_elements):
                    next_el = raw_elements[i+1]
                    current_meta = {
                        "page_idx": next_el.get("page_idx", -1),
                        "max_font_size": 0.0,
                        "has_bold": False,
                        "bboxes": [],
                        "start_id": str(next_el.get("id", str(i+1)))
                    }

        return chunks

    @staticmethod
    def logical_split(vlm_elements: List[Dict[str, Any]]) -> List[RawChunk]:
        """
        VLM が抽出した論理ブロックをパースし、RawChunk のリストに変換する。
        """
        chunks: List[RawChunk] = []
        global_idx = 0

        for el in vlm_elements:
            page_idx = el.get("page_idx", -1)
            raw_text = el.get("text", "")
            
            # ブロックごとに分割 (VLMは通常 \n\n で段落を区切る)
            lines = [p.strip() for p in raw_text.split("\n\n") if p.strip()]
            
            for line in lines:
                role = "p"
                clean_text = line
                
                # 1. 見出し判定 (# ) or 特定の強制見出しキーワード
                # GUARDRAIL: [Unlabeled Section] は VLM が # を付け忘れても見出し扱いにする
                is_forced_heading = "[Unlabeled Section]" in line
                
                if line.startswith("# ") or is_forced_heading:
                    full_h_text = line[2:].strip() if line.startswith("# ") else line.strip()
                    if "\n" in full_h_text:
                        # 見出しの直後に本文が混入している場合 (VLMの出力ミス対策)
                        parts = full_h_text.split("\n", 1)
                        h_title = parts[0].strip()
                        body_remainder = parts[1].strip()
                        
                        # 見出しを追加
                        chunks.append(RawChunk(
                            id=f"chunk_{global_idx}",
                            text=h_title,
                            seq_index=float(global_idx),
                            page_idx=page_idx,
                            role="h1"
                        ))
                        global_idx += 1
                        
                        # 残りを本文として追加
                        if body_remainder:
                            chunks.append(RawChunk(
                                id=f"chunk_{global_idx}",
                                text=body_remainder,
                                seq_index=float(global_idx),
                                page_idx=page_idx,
                                role="p"
                            ))
                            global_idx += 1
                        continue
                    else:
                        role = "h1"
                        clean_text = full_h_text
                
                # 2. メタデータ判定 ([metadata])
                elif "[metadata]" in line:
                    role = "metadata"
                
                chunk = RawChunk(
                    id=f"chunk_{global_idx}",
                    text=clean_text,
                    seq_index=float(global_idx),
                    page_idx=page_idx,
                    role=role
                )
                chunks.append(chunk)
                global_idx += 1
                
        return chunks

    @staticmethod
    def _merge_bboxes(bboxes: List[List[float]]) -> Optional[List[float]]:
        """複数の矩形領域を包摂する最大の矩形を計算する。 [x0, y0, x1, y1]"""
        if not bboxes:
            return None
        x0 = min(b[0] for b in bboxes)
        y0 = min(b[1] for b in bboxes)
        x1 = max(b[2] for b in bboxes)
        y1 = max(b[3] for b in bboxes)
        return [x0, y0, x1, y1]
