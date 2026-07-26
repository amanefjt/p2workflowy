"""
boundary_adjudicator のユニットテスト

テスト対象:
  - flag_suspects: 前後の確定章の双方とオフセットが食い違う章の検出（層2）
  - interpolated_offset: 前後の確定章から期待オフセットを補間
  - BoundaryAdjudicator.adjudicate: 要審査の章に対する LLM 裁定（層3）
"""

from unittest.mock import MagicMock, patch

import pytest

from core.engine.p1_ingest.boundary_adjudicator import (
    BoundaryAdjudicator,
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

    def test_falls_back_to_next_only_neighbour(self):
        """前の確定章が無く、次の確定章のみ存在する場合はその値を使う。"""
        p = placements([(10, 10, False), (20, 29, True)])
        assert interpolated_offset(p, 0) == 9

    def test_average_rounds_half_to_even(self):
        """前+12・後+9 の平均10.5は Python の round() により偶数丸めで10になる。"""
        p = placements([(10, 22, True), (20, 20, False), (30, 39, True)])
        # 前 +12、後 +9 → 平均 10.5 → round(10.5) == 10（偶数丸め）
        assert interpolated_offset(p, 1) == 10

    def test_returns_none_without_any_confirmed_neighbour(self):
        p = placements([(10, 10, False), (20, 20, False)])
        assert interpolated_offset(p, 0) is None


class TestInterval:
    """BoundaryAdjudicator._interval の探索窓計算（層3の窓幅キャップ、2026-07-21修理）。"""

    def test_cluster_suspects_get_distinct_windows_when_gap_exceeds_max_pages(self):
        """同一確定章ペアに挟まれた複数の要審査章は、区間が ADJUDICATION_MAX_PAGES を
        超える場合、論理頁の位置に応じて異なる窓を割り当てられるべき。旧実装は index を
        無視し全員が区間の先頭からの同じ窓を割り当てられていた（PSE の12連続fallbackで
        後方の章の真の扉頁が窓外になり裁定不能になるバグ）。
        """
        from core.engine.p1_ingest.boundary_adjudicator import ADJUDICATION_MAX_PAGES
        p = placements([
            (10, 10, True),     # A 確定
            (30, 10, False),    # B fallback（論理頁的に A 寄り）
            (90, 10, False),    # C fallback（論理頁的に D 寄り）
            (100, 110, True),   # D 確定
        ])
        adj = BoundaryAdjudicator(api_key="k", model="m", cache={}, save_cache=lambda: None)

        lower_b, upper_b = adj._interval(p, 1, total_pages=200)
        lower_c, upper_c = adj._interval(p, 2, total_pages=200)

        assert (lower_b, upper_b) != (lower_c, upper_c), "同じ窓を割り当てられてはならない"
        assert lower_c > upper_b, "Cの窓はBより後方であるべき"
        assert upper_b - lower_b + 1 <= ADJUDICATION_MAX_PAGES
        assert upper_c - lower_c + 1 <= ADJUDICATION_MAX_PAGES
        # 確定章 A(10)・D(110) の区間からはみ出さない
        assert lower_b >= 11 and upper_c <= 109

    def test_interval_within_max_pages_is_returned_unchanged(self):
        """区間そのものが窓幅以下なら、絞り込まず区間全体を返す（既存挙動を維持）。"""
        p = placements([(10, 40, True), (20, 50, True), (30, 61, True), (40, 70, True)])
        adj = BoundaryAdjudicator(api_key="k", model="m", cache={}, save_cache=lambda: None)
        assert adj._interval(p, 2, total_pages=200) == (51, 69)

    def test_prefatory_cluster_gets_distinct_windows_when_no_confirmed_predecessor(self):
        """本の先頭（前に確定章が無い）に複数の fallback 章が並ぶ前付けクラスタで、
        区間が ADJUDICATION_MAX_PAGES を超える場合、片側にしか確定章が無いために
        論理頁ベースの比率が使えなくても、クラスタ内の並び順に応じて異なる窓を
        割り当てるべき。旧実装は prev が None だと ratio を常に 0.0 に固定し、
        クラスタ全員が区間先頭の同じ窓（lower_bound 起点）に潰れていた。
        """
        p = placements([
            (1, 1, False),      # Acknowledgments fallback
            (5, 1, False),      # Preface fallback
            (10, 1, False),     # Introduction fallback
            (20, 60, True),     # Chapter 1 確定
        ])
        adj = BoundaryAdjudicator(api_key="k", model="m", cache={}, save_cache=lambda: None)

        windows = [adj._interval(p, i, total_pages=200) for i in range(3)]

        assert len(set(windows)) == 3, "前付けクラスタの全章が同じ窓に潰れてはならない"
        assert windows[0][0] < windows[1][0] < windows[2][0]

    def test_postfatory_cluster_gets_distinct_windows_when_no_confirmed_successor(self):
        """本の末尾（後に確定章が無い）の fallback クラスタでも同様に窓を分けるべき。"""
        p = placements([
            (100, 150, True),   # 最終確定章
            (110, 150, False),  # Afterword fallback
            (120, 150, False),  # Index fallback
        ])
        adj = BoundaryAdjudicator(api_key="k", model="m", cache={}, save_cache=lambda: None)

        window1 = adj._interval(p, 1, total_pages=200)
        window2 = adj._interval(p, 2, total_pages=200)

        assert window1 != window2, "後付けクラスタの全章が同じ窓に潰れてはならない"
        assert window1[0] < window2[0]


def make_mock_doc(page_texts: list[str]) -> MagicMock:
    doc = MagicMock()
    pages = []
    for t in page_texts:
        p = MagicMock()
        p.get_text.return_value = t
        pages.append(p)
    doc.__len__.return_value = len(pages)
    doc.__getitem__ = MagicMock(side_effect=lambda idx: pages[idx])
    return doc


class TestAdjudicate:
    def _run(self, placements_, doc, response=None, raises=False, api_key="k"):
        adj = BoundaryAdjudicator(api_key=api_key, model="m", cache={}, save_cache=lambda: None)
        target = "core.engine.p1_ingest.boundary_adjudicator.call_gemini"
        if raises:
            with patch(target, side_effect=RuntimeError("API down")):
                return adj.adjudicate(doc, placements_, "hash")
        with patch(target, return_value=response):
            return adj.adjudicate(doc, placements_, "hash")

    def test_valid_response_moves_the_boundary(self):
        p = placements([
            (10, 40, True), (20, 50, True), (30, 61, True), (40, 70, True), (50, 80, True),
        ])
        doc = make_mock_doc(["x\n"] * 100)
        result = self._run(p, doc, response='{"page": 60, "reason": "章扉"}')
        assert result[2].start_page == 60

    def test_null_response_keeps_matched_chapter_as_is(self):
        p = placements([
            (10, 40, True), (20, 50, True), (30, 61, True), (40, 70, True), (50, 80, True),
        ])
        doc = make_mock_doc(["x\n"] * 100)
        result = self._run(p, doc, response='{"page": null, "reason": "判断不能"}')
        assert result[2].start_page == 61

    def test_null_response_uses_interpolated_offset_for_fallback_chapter(self):
        """フォールバック章では機械照合の値（オフセット0）に留まってはならない。"""
        p = placements([
            (10, 19, True),    # +9
            (20, 20, False),   # フォールバック（オフセット0 = I-27 の症状）
            (30, 39, True),    # +9
        ])
        doc = make_mock_doc(["x\n"] * 100)
        result = self._run(p, doc, response='{"page": null, "reason": "判断不能"}')
        assert result[1].start_page == 29, "論理頁20 + 補間オフセット9 = 29 になるべき"

    def test_out_of_range_response_is_rejected(self):
        p = placements([
            (10, 40, True), (20, 50, True), (30, 61, True), (40, 70, True), (50, 80, True),
        ])
        doc = make_mock_doc(["x\n"] * 100)
        # 前章50・次章70 の区間外を返した場合は棄却する
        result = self._run(p, doc, response='{"page": 95, "reason": "でたらめ"}')
        assert result[2].start_page == 61

    def test_monotonicity_violation_is_rejected(self):
        p = placements([
            (10, 40, True), (20, 50, True), (30, 61, True), (40, 70, True), (50, 80, True),
        ])
        doc = make_mock_doc(["x\n"] * 100)
        result = self._run(p, doc, response='{"page": 50, "reason": "前章と同じ"}')
        assert result[2].start_page == 61

    def test_malformed_response_is_rejected(self):
        p = placements([
            (10, 40, True), (20, 50, True), (30, 61, True), (40, 70, True), (50, 80, True),
        ])
        doc = make_mock_doc(["x\n"] * 100)
        result = self._run(p, doc, response="これはJSONではない")
        assert result[2].start_page == 61

    def test_exception_falls_back_safely(self):
        p = placements([
            (10, 40, True), (20, 50, True), (30, 61, True), (40, 70, True), (50, 80, True),
        ])
        doc = make_mock_doc(["x\n"] * 100)
        result = self._run(p, doc, raises=True)
        assert result[2].start_page == 61

    def test_no_api_key_skips_llm_entirely(self):
        p = placements([
            (10, 19, True), (20, 20, False), (30, 39, True),
        ])
        doc = make_mock_doc(["x\n"] * 100)
        adj = BoundaryAdjudicator(api_key=None, model="m", cache={}, save_cache=lambda: None)
        with patch("core.engine.p1_ingest.boundary_adjudicator.call_gemini") as mock_llm:
            result = adj.adjudicate(doc, p, "hash")
        assert not mock_llm.called
        # API キーが無くてもフォールバック章は補間オフセットで改善される
        assert result[1].start_page == 29

    def test_no_suspects_returns_input_unchanged(self):
        p = placements([(10, 19, True), (20, 29, True), (30, 39, True)])
        doc = make_mock_doc(["x\n"] * 100)
        adj = BoundaryAdjudicator(api_key="k", model="m", cache={}, save_cache=lambda: None)
        with patch("core.engine.p1_ingest.boundary_adjudicator.call_gemini") as mock_llm:
            result = adj.adjudicate(doc, p, "hash")
        assert not mock_llm.called
        assert [x.start_page for x in result] == [19, 29, 39]

    def test_out_of_range_response_on_fallback_uses_interpolated_baseline(self):
        """fallback 章で LLM が区間外を返したら、元のオフセット0ではなく補間オフセットへ落ちる。

        これは I-27 への逆戻りを防ぐ回帰テスト（レビューで発見された Critical）。
        """
        p = placements([
            (10, 19, True),    # +9
            (20, 20, False),   # フォールバック（start=20=オフセット0）
            (30, 39, True),    # +9
        ])
        doc = make_mock_doc(["x\n"] * 100)
        # 区間 [20, 38] の外（95）を返す
        result = self._run(p, doc, response='{"page": 95, "reason": "でたらめ"}')
        assert result[1].start_page == 29, "論理頁20 + 補間オフセット9 = 29 になるべき（元の20ではない）"

    def test_monotonicity_violation_on_fallback_uses_interpolated_baseline(self):
        """fallback 章で LLM が単調性違反（前章以前）を返した場合も補間オフセットへ。"""
        p = placements([
            (10, 19, True),
            (20, 20, False),
            (30, 39, True),
        ])
        doc = make_mock_doc(["x\n"] * 100)
        # 前章 start=19 より前（10）を返す＝単調性違反
        result = self._run(p, doc, response='{"page": 10, "reason": "前章より前"}')
        assert result[1].start_page == 29

    def test_cache_hit_skips_llm_call(self):
        p = placements([
            (10, 40, True), (20, 50, True), (30, 61, True), (40, 70, True), (50, 80, True),
        ])
        doc = make_mock_doc(["x\n"] * 100)
        # index2 の要審査章に対応するキャッシュを事前投入
        adj = BoundaryAdjudicator(api_key="k", model="m",
                                  cache={"hash_adjudicate_2_Ch2": 60}, save_cache=lambda: None)
        with patch("core.engine.p1_ingest.boundary_adjudicator.call_gemini") as mock_llm:
            result = adj.adjudicate(doc, p, "hash")
        assert not mock_llm.called, "キャッシュヒット時は LLM を呼ばない"
        assert result[2].start_page == 60

    def test_llm_crossover_is_repaired_not_dropped(self):
        """同一確定区間内の連続要審査章で LLM が順序を誤っても、章が消えず単調に修復される。

        最終ブランチレビュー I-1 の回帰テスト。A=確定(10), B・C=fallback, D=確定(50) で
        LLM が B→40・C→20 を返すと、修復前は [10,40,20,50] になり split() が
        C を無言でスキップ（start_page 20 < 前章末尾）して章が消える。修復後は
        C が前章直後（41）へクランプされ、全章が残り start_page が狭義単調増加する。
        """
        p = placements([
            (10, 10, True),    # A 確定
            (20, 20, False),   # B fallback（要審査）
            (30, 30, False),   # C fallback（要審査）
            (40, 50, True),    # D 確定
        ])
        doc = make_mock_doc(["x\n"] * 100)

        def per_chapter(prompt, **kwargs):
            if "Ch1" in prompt:      # B
                return '{"page": 40, "reason": "B"}'
            if "Ch2" in prompt:      # C（B より手前を誤って返す）
                return '{"page": 20, "reason": "C"}'
            return '{"page": null, "reason": "x"}'

        adj = BoundaryAdjudicator(api_key="k", model="m", cache={}, save_cache=lambda: None)
        with patch("core.engine.p1_ingest.boundary_adjudicator.call_gemini",
                   side_effect=per_chapter):
            result = adj.adjudicate(doc, p, "hash")

        starts = [x.start_page for x in result]
        assert len(result) == 4, "章が消失してはならない"
        assert starts == sorted(set(starts)), f"start_page が狭義単調増加でない: {starts}"
        assert starts[2] > starts[1], "C は B を追い越したまま残ってはならない"
