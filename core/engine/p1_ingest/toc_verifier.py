"""TOC エントリの検算と、エントリ↔頁番号の系統的ずれ（shift）の補正。

LLM による TOC 抽出（Route 3）は、目次頁のテキスト層が列単位で出力される
書籍で、エントリと頁番号を1つずらして対応付けることがある（PSE で実測）。
このずれは下流のどの精緻化でも回復できないため、上流で検出・補正する。

検算の方法は「予測物理頁にそのエントリのタイトルが実在するか」である。
予測は `論理頁 + オフセット`（オフセットは page_number_map が実測から推定）。

spec: docs/superpowers/specs/2026-07-19-chapter-boundary-adjudication-design.md §2.2
"""

from typing import Any, Callable, Dict, List

from core.config import print_log
from .page_number_map import estimate_offset

# 試す shift の候補。実測では PSE の −1 以外に必要なずれは観測されていない。
SHIFT_CANDIDATES = (-1, 0, 1)

# 検算でタイトルを探す行数（章扉のタイトルは冒頭に現れる）
VERIFY_HEAD_LINES = 12

# タイトル比較に使う先頭文字数。全長一致を求めると、扉頁で改行された
# タイトルの後半が欠ける場合に落ちるため、先頭のみを見る。
VERIFY_TITLE_PREFIX = 25

# shift 補正を適用する条件。最良 shift の一致数がこの件数以上あり、
# かつ次点の SHIFT_DOMINANCE_RATIO 倍以上であること。
# 判断材料が乏しい書籍で賭けに出ないための安全弁である。
SHIFT_MIN_MATCHES = 3
SHIFT_DOMINANCE_RATIO = 2.0


def count_title_matches(
    doc: Any,
    entries: List[Dict[str, Any]],
    offset: int,
    shift: int,
    normalize: Callable[[str], str],
) -> int:
    """shift を適用したときに、予測物理頁に自分のタイトルが実在する章の数。"""
    matches = 0
    for i, entry in enumerate(entries):
        source = i + shift
        if not (0 <= source < len(entries)):
            continue
        logical = entries[source].get("start_page")
        if logical is None:
            continue
        predicted = int(logical) + offset
        if not (0 <= predicted < len(doc)):
            continue

        norm_title = normalize(entry.get("title", ""))
        if not norm_title:
            continue

        lines = [l.strip() for l in doc[predicted].get_text("text").split("\n") if l.strip()]
        head = normalize(" ".join(lines[:VERIFY_HEAD_LINES]))
        if norm_title[:VERIFY_TITLE_PREFIX] in head:
            matches += 1
    return matches


def detect_toc_shift(
    doc: Any,
    entries: List[Dict[str, Any]],
    offset: int,
    normalize: Callable[[str], str],
) -> int:
    """エントリと頁番号の対応ずれを検出する。ずれが無ければ 0 を返す。

    実測（2026-07-19）では PSE のみ shift=−1 が 12対2 で勝ち、
    corfra・Naven・relations は shift=0 が勝った（誤検出ゼロ）。
    """
    scores = {s: count_title_matches(doc, entries, offset, s, normalize)
              for s in SHIFT_CANDIDATES}
    best_shift = max(scores, key=lambda s: scores[s])
    best = scores[best_shift]

    if best_shift == 0:
        return 0
    if best < SHIFT_MIN_MATCHES:
        return 0

    runner_up = max(v for s, v in scores.items() if s != best_shift)
    if runner_up > 0 and best < runner_up * SHIFT_DOMINANCE_RATIO:
        return 0
    return best_shift


def apply_shift(entries: List[Dict[str, Any]], shift: int) -> List[Dict[str, Any]]:
    """shift を適用し、各エントリに参照先エントリの頁番号を割り当てる。

    参照先が範囲外になるエントリの start_page は None にする。
    その章は論理頁を持たないものとして、下流のコンテンツスキャンに委ねられる。
    """
    if shift == 0:
        return entries

    fixed = []
    for i, entry in enumerate(entries):
        source = i + shift
        new_entry = dict(entry)
        if 0 <= source < len(entries):
            new_entry["start_page"] = entries[source].get("start_page")
        else:
            new_entry["start_page"] = None
        fixed.append(new_entry)
    return fixed


def verify_and_fix_toc(
    doc: Any,
    entries: List[Dict[str, Any]],
    normalize: Callable[[str], str],
) -> List[Dict[str, Any]]:
    """TOC を検算し、系統的ずれがあれば補正して返す（層1の入口）。

    オフセットを推定できない書籍では何もしない（元のまま返す）。
    """
    if not entries:
        return entries

    offset = estimate_offset(doc)
    if offset is None:
        print_log("  [TOCVerifier] 印刷頁番号が乏しく写像を推定できません。検算をスキップします。")
        return entries

    shift = detect_toc_shift(doc, entries, offset, normalize)
    if shift == 0:
        print_log(f"  [TOCVerifier] TOC 検算: ずれなし（推定オフセット {offset:+d}）")
        return entries

    print_log(
        f"  [TOCVerifier] TOC のエントリと頁番号が {shift:+d} ずれています。"
        f"補正します（推定オフセット {offset:+d}）。"
    )
    return apply_shift(entries, shift)
