import re
from typing import List, Optional, Tuple, Dict
from collections import Counter
from core.models import RawChunk

class HeadingDetector:
    """
    物理データ主権（Physical Data Sovereignty）に基づく学術論文用見出し判定エンジン。
    """
    def __init__(self, headings: List[str] = None, body_font_size: float = 0.0):
        self.headings = headings or []
        self.body_font_size = body_font_size
        self._update_threshold()

    def _update_threshold(self):
        """本文サイズに基づいて見出し閾値を更新。"""
        # ユーザーFB: 1.05倍以上、または属性判定
        self.heading_font_threshold = self.body_font_size * 1.05 if self.body_font_size > 0 else 0.0

    @staticmethod
    def compute_body_font_size(chunks: List[RawChunk]) -> float:
        """
        全チャンクの中から最も頻出するフォントサイズを「本文」のサイズとして推定。
        """
        sizes = [round(c.font_size, 1) for c in chunks if c.font_size > 0]
        if not sizes:
            return 10.0
        most_common = Counter(sizes).most_common(1)
        return most_common[0][0]

    @staticmethod
    def normalize_heading(text: str) -> str:
        """比較のために見出しを正規化する（記号、数字を剥離し、小文字化）。"""
        t = re.sub(r'^[\d\.]+\s*', '', text.strip())
        original_clean = re.sub(r'[^\w\s]', '', t).strip()
        t = re.sub(r'[^\w\s]', ' ', t)
        norm = " ".join(t.lower().split())
        if len(norm) < 2 and original_clean:
            return " ".join(original_clean.lower().split())
        return norm

    def detect(self, chunk: RawChunk) -> Tuple[bool, str, str]:
        """
        チャンクが見出しであるか判定し、(判定結果, 見出しタイトル, 残りの本文) を返す。
        """
        text = chunk.text.strip()
        if not text:
            return False, "", ""

        # 1. テキストマッチングによる判定 (Anchor List / TOC 優先)
        match = self._match_known_headings(text)
        if match:
            return True, match[0], match[1]

        # 2. 物理データ主権（Physical Sovereignty）による判定
        # 学術論文用: 斜体(Italic)やフォントの種類、わずかなサイズ差を統合
        
        # 2.1 フォントサイズによる判定 (1.05倍〜)
        if self.heading_font_threshold > 0 and chunk.font_size >= self.heading_font_threshold:
            # 本文より僅かでも大きければ見出し候補
            return True, text, ""

        # 2.2 太字属性、または「斜体 (Italic)」属性
        if (chunk.is_bold or chunk.is_italic) and text and text[0].isupper():
            # 1行が短い、あるいは大文字始まりであれば見出しの可能性が高い
            if len(text.split()) < 10:
                return True, text, ""

        # 2.3 フォントファミリーによる判定 (Gothic / Sans-serif 等)
        if chunk.font_name:
            fn = chunk.font_name.lower()
            if any(x in fn for x in ["gothic", "sans", "helvetica", "arimo"]):
                # セリフ体の本文の中でサンセリフ体が使われている場合は有力な見出し
                if len(text.split()) < 15:
                    return True, text, ""

        return False, "", text

    def _match_known_headings(self, text: str) -> Optional[Tuple[str, str]]:
        """既知の見出しリストとの前方一致判定。"""
        lines = text.split("\n")
        first_line = lines[0].strip()
        if not first_line:
            return None

        norm_full_line = self.normalize_heading(first_line)
        
        for head in self.headings:
            norm_head = self.normalize_heading(head)
            if not norm_head:
                continue
                
            if norm_full_line.startswith(norm_head):
                # 誤爆防止フィルターの調整
                # ユーザーFB: 短い見出し (Methods 等) を不当に弾いてはいけない
                if len(norm_full_line) > len(norm_head) * 2.5:
                    if len(norm_head) < 4: # "1. H" のような極端に短いマッチは弾く
                        continue
                    # アンカーリストにある見出しが本文中に埋没している場合も分割を許可する

                # 一致した場合の分離ロジック
                words = first_line.split()
                # 剥離された番号等をスキップするために、正規化後の単語数差分を利用
                head_norm_words = len(norm_head.split())
                full_norm_words = len(norm_full_line.split())
                
                if norm_full_line == norm_head:
                    matched_remaining = ""
                else:
                    # 後続の本文を切り出す
                    # words の末尾から、(full_norm - head_norm) 個の単語を救出
                    remaining_count = full_norm_words - head_norm_words
                    matched_remaining = " ".join(words[len(words)-remaining_count:]).strip()

                if len(lines) > 1:
                    matched_remaining += ("\n" if matched_remaining else "") + "\n".join(lines[1:])
                
                return head, matched_remaining

        return None
