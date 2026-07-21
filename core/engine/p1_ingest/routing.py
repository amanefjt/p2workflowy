"""Phase 1 PDF 入力ルーティング規則（①〜④）。

書籍モード（book_manager.py）と論文モード（main.py / server.py）の両方から
共有される。判定は入力PDFファイル単位で1回だけ行うことを想定する。
"""

from typing import Optional


def decide_pdf_mode(
    explicit_pdf_mode: Optional[str], is_spread: bool, is_docling_ok: bool
) -> tuple[str, str]:
    """PDF処理ルートを決定する。

    ① ユーザーが pdf_mode を明示指定 → それを尊重
    ② 見開きスキャン → VLM ルート（Docling の読み順が未検証のため保守的に優先）
    ③ Docling 可能（デジタルPDF）→ Docling ルート
    ④ それ以外（スキャン等）→ VLM ルート

    戻り値は (pdf_mode, reason)。reason はログや routing 記録に使う。
    """
    if explicit_pdf_mode is not None:
        return explicit_pdf_mode, "explicit_pdf_mode"
    if is_spread:
        return "full_vlm", "spread_pdf"
    if is_docling_ok:
        return "hybrid", "docling_viable"
    return "full_vlm", "docling_not_viable"
