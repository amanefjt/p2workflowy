"""印刷頁番号の回収と、印刷頁→物理頁オフセットの推定。

書籍の TOC が持つ論理頁（＝紙面に印刷された頁番号）と PDF の物理頁との
写像は、PDF の作られ方に依存する。この写像を、ヘッダー/フッターに印字された
頁番号から実測で推定する。

spec: docs/superpowers/specs/2026-07-19-chapter-boundary-adjudication-design.md §2.2
"""

import re
from collections import Counter
from typing import Any, Optional

# 頁番号として受け付ける文字列の最大長・値域
PAGE_NUMBER_MAX_LEN = 8
PAGE_NUMBER_MIN_VALUE = 1
PAGE_NUMBER_MAX_VALUE = 9999

# ヘッダー/フッターとして走査する行数（先頭 N 行と末尾 N 行）
HEADER_FOOTER_LINES = 2

# オフセット推定に必要な最小の投票数。これ未満なら推定を諦める。
MIN_OFFSET_VOTES = 5

# OCR で数字と誤読されやすい文字の対応表。
# pdf_splitter.PDFSplitter._OCR_DIGIT_MAP と同一の内容を持つ（委譲元）。
OCR_DIGIT_MAP = str.maketrans(
    {'I': '1', 'l': '1', '|': '1', 'i': '1', 'r': '1',
     'O': '0', 'o': '0', 'S': '5', 'B': '8'}
)

# 行をトークンへ割る区切り（空白と縦罫）
_TOKEN_SPLIT_RE = re.compile(r'[\s|]+')


def parse_page_number(text: str) -> Optional[int]:
    """行を頁番号として解釈する。OCR 崩れ（'3 I'→31, 'l72'→172）に耐える。

    数字を1文字も含まない文字列（ローマ数字 'XIII' や 'I'）は頁番号として
    扱わない。章マーカーとの誤認を防ぐため。
    """
    t = text.strip()
    if not t or len(t) > PAGE_NUMBER_MAX_LEN:
        return None
    if not any(c.isdigit() for c in t):
        return None
    normalized = t.translate(OCR_DIGIT_MAP).replace(' ', '')
    if normalized.isdigit() and PAGE_NUMBER_MIN_VALUE <= int(normalized) <= PAGE_NUMBER_MAX_VALUE:
        return int(normalized)
    return None


def harvest_printed_page(page_text: str) -> Optional[int]:
    """頁のヘッダー/フッター領域から印刷頁番号を1つ回収する。

    recto は 'Knowing | 147'（タイトル→番号）、verso は '144 | 書名'（番号→タイトル）
    という交互配置が組版の慣習である。したがって各行の先頭トークンと末尾トークンの
    両方を候補として見る。

    同一頁から異なる数値が読めた場合は None を返す。本文中の数字や年号を
    誤って拾うより、その頁を投票から外すほうが安全である。
    """
    lines = [l.strip() for l in page_text.split("\n") if l.strip()]
    if not lines:
        return None

    candidates = []
    for line in lines[:HEADER_FOOTER_LINES] + lines[-HEADER_FOOTER_LINES:]:
        tokens = _TOKEN_SPLIT_RE.split(line)
        if not tokens:
            continue
        for token in (tokens[0], tokens[-1]):
            value = parse_page_number(token)
            if value is not None:
                candidates.append(value)

    if not candidates:
        return None
    if len(set(candidates)) != 1:
        return None
    return candidates[0]


def estimate_offset(doc: Any) -> Optional[int]:
    """文書全体から `物理 idx − 印刷頁` の最頻値を推定する。

    中央値ではなく最頻値を使う。relations のように部扉ごとにオフセットが
    階段状に変わる書籍では、中央値が実在しない中間値になりうるのに対し、
    最頻値は必ず実在する段のいずれかを選ぶ。

    投票数が MIN_OFFSET_VOTES 未満の場合は None を返す（推定を諦める）。
    """
    votes: Counter = Counter()
    for idx in range(len(doc)):
        printed = harvest_printed_page(doc[idx].get_text("text"))
        if printed is None:
            continue
        votes[idx - printed] += 1

    if not votes:
        return None
    offset, count = votes.most_common(1)[0]
    if count < MIN_OFFSET_VOTES:
        return None
    return offset
