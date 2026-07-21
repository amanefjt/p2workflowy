"""
core/engine/p1_ingest/routing.py の①〜④ルーティング規則テスト。

書籍モード（book_manager.py）専用だった判定ロジックを論文モード（main.py /
server.py）とも共有できるよう切り出した。book_manager.py 側は
_decide_book_pdf_mode としてこの関数をそのまま re-export している
（tests/unit/test_book_manager.py::TestDecideBookPdfMode が別途カバー）。
"""

from core.engine.p1_ingest.routing import decide_pdf_mode


class TestDecidePdfMode:
    def test_explicit_pdf_mode_takes_priority(self):
        """規則①: ユーザー明示指定は他の判定より優先される。"""
        mode, reason = decide_pdf_mode("full_vlm", is_spread=False, is_docling_ok=True)
        assert (mode, reason) == ("full_vlm", "explicit_pdf_mode")

    def test_spread_pdf_forces_vlm_even_if_docling_viable(self):
        """規則②>③: 見開きスキャンはDocling可能でもVLMを優先する。"""
        mode, reason = decide_pdf_mode(None, is_spread=True, is_docling_ok=True)
        assert (mode, reason) == ("full_vlm", "spread_pdf")

    def test_docling_viable_non_spread_uses_hybrid(self):
        """規則③: 見開きでなくDocling可能ならDoclingルート（hybrid=Docling優先）。"""
        mode, reason = decide_pdf_mode(None, is_spread=False, is_docling_ok=True)
        assert (mode, reason) == ("hybrid", "docling_viable")

    def test_non_viable_non_spread_falls_back_to_vlm(self):
        """規則④: 見開きでもDocling可能でもない（劣化スキャン等）ならVLM。

        論文モード（main.py）が以前 "hybrid" 固定だったために踏んでいたバグ
        （Docling不可PDFでVLMフォールバックが働かず物理抽出に静落ちする）
        の回帰テスト。
        """
        mode, reason = decide_pdf_mode(None, is_spread=False, is_docling_ok=False)
        assert (mode, reason) == ("full_vlm", "docling_not_viable")
