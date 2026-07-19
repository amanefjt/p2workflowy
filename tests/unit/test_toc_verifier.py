"""
toc_verifier のユニットテスト

テスト対象:
  - count_title_matches: 予測位置にタイトルがある章の数
  - detect_toc_shift: TOC エントリと頁番号の対応ずれの検出
  - apply_shift: 検出した shift の適用
  - verify_and_fix_toc: 上記を束ねた入口
"""

import pytest
from unittest.mock import MagicMock

from core.engine.p1_ingest.toc_verifier import (
    count_title_matches,
    detect_toc_shift,
    apply_shift,
    verify_and_fix_toc,
)


def normalize(text: str) -> str:
    """PDFSplitter._normalize_title と同等の簡易版（テスト用）。"""
    import re
    t = re.sub(r'^(?:Chapter|Chap\.?|Part)\s+[\dIVXivx]+\s*[.:]?\s*', '', text, flags=re.I)
    t = re.sub(r'^[\dIVXivx]+[.:]?\s+', '', t)
    t = re.sub(r'[^\w\s]', ' ', t)
    return ' '.join(t.lower().split())


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


class TestCountTitleMatches:
    def test_counts_entries_landing_on_their_own_title(self):
        # オフセット +2: 論理頁1→物理idx3、論理頁3→物理idx5
        doc = make_mock_doc([
            "表紙\n", "扉\n", "1\n本文\n",
            "Alpha Chapter\n本文\n",       # idx3
            "4\n本文\n",
            "Beta Chapter\n本文\n",        # idx5
        ])
        entries = [
            {"title": "Alpha Chapter", "start_page": 1},
            {"title": "Beta Chapter", "start_page": 3},
        ]
        assert count_title_matches(doc, entries, offset=2, shift=0, normalize=normalize) == 2

    def test_shift_moves_which_number_each_entry_uses(self):
        # 各エントリが「次のエントリの頁番号」を持っている（PSE 型のずれ）
        doc = make_mock_doc([
            "x\n", "x\n", "x\n",
            "Alpha Chapter\n本文\n",       # idx3 が Alpha の真の位置
            "x\n",
            "Beta Chapter\n本文\n",        # idx5 が Beta の真の位置
        ])
        # Alpha には Beta の頁(3)が、Beta にはさらに次の頁(5)が入っている
        entries = [
            {"title": "Alpha Chapter", "start_page": 3},
            {"title": "Beta Chapter", "start_page": 5},
        ]
        # shift=-1 で Alpha は「1つ前のエントリの頁番号」を使う…が先頭には無い。
        # Beta は Alpha の頁番号(3)を使い、offset=2 で idx5 に着地して一致する。
        assert count_title_matches(doc, entries, offset=2, shift=-1, normalize=normalize) == 1
        assert count_title_matches(doc, entries, offset=2, shift=0, normalize=normalize) == 0


