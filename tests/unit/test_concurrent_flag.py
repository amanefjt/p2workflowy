"""concurrent_sections が ParallelTranslator まで届くことを確認するテスト。"""
from unittest.mock import patch, MagicMock, AsyncMock
import pytest
from core.engine.p4_translate.parallel_translator import ParallelTranslator


def test_parallel_translator_accepts_concurrent_param():
    """ParallelTranslator がカスタム concurrent_sections を受け取ること。"""
    translator = ParallelTranslator(max_concurrent_sections=2)
    assert translator.semaphore._value == 2


def test_parallel_translator_default_concurrent():
    """デフォルト値が 8 であること（2026-07-22、無料枠Liteプールのラウンドロビン実装後の
    実測でconcurrent=8がconcurrent=4より速いことを確認し、デフォルトを4→8に変更した）。"""
    translator = ParallelTranslator()
    assert translator.semaphore._value == 8


@patch("core.phase4_translate.ParallelTranslator")
def test_run_phase4_passes_concurrent_to_translator(mock_translator_cls):
    """run_phase4 の max_concurrent_sections が ParallelTranslator に渡されること。"""
    from core.phase4_translate import _run_phase4_async
    import asyncio, json
    from pathlib import Path
    import tempfile

    # 最小限のフェイクstate ファイルを用意
    with tempfile.TemporaryDirectory() as tmpdir:
        sections_path = Path(tmpdir) / "sections.json"
        structure_path = Path(tmpdir) / "structure.json"
        phase2_path = Path(tmpdir) / "phase2.json"
        phase4_path = Path(tmpdir) / "phase4.json"

        sections_path.write_text(json.dumps({}))
        structure_path.write_text(json.dumps([]))
        phase2_path.write_text(json.dumps({"resume_content": "", "keywords_data": []}))

        mock_translator_cls.return_value = MagicMock()
        mock_translator_cls.return_value.translate_section_chunks = AsyncMock(return_value=[])

        asyncio.run(_run_phase4_async(
            phase2_state_path=phase2_path,
            structure_state_path=structure_path,
            sections_state_path=sections_path,
            phase4_state_path=phase4_path,
            glossary_path=None,
            api_key="dummy",
            max_concurrent_sections=2,
        ))

        mock_translator_cls.assert_called_once()
        call_kwargs = mock_translator_cls.call_args.kwargs
        assert call_kwargs.get("max_concurrent_sections") == 2
