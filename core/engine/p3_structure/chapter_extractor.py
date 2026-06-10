"""
書籍 PDF の章境界抽出。フォントサイズ統計と TOC 照合により
章タイトル・開始ページを決定する。
"""

import statistics
from pathlib import Path
from typing import List

import fitz

from core.config import print_log
from core.text_utils import _TRAILING_WORDS
from .heading_matcher import normalize_heading
from .toc_extractor import _matches_toc_entry, _should_join_lines

# --- 以下3定数は phase3_structure.py から verbatim 移設 ---
_RUNNING_HEADER_MAX = 9.5  # Running Header（柱）の最大サイズ
_FOOTNOTE_MAX = 8.5        # 脚注の最大サイズ


STOP_SECTIONS = {
    "notes", "references", "bibliography",
    "index", "index of names and places", "index of subjects",
}


def detect_chapter_font_sizes(
    doc: "fitz.Document",
    toc: List[dict],
    body_start_page: int = 1,
) -> set:
    """
    PDFの全フォントサイズを分析し、TOCとの照合でどのサイズが章タイトルかを特定する。
    """
    # --- Step 1: フォントサイズの全収集 ---
    all_sizes: List[float] = []
    # サイズ → そのサイズで出現したテキスト行の集合
    size_to_lines: dict[float, List[str]] = {}

    for page_num in range(1, len(doc) + 1):
        page = doc[page_num - 1]
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                line_parts = []
                line_max_size = 0.0
                for span in line.get("spans", []):
                    t = span.get("text", "").strip()
                    if t:
                        all_sizes.append(span["size"])
                        line_parts.append(t)
                        if span["size"] > line_max_size:
                            line_max_size = span["size"]

                line_text = "".join(line_parts).strip()
                line_max_size_val = float(line_max_size)
                if line_text and line_max_size_val > 0.0:
                    # サイズを0.5pt単位で丸めてグループ化（微妙な浮動小数点差を吸収）
                    rounded = round(line_max_size * 2) / 2
                    if rounded not in size_to_lines:
                        size_to_lines[rounded] = []
                    size_to_lines[rounded].append(line_text)

    if not all_sizes:
        print_log("  [Phase 3] フォントサイズ取得失敗 → フォールバック閾値を使用")
        return set()

    median_size = statistics.median(all_sizes)
    print_log(f"  [Phase 3] 本文フォントサイズ（中央値）: {median_size:.2f}pt")

    # --- Step 2: 本文より大きいサイズの候補一覧 ---
    # 中央値よりわずかでも大きいものを候補とする（11.0pt vs 10.6pt 等を拾うため 0.3pt の余裕を持たせる）
    candidate_sizes = {
        size for size in size_to_lines.keys()
        if size >= median_size + 0.3
    }
    print_log(f"  [Phase 3] 章タイトル候補サイズ: {sorted(candidate_sizes)}")

    # --- Step 3: TOCとの照合 ---
    # ページ番号が不明（ローマ字ページ等）な TOC も含めてサイズ判定に活用する
    toc_valid = [e for e in toc if e.get("title")]
    if not toc_valid:
        fallback = {s for s in candidate_sizes if s >= median_size + 1.0}
        print_log(f"  [Phase 3] TOCなし → フォールバック: {sorted(fallback)}")
        return fallback

    # TOCタイトルを正規化しておく
    toc_norms = [normalize_heading(e["title"]) for e in toc_valid]

    confirmed_base_sizes: set[float] = set()
    for size in sorted(candidate_sizes):
        lines_at_size = size_to_lines[size]
        for line in lines_at_size:
            norm_line = normalize_heading(line)
            if not norm_line:
                continue
            
            # TOCタイトルとの照合 (ヘルパー関数を利用して一貫性を確保)
            if _matches_toc_entry(norm_line, toc_valid):
                confirmed_base_sizes.add(size)
                break

    print_log(f"  [Phase 3] TOC照合で直接確認されたサイズ: {sorted(confirmed_base_sizes)}")

    # 照合できたサイズ周辺を救済し、階層差やフォントの揺れをカバー
    # 例: 12.5pt が Chapter なら、その下の階層や、わずかに小さい Preface (11.0pt) 等も拾うため
    # confirmed_base_sizes の -1.5pt から +1.5pt 程度までを許容範囲とする
    confirmed_sizes: set[float] = set()
    for base_s in confirmed_base_sizes:
        for cand_s in candidate_sizes:
            if base_s - 1.5 <= cand_s <= base_s + 1.5:
                confirmed_sizes.add(cand_s)

    # 一件もマッチしなかった場合のフォールバック
    if not confirmed_sizes:
        fallback = {s for s in candidate_sizes if s >= median_size + 1.0}
        print_log(f"  [Phase 3] TOC照合マッチなし → フォールバック: {sorted(fallback)}")
        return fallback

    print_log(f"  [Phase 3] 章タイトル確定サイズ（救済後）: {sorted(confirmed_sizes)}")
    return confirmed_sizes