class TestDetectTocShift:
    def test_detects_off_by_one(self):
        doc = make_mock_doc([
            "x\n", "x\n", "x\n",
            "Alpha Chapter\n本文\n",
            "x\n",
            "Beta Chapter\n本文\n",
            "x\n",
            "Gamma Chapter\n本文\n",
            "x\n",
            "Delta Chapter\n本文\n",
        ])
        entries = [
            {"title": "Alpha Chapter", "start_page": 3},
            {"title": "Beta Chapter", "start_page": 5},
            {"title": "Gamma Chapter", "start_page": 7},
            {"title": "Delta Chapter", "start_page": 9},
        ]
        assert detect_toc_shift(doc, entries, offset=2, normalize=normalize) == -1

    def test_returns_zero_when_toc_is_correct(self):
        doc = make_mock_doc([
            "x\n", "x\n",
            "Alpha Chapter\n本文\n",      # idx2
            "x\n",
            "Beta Chapter\n本文\n",       # idx4
            "x\n",
            "Gamma Chapter\n本文\n",      # idx6
        ])
        entries = [
            {"title": "Alpha Chapter", "start_page": 0},
            {"title": "Beta Chapter", "start_page": 2},
            {"title": "Gamma Chapter", "start_page": 4},
        ]
        assert detect_toc_shift(doc, entries, offset=2, normalize=normalize) == 0

    def test_returns_zero_when_evidence_is_thin(self):
        # 一致が少なく判断材料が乏しい場合は賭けに出ない
        doc = make_mock_doc(["x\n", "x\n", "Alpha\n"])
        entries = [{"title": "Alpha", "start_page": 0}]
        assert detect_toc_shift(doc, entries, offset=2, normalize=normalize) == 0

    def test_returns_zero_when_best_shift_below_min_matches(self):
        # best_shift=-1 が「最良」ではあるが、一致数が SHIFT_MIN_MATCHES(3) 未満
        # （2件）なので安全弁1で弾かれ、0（補正なし）を返す。
        # shift=0 / shift=1 は一致0件で、shift=-1(2件) がそれでも「最良」になる
        # ことが安全弁1を確実に検査するための鍵（best_shift==0 の早期リターンでは
        # 通らない）。
        doc = make_mock_doc([
            "x\n", "x\n", "x\n",           # idx0-2（idx2 は Delta の shift=0 着地点、空白）
            "x\n", "x\n",                  # idx3-4
            "Beta Chapter\n本文\n",        # idx5: entries[0].start_page(3)+offset(2)
            "x\n", "x\n",                  # idx6-7
            "Gamma Chapter\n本文\n",       # idx8: entries[1].start_page(6)+offset(2)
        ])
        entries = [
            {"title": "Alpha Chapter", "start_page": 3},
            {"title": "Beta Chapter", "start_page": 6},
            {"title": "Gamma Chapter", "start_page": 0},
        ]
        # shift=-1: Beta(i=1)・Gamma(i=2) が一致 → 2件。Alpha(i=0)は参照先が無く判定不可。
        # shift=0 / shift=1: どのエントリも自タイトルが着地先に無く 0件。
        # best_shift=-1, best=2 < SHIFT_MIN_MATCHES(3) → 0 を返す。
        assert detect_toc_shift(doc, entries, offset=2, normalize=normalize) == 0

    def test_returns_zero_when_dominance_insufficient(self):
        # best_shift=-1 の一致数は3件で SHIFT_MIN_MATCHES を満たすが、
        # 次点 shift=0 の一致数2件の SHIFT_DOMINANCE_RATIO(2.0)倍(=4件)に届かず
        # （3 < 4）、安全弁2で弾かれ 0（補正なし）を返す。
        # 同一頁に2章分のタイトルを併記することで、shift=-1 と shift=0 の双方が
        # 同じ頁で一致するよう仕込んでいる（部分一致判定を利用）。
        doc = make_mock_doc([
            "x\n", "x\n", "x\n",                                # idx0-2（idx2 は Delta の shift=0 着地点、空白）
            "x\n", "x\n",                                        # idx3-4
            "Alpha Chapter\nBeta Chapter\n本文\n",              # idx5: start_page(3)+offset(2)
            "x\n", "x\n",                                        # idx6-7
            "Beta Chapter\nGamma Chapter\n本文\n",              # idx8: start_page(6)+offset(2)
            "x\n", "x\n",                                        # idx9-10
            "Delta Chapter\n本文\n",                             # idx11: start_page(9)+offset(2)
        ])
        entries = [
            {"title": "Alpha Chapter", "start_page": 3},
            {"title": "Beta Chapter", "start_page": 6},
            {"title": "Gamma Chapter", "start_page": 9},
            {"title": "Delta Chapter", "start_page": 0},
        ]
        # shift=-1: Beta(i=1)@idx5, Gamma(i=2)@idx8, Delta(i=3)@idx11 が一致 → 3件。
        # shift=0:  Alpha(i=0)@idx5, Beta(i=1)@idx8 が一致 → 2件（Gamma/Deltaは不一致）。
        # shift=1:  一致なし → 0件。
        # best_shift=-1, best=3 は SHIFT_MIN_MATCHES(3) を満たすが、
        # runner_up=2 の2倍(4)未満なので dominance で弾かれ 0 を返す。
        assert detect_toc_shift(doc, entries, offset=2, normalize=normalize) == 0


class TestApplyShift:
    def test_shift_zero_returns_unchanged(self):
        entries = [{"title": "A", "start_page": 1}, {"title": "B", "start_page": 2}]
        assert apply_shift(entries, 0) == entries

    def test_negative_shift_takes_previous_entry_page(self):
        entries = [
            {"title": "A", "start_page": 10},
            {"title": "B", "start_page": 20},
            {"title": "C", "start_page": 30},
        ]
        result = apply_shift(entries, -1)
        # A は参照先が無いので頁番号を持たない扱い（None）にする
        assert result[0]["start_page"] is None
        assert result[1]["start_page"] == 10
        assert result[2]["start_page"] == 20

    def test_preserves_other_fields(self):
        entries = [
            {"title": "A", "start_page": 10, "role": "chapter"},
            {"title": "B", "start_page": 20, "role": "chapter"},
        ]
        result = apply_shift(entries, -1)
        assert result[1]["role"] == "chapter"
        assert result[1]["title"] == "B"


class TestVerifyAndFixToc:
    def test_returns_original_when_offset_cannot_be_estimated(self):
        doc = make_mock_doc(["本文だけ\n", "本文だけ\n"])
        entries = [{"title": "A", "start_page": 1}]
        assert verify_and_fix_toc(doc, entries, normalize) == entries

    def test_returns_original_for_empty_entries(self):
        doc = make_mock_doc(["1\n", "2\n"])
        assert verify_and_fix_toc(doc, [], normalize) == []
