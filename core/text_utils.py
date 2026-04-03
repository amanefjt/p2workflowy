import re

# 文末判定用正規表現（引用ブラケット対応）
# Phase 1, Phase 3 で共有
_SENTENCE_END_RE = re.compile(r"""[.!?;:\"'](?:\[[\d,\s-]+\])?\s*$""")

# Trailing words リスト（前置詞・冠詞等、行末にある場合に結合を促す単語群）
# Phase 1, Phase 3 で共有
_TRAILING_WORDS = frozenset([
    "the", "a", "an", "of", "in", "on", "at", "to", "for",
    "and", "or", "but", "with", "by", "from", "as", "is",
    "was", "were", "are", "has", "had", "have", "that",
    "which", "who", "whom", "this", "these", "those",
])
# セクション判定用の定数
STOP_SECTIONS = ["References", "Bibliography", "Appendix", "Index", "Index of Subjects"]

def normalize_heading(text: str, squash: bool = False) -> str:
    """比較のために見出しを正規化する。
    squash=True の場合、記号だけでなく空白もすべて除去し、完全に文字のみで比較する。
    """
    if not text:
        return ""
    
    # ソフトハイフンや特殊な空白の除去
    text = text.replace('\u00ad', '').replace('\u2010', '-').replace('\u2011', '-')
    
    # 記号除去
    clean = re.sub(r'[^\w\s]', '', text)
    if squash:
        # 空白もすべて除去して小文字化
        return "".join(clean.lower().split())
        
    # 通常の正規化 (スペースを1つに統合)
    norm = " ".join(clean.lower().split())
    
    # 削りすぎた場合のフォールバック
    if len(norm) < 2 and text.strip():
        # 記号以外（数字等）が含まれている場合はそれを活かす
        fallback = "".join(re.findall(r'[\w\s]', text)).lower().strip()
        return " ".join(fallback.split()) if not squash else "".join(fallback.split())
        
    return norm

def roman_to_int(roman: str) -> int:
    """ローマ数字を整数に変換する。"""
    roman = roman.upper()
    val = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
    res = 0
    for i in range(len(roman)):
        if i > 0 and val[roman[i]] > val[roman[i - 1]]:
            res += val[roman[i]] - 2 * val[roman[i - 1]]
        else:
            res += val[roman[i]]
    return res

def is_roman_numeral(text: str) -> bool:
    """テキストがローマ数字かどうかを判定する。"""
    if not text:
        return False
    return bool(re.match(r'^[IVXLCDMivxlcdm]+$', text))

def extract_headings_from_resume(resume: str) -> list[str]:
    """レジュメからセクション見出し候補を抽出する。"""
    headings = []
    lines = resume.split("\n")
    for line in lines:
        line_strip = line.strip()
        match = re.match(r'^(?:[-\*]\s*)?((?:#\s*)+)\s*(.*)$', line_strip)
        if match:
            title = match.group(2).strip()
            
            # [Original English Heading] の書式がある場合、それを最優先で抽出
            brackets = re.findall(r'\[(.*?)\]', title)
            if brackets:
                extracted = " ".join([b.strip() for b in brackets if b.strip()]).strip()
                if extracted and extracted not in headings:
                    headings.append(extracted)
                continue
            
            # メタ見出しの除外
            meta_keywords = {
                "リサーチ・クエスチョン", "全体のリサーチ・クエスチョン", "章のリサーチ・クエスチョン",
                "核心的主張", "核心的主張（Thesis）", "全体の核心的主張", "中心的な主張", "章の核心的主張",
                "構成と理論的貢献", "論理展開", "詳細な論理展開", 
                "詳細な各章の論理展開", "章内の論理展開", "各節の主張とその根拠", "各章の展開",
                "要約", "書籍全体のレジュメ", "各セクションの展開", "各章の構成と理論的貢献"
            }
            clean_for_meta_check = re.sub(r'^[\d\.]+\s*', '', title).strip()
            if clean_for_meta_check in meta_keywords:
                continue
            
            # 元のタイトルも正規化して追加
            title_clean = re.sub(r'^[#\s]+', '', title)
            title_clean = title_clean.strip("[] ").strip()
            title_clean = re.sub(r'^(?:[IVXLCDM]+\b|[\d\.]+)\s*[:\.]?\s*', '', title_clean, flags=re.I)
            
            if len(title_clean.split()) > 20:
                continue
            if title_clean.endswith('.') or title_clean.endswith(','):
                continue

            if title_clean and title_clean not in headings:
                headings.append(title_clean)
                
    return headings