def extract_book_chapters(
    pdf_path: str | Path,
    body_start_page: int = 1,
    toc: List[dict] | None = None,
) -> List[dict]:
    """
    PyMuPDF のフォントメタデータを使って PDF から章構造を抽出する。

    フォント判定:
    - 12.0〜20.0pt : 章タイトル → 章境界
    - 9.25pt + 「·」 : Running Header（柱） → 除去
    - 8.5pt以下     : 脚注 → 除去
    - それ以外       : 本文 → 段落として採用

    Returns:
        List[dict]: [
            {
                "title": "Preface",
                "start_page": 7,
                "paragraphs": ["text1", "text2", ...]
            },
            ...
        ]
        STOP_SECTIONS（Notes/References/Index）は含まない。
    """
    doc = fitz.open(str(pdf_path))
    print_log(f"  [Phase 3] PyMuPDF: {len(doc)}ページ / body_start_page={body_start_page}")

    # --- 動的フォントサイズ判定 ---
    chapter_sizes = detect_chapter_font_sizes(doc, toc or [], body_start_page)
    if not chapter_sizes:
        # 検出失敗時の最終フォールバック（旧固定値）
        chapter_sizes = {s / 10 for s in range(108, 201)}  # 10.8〜20.0の全値
        print_log("  [Phase 3] サイズ検出失敗 → 旧固定値範囲にフォールバック")
    chapter_size_min = min(chapter_sizes)
    chapter_size_max = max(chapter_sizes)
    print_log(f"  [Phase 3] 章タイトル判定範囲: {chapter_size_min:.1f}〜{chapter_size_max:.1f}pt")

    # TOC に page=-1 で登録されている前付けタイトル（Preface, Foreword 等）を収集
    # body_start_page 以前のページでも、これらのタイトルが出現したら章として扱う
    front_matter_titles = {
        normalize_heading(e["title"])
        for e in (toc or [])
        if e.get("page") == -1 and e.get("title")
    }
    # 一度章として開始したタイトルはここに移し、目次等での再マッチを防ぐ
    used_front_matter_titles: set[str] = set()
    if front_matter_titles:
        print_log(f"  [Phase 3] 前付けタイトル検出: {front_matter_titles}")

    chapters: List[dict] = []
    current_title: str | None = None
    current_paragraphs: List[str] = []
    current_start_page: int = 0
    
    pending_title: str | None = None
    pending_start_page: int = 0
    
    stopped = False

    for page_num, page in enumerate(doc, 1):
        if page_num < body_start_page:
            # 前付けページ: TOC にある front_matter_titles のテキストが出現するページのみ処理
            if not front_matter_titles:
                continue
            # ページ内のテキストを走査して front_matter_titles と照合
            for block in page.get_text("dict")["blocks"]:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    line_text = "".join(
                        span["text"] for span in line.get("spans", [])
                    ).strip()
                    if not line_text:
                        continue
                    norm_line = normalize_heading(line_text)
                    # used_front_matter_titles に含まれるものは重複マッチとして無視
                    if norm_line in front_matter_titles and norm_line not in used_front_matter_titles:
                        # 前付けの見出しを発見 → 前の章を確定して新章を開始
                        if current_title is not None and current_paragraphs:
                            chapters.append({
                                "title": current_title,
                                "start_page": current_start_page,
                                "paragraphs": current_paragraphs,
                            })
                        current_title = line_text
                        current_start_page = page_num
                        current_paragraphs = []
                        used_front_matter_titles.add(norm_line)  # 使用済みに登録
                        print_log(f"  [Phase 3] 前付け章確定 p{page_num}: '{line_text[:60]}'")
                    elif current_title is not None and normalize_heading(current_title) in used_front_matter_titles:
                        # 前付け章の本文として収集
                        if not current_paragraphs:
                            current_paragraphs.append(line_text)
                        else:
                            prev = current_paragraphs[-1]
                            if _should_join_lines(prev, line_text):
                                assert isinstance(current_paragraphs, list)
                                if prev.endswith("-"):
                                    current_paragraphs[-1] = prev[:-1] + line_text
                                else:
                                    current_paragraphs[-1] = prev + " " + line_text
                            else:
                                current_paragraphs.append(line_text)
            continue  # 前付けページの処理完了
        if stopped:
            break

        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:  # テキストブロックのみ
                continue

            for line in block.get("lines", []):
                # スパンを結合して行のテキスト、最大フォントサイズ、イタリック属性を確認
                line_parts = []
                max_size = 0.0
                has_dot = False
                is_italic = False

                for span in line.get("spans", []):
                    line_parts.append(span["text"])
                    if span["size"] > max_size:
                        max_size = span["size"]
                    if "·" in span["text"]:
                        has_dot = True
                    font_name = span.get("font", "").lower()
                    if "ital" in font_name:
                        is_italic = True

                line_text = "".join(line_parts).strip()
                if not line_text:
                    continue

                # --- 停止判定 (STOP_SECTIONS) ---
                # Notes/References 等は 11.0pt Italic 等、本文より少し目立つ場合がある
                # また、本来の章タイトルサイズ（12.6pt）で出現することもある
                if (line_text.lower() in STOP_SECTIONS) and (max_size >= 11.0):
                    print_log(f"  [Phase 3] STOP: '{line_text}' detected (size={max_size:.1f})")
                    # 現在進行中の章または保留中のタイトルがあれば確定させてリストへ
                    # pending_title があればそれを優先して確定
                    if pending_title is not None:
                        chapters.append({
                            "title": pending_title,
                            "start_page": pending_start_page,
                            "paragraphs": [], # STOPセクションなので本文は含めない
                        })
                        print_log(f"  [Phase 3] 空章確定（STOP前） p{pending_start_page}: '{pending_title[:60]}'")
                        pending_title = None # 確定したのでクリア
                    
                    # current_title があればそれを確定
                    elif current_title is not None and current_paragraphs:
                        chapters.append({
                            "title": current_title,
                            "start_page": current_start_page,
                            "paragraphs": current_paragraphs,
                        })
                        print_log(f"  [Phase 3] 章確定（STOP前） p{current_start_page}: '{current_title[:60]}'")
                    
                    stopped = True
                    break

                # --- フィルタリング ---
                # 定型ノイズを見出しとして拾わない
                _NOISE_HEADINGS = {
                    "contents", "this page intentionally left blank",
                    "table of contents",
                }
                if line_text.lower() in _NOISE_HEADINGS and max_size >= chapter_size_min:
                    continue

                # Running Header: 9.5pt以下 かつ「·」を含む
                if max_size <= _RUNNING_HEADER_MAX and has_dot:
                    continue
                # 脚注: 8.5pt以下
                if max_size <= _FOOTNOTE_MAX:
                    continue
                # 書籍タイトル等の巨大テキスト: 除去
                if max_size > chapter_size_max:
                    continue

                # --- 章タイトル検出 (Stateful Coalescing & Hybrid Verification) ---
                max_size_val = float(max_size)
                rounded_size = round(max_size_val * 2) / 2
                is_heading_size = (rounded_size in chapter_sizes)
                is_valid_heading = is_heading_size
                
                if is_heading_size:
                    if toc:
                        # [戦略A: TOCが存在する場合は、TOCと厳密に照合する (DRY原則)]
                        norm_line = normalize_heading(line_text)
                        is_valid_heading = _matches_toc_entry(norm_line, toc)
                    else:
                        # [戦略B: TOCがない場合（抽出失敗時）は、テキスト形状によるヒューリスティクスで防御する]
                        if line_text[0].islower():
                            is_valid_heading = False
                        elif len(line_text) > 80:
                            is_valid_heading = False
                        else:
                            last_word = line_text.split()[-1].lower().rstrip(".,;:!?")
                            if last_word in _TRAILING_WORDS:
                                is_valid_heading = False

                # 3. 判定結果に基づくステートマシンのルーティング
                if is_valid_heading:
                    # 正当な見出しとしての処理
                    if pending_title is not None:
                        if page_num == pending_start_page:
                            pending_title = f"{pending_title} {line_text}"
                        else:
                            chapters.append({
                                "title": pending_title,
                                "start_page": pending_start_page,
                                "paragraphs": [],
                            })
                            print_log(f"  [Phase 3] 空章確定（ページ跨ぎ） p{pending_start_page}: '{pending_title[:60]}'")
                            pending_title = line_text
                            pending_start_page = page_num
                    else:
                        if current_title is not None:
                            chapters.append({
                                "title": current_title,
                                "start_page": current_start_page,
                                "paragraphs": current_paragraphs,
                            })
                        pending_title = line_text
                        pending_start_page = page_num
                else:
                    # 本文（または見出しとして却下された行）としての処理
                    if pending_title is not None:
                        current_title = pending_title
                        current_start_page = pending_start_page
                        current_paragraphs = []
                        pending_title = None
                        print_log(f"  [Phase 3] 章確定 p{current_start_page}: '{current_title[:60]}'")
                    
                    # 最初期（章確定前）の場合は Forward/Intro
                    if current_title is None:
                        current_title = "[Forward/Intro]"
                        current_start_page = page_num
                        current_paragraphs = []

                    # --- 段落の再構築ロジック ---
                    if not current_paragraphs:
                        current_paragraphs.append(line_text)
                    else:
                        prev_line = current_paragraphs[-1]
                        if _should_join_lines(prev_line, line_text):
                            assert isinstance(current_paragraphs, list)
                            # 結合（ハイフン除去対応）
                            if prev_line.endswith("-"):
                                current_paragraphs[-1] = prev_line[:-1] + line_text
                            else:
                                current_paragraphs[-1] = prev_line + " " + line_text
                        else:
                            current_paragraphs.append(line_text)

            if stopped:
                break

    # 最後の章を保存（STOP しなかった場合）
    if not stopped:
        final_title = pending_title if pending_title else current_title
        final_start_page = pending_start_page if pending_title else current_start_page
        if final_title and current_paragraphs:
            chapters.append({
                "title": final_title,
                "start_page": final_start_page,
                "paragraphs": current_paragraphs,
            })

    # 【追加】ゴーストチャプター防止: テキストブロックが1つも抽出されなかった場合のガード
    if not chapters:
        print_log("  [Phase 3] 警告: 有効な章・テキストが1つも抽出されませんでした。")
        return []

    print_log(f"  [Phase 3] 章抽出完了: {len(chapters)}章")
    for ch in chapters:
        print_log(f"    p{ch['start_page']:3d} | {ch['title'][:50]:50s} | {len(ch['paragraphs'])}段落")

    return chapters
