"""章境界の逸脱検出（層2）と LLM 裁定（層3）。

層2は「前の確定章と次の確定章の双方とオフセットが食い違う章」を要審査とする。
逸脱幅の閾値は使えない — relations の正当な段差は 2、Naven の誤りは 1 であり、
大小で正誤を区別できないため（実測 2026-07-19）。

層3（BoundaryAdjudicator）は層2が要審査とした章のみ LLM に章扉頁を裁定させる。
窓は前後の確定章に挟まれた物理区間で、論理頁の正しさに依存しない。LLM の返り値は
無条件に信じず区間内・単調性を機械検証し、不合格・null・API キーなし・例外の
いずれでも安全側の基準値へ落とす。

spec: docs/superpowers/specs/2026-07-19-chapter-boundary-adjudication-design.md §2.3, §2.4, §2.5
"""

import json
from dataclasses import dataclass
from typing import Any, List, Optional, Set

from core.config import print_log
from core.llm_client import call_gemini


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


# 層3: LLM 裁定の設定
ADJUDICATION_HEAD_LINES = 15       # 各候補頁から見せる行数（_classify_match と同じ）
ADJUDICATION_MAX_PAGES = 32        # 候補区間の上限（トークン量の上限を決める）


class BoundaryAdjudicator:
    """要審査の章について、LLM に章扉頁を裁定させる（層3）。

    LLM が結論を出せない場合の基準値は、その章がどう要審査になったかで分ける。
      - 照合が成立した章 → 既存の機械照合の結果を維持する（実在の一致であり安全）
      - フォールバック章 → 論理頁 + 補間オフセット（機械照合の値はオフセット0で
        あり、それ自体が I-27 の症状であるため維持してはならない）
    """

    def __init__(self, api_key: Optional[str], model: str, cache: dict, save_cache):
        self.api_key = api_key
        self.model = model
        self.cache = cache
        self._save_cache = save_cache

    def adjudicate(
        self, doc: Any, placements: List[ChapterPlacement], pdf_hash: str
    ) -> List[ChapterPlacement]:
        """要審査の章を裁定し、更新した配置リストを返す。"""
        suspects = flag_suspects(placements)
        if not suspects:
            return placements

        print_log(f"  [Adjudicator] 要審査の章: {len(suspects)}件")
        result = list(placements)

        for index in sorted(suspects):
            target = result[index]
            lower, upper = self._interval(result, index, len(doc))
            if lower > upper:
                continue

            decided = None
            if self.api_key:
                decided = self._ask_llm(doc, target, lower, upper, pdf_hash)

            # LLM の返り値を機械検証する。区間外（＝単調性違反を含む。lower/upper は
            # 前後の確定章から算出されるため、区間内であることが単調性そのもの）なら
            # 不採用とし、基準値へ落とす（spec §2.4 の「不合格」経路）。
            if decided is not None and not (lower <= decided <= upper):
                decided = None

            # 不合格・null・APIキー不在・例外 → 基準値（spec §2.5）
            if decided is None:
                decided = self._baseline(result, index, len(doc))

            if decided is None or decided == target.start_page:
                continue
            # 基準値が区間外になる稀なケース（fallback の補間が区間を外れる）は
            # 補正を見送り元の値を維持する（現状より悪化させない）。
            if not (lower <= decided <= upper):
                continue

            print_log(
                f"  [Adjudicator] 境界を補正: '{target.title[:40]}' "
                f"物理P{target.start_page + 1} → P{decided + 1}"
            )
            result[index] = ChapterPlacement(
                index=target.index, title=target.title,
                logical_page=target.logical_page, start_page=decided,
                matched=target.matched,
            )

        return self._enforce_monotonic(result)

    def _enforce_monotonic(
        self, result: List[ChapterPlacement]
    ) -> List[ChapterPlacement]:
        """全補正適用後、start_page の狭義単調増加を強制する（spec §2.4）。

        層3の LLM 経路は非単調化しうる。区間検証は前後の**確定章**だけを境界に
        使うため、同一確定区間に挟まれた複数の要審査章が独立に裁定されると、
        LLM が順序を誤ったとき互いに追い越しうる（例: A=確定10, B・C=fallback,
        D=確定50 で LLM が B→40・C→20 を返すと [10,40,20,50] になる）。決定論的な
        baseline 経路は同一オフセットの補間で必ず昇順になるため安全で、crossover を
        生むのは LLM 経路のみ。この失敗モードは、まさに層3が対象とする「fallback が
        クラスタ化する書籍」（層1適用前の PSE は12連続 fallback）で現実に起こりうる。

        単調性が破れると `split()` は `start_page > end_page` の章を**無言で
        スキップ**し章が消失する。警告だけでは防げないため、違反章を直前章の
        直後（prev+1）へ前方クランプして単調性を回復する。クランプ後の位置は
        真の扉頁とは限らないが、章の消失・頁範囲の重複よりは安全側である。
        """
        prev_sp = -1
        adjusted = []
        for p in result:
            if p.start_page <= prev_sp:
                clamped = prev_sp + 1
                print_log(
                    f"  [Adjudicator] 単調性を修復: '{p.title[:30]}' "
                    f"P{p.start_page + 1} → P{clamped + 1}（前章 P{prev_sp + 1} と"
                    f"の追い越しを解消。LLM 裁定の順序誤りの可能性）。"
                )
                p = ChapterPlacement(
                    index=p.index, title=p.title, logical_page=p.logical_page,
                    start_page=clamped, matched=p.matched,
                )
            adjusted.append(p)
            prev_sp = p.start_page
        return adjusted

    def _interval(
        self, placements: List[ChapterPlacement], index: int, total_pages: int
    ) -> tuple:
        """前後の確定章に挟まれた物理頁の区間（両端含む）を返す。

        真の章扉は定義上この区間全体のどこかにあるため、論理頁の正しさに依存しない。
        これにより、論理頁が誤っているために要審査になった章でも窓が正しく張れる。

        ただし区間そのもの（＝確定章ペア間の物理頁差）が ADJUDICATION_MAX_PAGES を
        超える場合は、LLM に渡すトークン量を抑えるため区間全体ではなく窓幅で絞り込む
        必要がある。旧実装は index を無視して常に区間の先頭から窓幅ぶんを返していたため、
        同じ確定章ペアに挟まれた複数の要審査章（fallback クラスタ）が全員まったく同じ窓を
        割り当てられ、クラスタ後方の章は真の扉頁が窓外になり裁定できなかった（PSE の
        12連続 fallback で顕在化。2026-07-21 レビュー指摘）。この場合は対象章の論理頁が
        prev/nxt の論理頁の間でどの比率に位置するかを使って窓の中心を推定し、章ごとに
        異なる窓を割り当てる。
        """
        prev = _confirmed_neighbour(placements, index, -1)
        nxt = _confirmed_neighbour(placements, index, +1)

        lower_bound = prev.start_page + 1 if prev is not None else 0
        upper_bound = nxt.start_page - 1 if nxt is not None else total_pages - 1
        upper_bound = min(upper_bound, total_pages - 1)

        if upper_bound - lower_bound + 1 <= ADJUDICATION_MAX_PAGES:
            return lower_bound, upper_bound

        if prev is not None and nxt is not None and nxt.logical_page != prev.logical_page:
            target = placements[index]
            ratio = (target.logical_page - prev.logical_page) / (nxt.logical_page - prev.logical_page)
            ratio = min(max(ratio, 0.0), 1.0)
        else:
            ratio = 0.0
        center = lower_bound + round(ratio * (upper_bound - lower_bound))
        half = ADJUDICATION_MAX_PAGES // 2

        win_lower = max(lower_bound, center - half)
        win_upper = min(upper_bound, win_lower + ADJUDICATION_MAX_PAGES - 1)
        win_lower = max(lower_bound, win_upper - ADJUDICATION_MAX_PAGES + 1)
        return win_lower, win_upper

    def _baseline(
        self, placements: List[ChapterPlacement], index: int, total_pages: int
    ) -> Optional[int]:
        """LLM が結論を出せない場合の基準値（spec §2.5）。"""
        target = placements[index]
        if target.matched:
            return target.start_page

        offset = interpolated_offset(placements, index)
        if offset is None:
            return target.start_page

        candidate = target.logical_page + offset
        return max(0, min(candidate, total_pages - 1))

    def _ask_llm(
        self, doc: Any, target: ChapterPlacement, lower: int, upper: int, pdf_hash: str
    ) -> Optional[int]:
        """候補区間を見せて章扉頁を尋ねる。判断できない場合は None を返す。"""
        cache_key = f"{pdf_hash}_adjudicate_{target.index}_{target.title}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        blocks = []
        for idx in range(lower, upper + 1):
            lines = [l.strip() for l in doc[idx].get_text("text").split("\n") if l.strip()]
            body = "\n".join(lines[:ADJUDICATION_HEAD_LINES])
            blocks.append(f"--- 物理ページ {idx} ---\n{body}")

        from core.llm_client import load_coreprompts
        prompts = load_coreprompts()
        template = prompts.get("CHAPTER_OPENER_ADJUDICATION_PROMPT", "")
        if not template:
            return None
        prompt = (template
                  .replace("{pages}", "\n\n".join(blocks))
                  .replace("{title}", target.title)
                  .replace("{current}", str(target.start_page)))

        try:
            response = call_gemini(
                prompt, api_key=self.api_key, model=self.model,
                response_mime_type="application/json",
            )
            data = json.loads(response)
            page = data.get("page")
            if page is None:
                decided = None
            else:
                decided = int(page)
        except Exception as e:
            print_log(f"  [Adjudicator] 裁定エラー ('{target.title[:30]}'): {e}")
            return None

        self.cache[cache_key] = decided
        self._save_cache()
        return decided
