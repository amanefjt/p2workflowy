"""
見開きスキャン PDF を単ページ PDF に変換するユーティリティ。

検出: アスペクト比 > 1.1 で見開き候補と判定。
分割: 輝度積分 + スムージングによるノド（綴じ目）検出。
      ノド信頼性が低い場合は安全策として分割をスキップ。
出力: 元ファイルと同ディレクトリに {stem}_split.pdf としてキャッシュ。
"""

import fitz
import numpy as np
from pathlib import Path
from typing import Tuple, Optional

from .config import print_log

SPREAD_ASPECT_THRESHOLD = 1.1


def is_spread_pdf(pdf_path: str, sample_pages: int = 5) -> bool:
    """アスペクト比でPDFが見開きスキャンかどうかを判定する。"""
    doc = fitz.open(pdf_path)
    try:
        n = min(sample_pages, len(doc))
        spread_count = sum(
            1 for i in range(n)
            if _page_aspect(doc[i]) > SPREAD_ASPECT_THRESHOLD
        )
        return spread_count > n // 2
    finally:
        doc.close()


def split_spread_pdf(pdf_path: str, output_path: Optional[str] = None) -> str:
    """見開きスキャンPDFを1ページずつに分割した新PDFを作成する。

    キャッシュ済みの分割PDFが存在すれば再処理せずそのパスを返す。
    ノドが不鮮明なページは安全策として分割せず元のまま挿入する。

    Returns:
        分割済みPDF（または元PDF）のパス文字列。
    """
    input_path = Path(pdf_path)
    if output_path is None:
        output_path = str(input_path.parent / f"{input_path.stem}_split.pdf")

    if Path(output_path).exists():
        print_log(f"  [SpreadSplitter] キャッシュ済み分割PDFを使用: {Path(output_path).name}")
        return output_path

    doc = fitz.open(pdf_path)
    new_doc = fitz.open()
    split_count = skip_count = passthrough_count = 0

    try:
        for i in range(len(doc)):
            page = doc[i]
            r = page.rect

            if _page_aspect(page) <= SPREAD_ASPECT_THRESHOLD:
                new_doc.insert_pdf(doc, from_page=i, to_page=i)
                passthrough_count += 1
                continue

            gutter_pt, is_reliable = _find_gutter_x(page)

            if not is_reliable:
                # ノド検出が不確かでも中点で分割する（章境界検出優先）
                gutter_pt = page.rect.width / 2
                print_log(f"  [SpreadSplitter] P{i+1}: ノド不鮮明 → 中点({gutter_pt:.0f}pt)で分割")

            # 左ページ（verso）を先に挿入
            _insert_cropped(new_doc, doc, i, r,
                            src_rect=fitz.Rect(0, 0, gutter_pt, r.height),
                            dst_w=gutter_pt, dst_h=r.height)
            # 右ページ（recto）
            right_w = r.width - gutter_pt
            _insert_cropped(new_doc, doc, i, r,
                            src_rect=fitz.Rect(gutter_pt, 0, r.width, r.height),
                            dst_w=right_w, dst_h=r.height)
            split_count += 1

    finally:
        doc.close()

    new_doc.save(output_path, garbage=4, deflate=True)
    new_doc.close()

    total_out = split_count * 2 + skip_count + passthrough_count
    print_log(
        f"  [SpreadSplitter] 完了: {split_count}ページ分割 / {skip_count}ページスキップ"
        f" → {total_out}ページ PDF を保存: {Path(output_path).name}"
    )
    return output_path


# ─────────────────────────────────────────────
# 内部ヘルパー
# ─────────────────────────────────────────────

def _page_aspect(page: fitz.Page) -> float:
    r = page.rect
    return r.width / r.height if r.height > 0 else 0.0


def _find_gutter_x(page: fitz.Page, dpi: int = 72) -> Tuple[float, bool]:
    """ページのノド（綴じ目）X座標（pt単位）とその信頼性を返す。

    輝度値の縦積分 + ウィンドウ平均スムージングで最も明るい列（白い綴じ目）を検出。
    中央±20%の帯域のみ探索して誤検出を低減する。
    """
    pix = page.get_pixmap(dpi=dpi)
    samples = np.frombuffer(pix.samples, dtype=np.uint8)
    n_channels = len(samples) // (pix.width * pix.height)
    img = samples.reshape(pix.height, pix.width, n_channels)
    gray = img[:, :, :3].mean(axis=2) if n_channels >= 3 else img[:, :, 0].astype(float)

    height, width = gray.shape
    x_min = int(width * 0.40)
    x_max = int(width * 0.60)

    col_sums = gray[:, x_min:x_max].sum(axis=0).astype(float)

    window = 20
    if len(col_sums) > window:
        smoothed = np.convolve(col_sums, np.ones(window) / window, mode='valid')
        rel_idx = int(np.argmax(smoothed))
        max_val = float(smoothed.max())
        pixel_x = x_min + rel_idx + window // 2
    else:
        rel_idx = int(np.argmax(col_sums))
        max_val = float(col_sums.max())
        pixel_x = x_min + rel_idx

    avg_val = float(col_sums.mean())
    min_val = float(col_sums.min())

    # 信頼性判定:
    #   1. ピーク輝度がレンジの上位25%以上（白い紙でも有効）
    #   2. 最低限の絶対コントラストがある（均一ページを除外）
    #   3. 全体が飽和しすぎていない
    luminance_range = max_val - min_val
    is_reliable = (
        luminance_range > height * 10
        and (max_val - avg_val) / (luminance_range + 1) > 0.25
        and avg_val < 250.0 * height
    )

    # ピクセル座標 → PDF ポイント座標
    pt_x = pixel_x * (page.rect.width / pix.width)
    return pt_x, is_reliable


def _insert_cropped(
    new_doc: fitz.Document,
    src_doc: fitz.Document,
    page_idx: int,
    src_page_rect: fitz.Rect,
    src_rect: fitz.Rect,
    dst_w: float,
    dst_h: float,
) -> None:
    """src_doc の page_idx ページから src_rect 領域を切り出して new_doc に追加する。"""
    new_page = new_doc.new_page(width=dst_w, height=dst_h)
    new_page.show_pdf_page(
        fitz.Rect(0, 0, dst_w, dst_h),
        src_doc,
        page_idx,
        clip=src_rect,
    )
