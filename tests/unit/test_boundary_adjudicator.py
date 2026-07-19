"""
boundary_adjudicator のユニットテスト

テスト対象:
  - flag_suspects: 前後の確定章の双方とオフセットが食い違う章の検出（層2）
  - interpolated_offset: 前後の確定章から期待オフセットを補間
"""

import pytest

from core.engine.p1_ingest.boundary_adjudicator import (
    ChapterPlacement,
    flag_suspects,
    interpolated_offset,
)


def placements(specs):
    """(logical, physical, matched) の列から ChapterPlacement 列を作る。"""
    return [
        ChapterPlacement(index=i, title=f"Ch{i}", logical_page=lg,
                         start_page=ph, matched=m)
        for i, (lg, ph, m) in enumerate(specs)
    ]


class TestFlagSuspects:
    def test_flat_offsets_flag_nothing(self):
        """corfra 型: 全章が同じオフセット。"""
        p = placements([(10, 19, True), (20, 29, True), (30, 39, True), (40, 49, True)])
        assert flag_suspects(p) == set()

    def test_stepped_offsets_flag_nothing(self):
        """relations 型: 部扉ごとに段が変わる。前か後の一方と一致すれば通過。"""
        p = placements([
            (10, 24, True),   # +14
            (20, 32, True),   # +12
            (30, 42, True),   # +12
            (40, 50, True),   # +10
            (50, 60, True),   # +10
        ])
        assert flag_suspects(p) == set()

    def test_single_deviant_is_flagged(self):
        """Naven 型: 1章だけ前後の双方と食い違う。"""
        p = placements([
            (10, 40, True),   # +30
            (20, 50, True),   # +30
            (30, 61, True),   # +31 ← 逸脱
            (40, 70, True),   # +30
            (50, 80, True),   # +30
        ])
        assert flag_suspects(p) == {2}

    def test_large_deviant_is_flagged(self):
        """Naven XII 型: 逸脱幅が大きい場合も同じ規則で捕まる。"""
        p = placements([
            (10, 40, True), (20, 50, True), (30, 67, True), (40, 70, True), (50, 80, True),
        ])
        assert flag_suspects(p) == {2}

    def test_fallback_chapters_always_flagged(self):
        """フォールバックに落ちた章は無条件に要審査。"""
        p = placements([(10, 19, True), (20, 19, False), (30, 39, True)])
        assert 1 in flag_suspects(p)

    def test_fallback_chapters_not_used_as_reference(self):
        """フォールバック章は前後の参照に使わない。"""
        p = placements([
            (10, 19, True),    # +9
            (20, 20, False),   # フォールバック（参照に使わない）
            (30, 39, True),    # +9
            (40, 49, True),    # +9
        ])
        # index1 は要審査だが、index2 は前(index0)・後(index3)ともに +9 で通過する
        suspects = flag_suspects(p)
        assert 1 in suspects
        assert 2 not in suspects

    def test_first_chapter_never_flagged(self):
        """前の確定章が無い章は評価対象外（前付けの誤検知を防ぐ）。"""
        p = placements([(10, 11, True), (20, 29, True), (30, 39, True)])
        # index0 のオフセットは +1 で他と違うが、前が無いので対象外
        assert 0 not in flag_suspects(p)

    def test_last_chapter_never_flagged(self):
        p = placements([(10, 19, True), (20, 29, True), (30, 45, True)])
        assert 2 not in flag_suspects(p)

    def test_empty_input(self):
        assert flag_suspects([]) == set()


class TestInterpolatedOffset:
    def test_uses_surrounding_confirmed_chapters(self):
        p = placements([(10, 19, True), (20, 20, False), (30, 39, True)])
        assert interpolated_offset(p, 1) == 9

    def test_averages_when_neighbours_differ(self):
        p = placements([(10, 22, True), (20, 20, False), (30, 40, True)])
        # 前 +12、後 +10 → 平均 +11
        assert interpolated_offset(p, 1) == 11

    def test_falls_back_to_single_neighbour(self):
        p = placements([(10, 19, True), (20, 20, False)])
        assert interpolated_offset(p, 1) == 9

    def test_returns_none_without_any_confirmed_neighbour(self):
        p = placements([(10, 10, False), (20, 20, False)])
        assert interpolated_offset(p, 0) is None
