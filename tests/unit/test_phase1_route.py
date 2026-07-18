"""
Phase 1 の実ルート記録（I-16 対応）に関するユニットテスト。
"""

from core.models import phase1_route_path, save_route_to_json, load_route_from_json


class TestPhase1RoutePath:
    def test_derives_sibling_path(self, tmp_path):
        phase1_path = tmp_path / "session123" / "phase1_preprocessor.json"
        result = phase1_route_path(str(phase1_path))
        assert result == str(tmp_path / "session123" / "phase1_route.json")


class TestSaveLoadRoute:
    def test_round_trip(self, tmp_path):
        route_path = str(tmp_path / "phase1_route.json")
        save_route_to_json("docling", route_path)
        assert load_route_from_json(route_path) == "docling"

    def test_load_missing_file_returns_none(self, tmp_path):
        missing_path = str(tmp_path / "does_not_exist.json")
        assert load_route_from_json(missing_path) is None


from unittest.mock import patch
from core.phase1_preprocessor import _run_phase1_pdf


class TestForceVlmRespectsPdfMode:
    def test_full_vlm_mode_skips_docling_even_if_viable(self, tmp_path):
        """I-16: pdf_mode='full_vlm' 指定時は is_docling_viable()=True でも Docling をスキップする。"""
        state_path = tmp_path / "phase1_preprocessor.json"
        fake_elements = [{"role": "vlm_page_source", "text": "# Chapter\nBody"}]

        with patch("core.phase1_preprocessor.is_docling_viable", return_value=True), \
             patch("core.phase1_preprocessor.docling_pdf_to_chunks") as mock_docling, \
             patch("core.phase1_preprocessor.run_pdf_ingestion", return_value=fake_elements):
            _run_phase1_pdf(
                "dummy.pdf", str(state_path),
                pdf_mode="full_vlm", save_state=True,
            )

        mock_docling.assert_not_called()
        from core.models import phase1_route_path, load_route_from_json
        assert load_route_from_json(phase1_route_path(str(state_path))) == "vlm"

    def test_hybrid_mode_uses_docling_when_viable(self, tmp_path):
        """既存動作の回帰確認: pdf_mode='hybrid'（既定）かつ Docling 可能ならDoclingルート。"""
        state_path = tmp_path / "phase1_preprocessor.json"
        from core.models import RawChunk

        with patch("core.phase1_preprocessor.is_docling_viable", return_value=True), \
             patch("core.phase1_preprocessor.docling_pdf_to_chunks",
                   return_value=[RawChunk(id="0", text="Title", role="h1", seq_index=0.0)]):
            _run_phase1_pdf(
                "dummy.pdf", str(state_path),
                pdf_mode="hybrid", save_state=True,
            )

        from core.models import phase1_route_path, load_route_from_json
        assert load_route_from_json(phase1_route_path(str(state_path))) == "docling"
