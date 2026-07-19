"""章境界の逸脱検出（層2）と LLM 裁定（層3）。

層2は「前の確定章と次の確定章の双方とオフセットが食い違う章」を要審査とする。
逸脱幅の閾値は使えない — relations の正当な段差は 2、Naven の誤りは 1 であり、
大小で正誤を区別できないため（実測 2026-07-19）。

spec: docs/superpowers/specs/2026-07-19-chapter-boundary-adjudication-design.md §2.3, §2.4
"""

from dataclasses import dataclass
from typing import Any, List, Optional, Set

from core.config import print_log


@dataclass
class ChapterPlacement:
    """コンテンツスキャンが決めた1章の配置。"""

    index: int
    title: str
    logical_page: int          # TOC 由来の論理頁（1-indexed）
    start_page: int            # 決定した物理頁（0-indexed）
    matched: bool              # True=本文照合が成立、False=フォールバック

    @property
    def offset(self) -> int:
        return self.start_page - self.logical_page


def _confirmed_neighbour(
    placements: List[ChapterPlacement], index: int, step: int
) -> Optional[ChapterPlacement]:
    """index から step 方向へ進み、最初に見つかる確定章を返す。"""
    i = index + step
    while 0 <= i < len(placements):
        if placements[i].matched:
            return placements[i]
        i += step
    return None


def flag_suspects(placements: List[ChapterPlacement]) -> Set[int]:
    """要審査の章の index 集合を返す（層2）。

    規則:
      - フォールバックに落ちた章は無条件に要審査
      - 前の確定章と次の確定章の**双方と**オフセットが食い違う章は要審査

    前または次の確定章が存在しない章（前付け・最終章）は評価できないため
    対象外とする。これにより、ローマ数字など別の頁体系を持つ前付けの
    誤検知が自動的に消える。
    """
    suspects: Set[int] = set()

    for p in placements:
        if not p.matched:
            suspects.add(p.index)
            continue

        prev = _confirmed_neighbour(placements, p.index, -1)
        nxt = _confirmed_neighbour(placements, p.index, +1)
        if prev is None or nxt is None:
            continue

        if p.offset != prev.offset and p.offset != nxt.offset:
            suspects.add(p.index)

    return suspects


def interpolated_offset(
    placements: List[ChapterPlacement], index: int
) -> Optional[int]:
    """前後の確定章から期待オフセットを補間する。

    両側にあれば平均、片側のみならその値、どちらも無ければ None。
    """
    prev = _confirmed_neighbour(placements, index, -1)
    nxt = _confirmed_neighbour(placements, index, +1)

    if prev is not None and nxt is not None:
        return round((prev.offset + nxt.offset) / 2)
    if prev is not None:
        return prev.offset
    if nxt is not None:
        return nxt.offset
    return None
