"""
p2workflowy V2 Phase 3: Structuring & Clipping
Pre-scanner + Section Detector + 除外クリッピング → 英語ツリー構築。
indi_pre_scanner.md / indi_section_detector.md に完全準拠。
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

import fitz  # PyMuPDF

import statistics

from .config import (print_log, 
    load_coreprompts,
)
from .models import RawChunk, TreeNode, load_chunks_from_json, save_tree_to_json
from .text_utils import _SENTENCE_END_RE, _TRAILING_WORDS
from .engine.p3_structure.heading_matcher import (
    normalize_heading,
    is_excluded_heading,
    match_heading,
    extract_headings_from_resume,
)
from .engine.p3_structure.tree_builder import (
    structure_nodes_by_headings,
    build_tree,
    structure_nodes_by_markdown,
)


# ============================================================
# Book Mode: PyMuPDF によるフォントベース章抽出
# ============================================================

_RUNNING_HEADER_MAX = 9.5  # Running Header（柱）の最大サイズ
_FOOTNOTE_MAX = 8.5        # 脚注の最大サイズ

# (定数は .text_utils からインポート)

def _should_join_lines(prev_line: str, next_line: str) -> bool:
    """前の行と次の行を結合すべきか（改行を消すべきか）を判定する。"""
    p = prev_line.strip()
    n = next_line.strip()
    if not p or not n: return False
    
    # 次の行が小文字で始まる場合は、文の途中とみなす
    if n[0].islower(): return True
    # 前の行がハイフンで終わる
    if p.endswith("-"): return True
    # 前の行がカンマで終わる
    if p.endswith(","): return True
    # 前の行の末尾が特定の単語（前置詞等）
    last_word = p.split()[-1].lower().rstrip(".,;:!?")
    if last_word in _TRAILING_WORDS: return True
    # 前の行が文末記号で終わっていない
    if not _SENTENCE_END_RE.search(p):
        # ただし、次の行が「見出し候補」の形状であれば結合しない。
        # 見出しの特徴: 大文字始まり / 末尾が trailing word でない / 120文字以下
        # 例: "Comparisons of Comparisons—1", "Theoretical Heterogeneity"
        if n[0].isupper():
            last_word_n = n.split()[-1].lower().rstrip(".,;:!?—\u2014")
            if last_word_n not in _TRAILING_WORDS and len(n) <= 120:
                return False  # 見出し候補 → 結合しない
        return True

    return False


def _matches_toc_entry(norm_line: str, toc: List[dict]) -> bool:
    """
    正規化済みテキストがTOCのいずれかのエントリと部分一致するか判定する。
    誤爆を防ぐため、3文字未満の文字列は判定から除外する。
    """
    if len(norm_line) < 3:
        return False
        
    for e in toc:
        # ページ番号が -999 (未抽出) のものは無視する
        if e.get("page", -1) == -999:
            continue
        norm_toc = normalize_heading(e["title"])
        if len(norm_toc) < 3:
            continue
            
        # TOCタイトルが行に含まれている、またはその逆ならOK (部分一致)
        if norm_line in norm_toc or norm_toc in norm_line:
            return True
            
    return False


STOP_SECTIONS = {
    "notes", "references", "bibliography",
    "index", "index of names and places", "index of subjects",
}


def extract_toc_via_llm(pdf_path: str | Path, api_key: str | None = None, model: str | None = None, state: Any = None) -> dict:
    """
    PDF冒頭からTOCをLLMで抽出する。
    
    Returns:
        {
            "toc": [{"title": "Introductions: The Compulsion of Relations", "page": 1}, ...],
            "body_start_page": 15   # アラビア数字ページ1がはじまるPDF物理ページ番号
        }
    """
    from .llm_client import call_gemini

    doc = fitz.open(str(pdf_path))
    # 冒頭15ページのテキストを取得（TOCが掲載されている範囲）
    pages_text = []
    for i in range(min(40, len(doc))):
        pages_text.append(f"[PDF Page {i+1}]\n{doc[i].get_text()}")
    text_for_llm = "\n\n".join(pages_text)

    prompt = f"""以下はPDF書籍の冒頭ページのテキストです。

タスク:
1. 目次（Contents / Table of Contents）を見つけて、章タイトルとアラビア数字ページ番号のリストを抽出してください。
2. アラビア数字ページ番号1（本文最初のページ）が、PDF物理何ページ目に相当するかを特定してください。
3. 【重要】目次が数ページにわたる場合でも、途中で省略せず、最後の章（Conclusions等）まで「すべて」完全に抽出してください。

出力形式（JSONのみ、前後の説明文なし）:
{{
  "toc": [
    {{"title": "Preface", "page": -1}},
    {{"title": "Acknowledgments", "page": -1}},
    {{"title": "Introductions: The Compulsion of Relations", "page": 1}},
    {{"title": "1. Experimentations, English and Otherwise", "page": 25}}
  ],
  "body_start_page": 15
}}

注意:
- titleは目次に記載されている完全なタイトルをそのまま使用すること（省略しない）
- PrefaceやAcknowledgmentsなどの前付け（ローマ数字ページ）のpageは -1 としてください
- body_start_pageは「本文のアラビア数字ページ1」が始まるPDF物理ページ番号
- Notes / References / Index は含めない

テキスト:
{text_for_llm}
"""

    response = call_gemini(
        prompt,
        api_key=api_key,
        temperature=0.2,
        model=model,
        max_output_tokens=4096,
        log_dir=state.logs_dir if state else None,
        metrics_metadata={"section": "toc_extraction"},
        response_mime_type="application/json",
    )

    try:
        # JSON フェンスを除去してパース
        clean = re.sub(r"```(?:json)?\s*|\s*```", "", response).strip()
        data = json.loads(clean)
        toc = data.get("toc", [])
        body_start_page = data.get("body_start_page", 1)
        # 有効なエントリ（page=-1 または page>0）を保持
        filtered_toc = [e for e in toc if e.get("page", -1) != -999]
        print_log(f"  [Phase 3] TOC抽出完了: {len(filtered_toc)}件, body_start_page={body_start_page}")
        return {"toc": filtered_toc, "body_start_page": body_start_page}
    except Exception as e:
        print_log(f"  [Phase 3] TOC抽出失敗: {e} → タイトル補正をスキップ")
        return {"toc": [], "body_start_page": 1}


def extract_toc_from_chunks(
    chunks: List[RawChunk],
    api_key: str | None = None,
    model: str | None = None,
) -> List[str]:
    """
    VLMが精製したクリーンなチャンクテキスト（冒頭部分）をLLMに渡し、
    書籍の目次（章タイトルのリスト）を抽出する。
    """
    from .llm_client import call_gemini
    
    # 冒頭 100 チャンク程度（目次が含まれる十分な範囲）を結合
    sample_size = min(100, len(chunks))
    text_for_llm = "\n\n".join([c.text for c in chunks[:sample_size]])

    prompt = f"""以下は書籍の冒頭部分のテキストです。
このテキストの中から「目次（Table of Contents）」を特定し、
「章タイトル」のリストのみを抽出してJSON形式で返してください。

【制約】
1. 出力は以下のJSON形式のみとし、説明文などは一切含めないでください。
2. ページ番号は不要です。タイトル文字列のみをリストにしてください。
3. 節（Section）の見出しは含めず、トップレベルの「章（Chapter）」の見出しのみを抽出してください。
4. 目次にない文字列は含めないでください。

出力形式:
{{
  "toc": [
    "Preface",
    "1. Experimentations, English and Otherwise",
    "2. The New Modernities",
    ...
  ]
}}

テキスト:
{text_for_llm}
"""
    print_log("  [Phase 3] VLMチャンクからTOCを抽出中...")
    response_str = call_gemini(
        prompt,
        api_key=api_key,
        model=model,
        response_schema={
            "type": "OBJECT",
            "properties": {
                "toc": {"type": "ARRAY", "items": {"type": "STRING"}}
            },
            "required": ["toc"]
        }
    )
    if not response_str:
        print_log("  [Phase 3] TOC抽出に失敗しました（空のリストを返します）")
        return []
    
    try:
        # JSON フェンス除去 & パース
        clean = re.sub(r"```(?:json)?\s*|\s*```", "", response_str).strip()
        data = json.loads(clean)
        return data.get("toc", [])
    except Exception as e:
        print_log(f"  [Phase 3] TOCパース失敗: {e}")
        return []


def apply_toc_titles(
    chapters: List[dict],
    toc: List[dict],
    page_offset: int,
    threshold: int = 10,
) -> List[dict]:
    """
    pymupdf で検出した章の title を、LLM が抽出した TOC タイトルで補正する。

    Args:
        chapters:     extract_book_chapters の出力
        toc:          [{"title": "完全タイトル", "page": N}]  ← アラビア数字のみ
        page_offset:  PDF物理ページ番号 - 書籍アラビア数字ページ番号
        threshold:    マッチ許容ページ差（デフォルト10ページ）
    """
    if not toc:
        print_log("  [Phase 3] TOCなし → タイトル補正をスキップ")
        return chapters

    corrected = []
    for ch in chapters:
        pdf_page = ch["start_page"]
        book_page = pdf_page - page_offset

        # 1. ページ番号で最も近い TOC エントリを探す (+ front matter 特殊対応)
        best_entry = None
        best_diff = threshold + 1
        
        norm_raw = normalize_heading(ch["title"])

        # 前付け（page=-1）はタイトルが一致するかどうかで判定
        for entry in toc:
            if entry.get("page") == -1:
                norm_toc = normalize_heading(entry["title"])
                if norm_toc in norm_raw or norm_raw in norm_toc:
                    best_entry = entry
                    best_diff = 0
                    break
            else:
                diff = abs(entry["page"] - book_page)
                if diff < best_diff:
                    best_diff = diff
                    best_entry = entry

        # 2. タイトルの類似性もチェック（誤爆防止）
        # ただし、raw_title が極端に短い場合やゴミが含まれる場合はページ番号を優先する
        is_plausible = False
        if best_entry and best_diff <= threshold:
            norm_raw = normalize_heading(ch["title"])
            norm_toc = normalize_heading(best_entry["title"])
            # TOCタイトルが本文（raw）に含まれている、またはその逆ならOK
            if norm_toc in norm_raw or norm_raw in norm_toc or len(norm_raw) < 5:
                is_plausible = True

        corrected_ch = dict(ch)
        if best_entry and best_diff <= threshold and is_plausible:
            corrected_ch["title"] = best_entry["title"]
            corrected_ch["raw_title"] = ch["title"]
            print_log(
                f"  [Phase 3] 補正 p{pdf_page}(book p{book_page}): "
                f"'{ch['title'][:35]}' → '{best_entry['title'][:35]}'"
            )
        else:
            reason = "しきい値外" if best_diff > threshold else "タイトル不一致"
            print_log(
                f"  [Phase 3] 補正なし p{pdf_page}(book p{book_page}): "
                f"'{ch['title'][:50]}' (最近傍diff={best_diff}, 理由={reason})"
            )

        corrected.append(corrected_ch)

    return corrected


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


# ============================================================
# 6. Pipeline Phase Execution
# ============================================================

def run_phase3(
    phase1_state_path: str | Path,
    phase2_state_path: str | Path,
    structure_state_path: str | Path,
    sections_state_path: str | Path,
    save_state: bool = True,
    state: "Any" = None,
    is_book: bool = False,
    api_key: str | None = None,
    model: str | None = None,
    input_path: str | Path | None = None,
    pdf_mode: str = "hybrid",
) -> tuple[List[TreeNode], Dict[str, List[dict]]]:
    """Phase 3 メイン処理."""

    intro_pre_heading = None  # Paper Mode で DNA から取得、Book Mode では使わない
    chunks = load_chunks_from_json(str(phase1_state_path))
    
    # --- Route C: VLM Markdown 構造化 (pdf_mode == "full_vlm" かつ Markdown見出しが存在する場合) ---
    if pdf_mode == "full_vlm":
        has_markdown_headers = any(re.match(r'^#\s+', c.text.strip()) for c in chunks)
        if has_markdown_headers:
            print_log("  [Phase 3] Route C: VLM Markdown Mode (正規表現パース) を実行します")
            toc_list = []
            if is_book:
                toc_path = Path(structure_state_path).parent / "phase3_toc.json"
                if toc_path.exists():
                    with open(toc_path, "r", encoding="utf-8") as f:
                        cached_data = json.load(f)
                        toc_list = [entry["title"] for entry in cached_data.get("toc", [])]
                else:
                    toc_list = extract_toc_from_chunks(chunks, api_key=api_key, model=model)

            tree, sections_dict = structure_nodes_by_markdown(chunks, is_book=is_book, toc_list=toc_list)
            if save_state:
                save_tree_to_json(tree, str(structure_state_path))
                with open(sections_state_path, "w", encoding="utf-8") as f:
                    json.dump(sections_dict, f, ensure_ascii=False, indent=2)
            return tree, sections_dict
        else:
            print_log("  [Phase 3] pdf_mode='full_vlm' ですが Markdown 見出しが未検出です。標準構造化へフォールバックします。")

    anchors = {"metadata_ids": []}
    headings = []
    exclude_keywords = []

    if is_book and input_path:
        print_log("  [Phase 3] Book Mode (PyMuPDF + TOC補正)")

        toc_cache_path = Path(structure_state_path).parent / "phase3_toc.json"

        # TOCキャッシュの確認
        toc_data = None
        if toc_cache_path.exists():
            try:
                with open(toc_cache_path) as f:
                    cached = json.load(f)
                # 新しい形式 (tocリストがある) か確認
                if isinstance(cached.get("toc"), list) and len(cached["toc"]) > 0:
                    toc_data = cached
                    print_log(f"  [Phase 3] TOCキャッシュ使用: {len(toc_data['toc'])}件")
            except Exception as e:
                print_log(f"  [Phase 3] TOCキャッシュ読み込み失敗: {e}")

        # TOCキャッシュがない場合のみ LLM でTOC抽出
        if toc_data is None:
            if not api_key:
                print_log("  [Phase 3] APIキーがないためTOC抽出をスキップします")
                toc_data = {"toc": [], "body_start_page": 1}
            else:
                print_log("  [Phase 3] LLMで目次を抽出します...")
                # 同期呼び出し（awaitなし）
                extracted = extract_toc_via_llm(input_path, api_key=api_key, model=model, state=state)
                # キー名の不一致を吸収 (toc_titles or toc)
                toc = extracted.get("toc_titles") or extracted.get("toc", [])
                body_start_page = extracted.get("body_start_page", 1)
                toc_data = {"toc": toc, "body_start_page": body_start_page}
                
                # キャッシュ保存
                try:
                    with open(toc_cache_path, "w", encoding="utf-8") as f:
                        json.dump(toc_data, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    print_log(f"  [Phase 3] TOCキャッシュ保存失敗: {e}")

        # 取得（キャッシュ経由またはLLM直後）
        body_start_page = int(toc_data.get("body_start_page", 1))
        toc = toc_data.get("toc", [])
        page_offset = body_start_page - 1  # PDF物理ページ - 書籍ページ = offset

        # ChapterParser で章境界を抽出（TOC補正・ノイズフィルタ・ChapterBoundary変換を含む）
        from .engine.p3_structure.chapter_parser import ChapterParser
        boundaries = ChapterParser().parse(
            input_path=input_path,
            body_start_page=body_start_page,
            toc=toc,
            page_offset=page_offset,
        )

        anchors = {"chapters": boundaries}
        chunks = []
        headings = []
        exclude_keywords = []
        dna = {}
        intro_pre_heading = None

    else:
        # Paper Mode
        chunks = load_chunks_from_json(str(phase1_state_path))
        with open(phase2_state_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        resume_content = meta.get("resume_content", "")

        # アンカー検知によるスキップを廃止し、レジュメの見出しリストを唯一の基準にする
        anchors = {"metadata_ids": []}
        headings = extract_headings_from_resume(resume_content)

        # 【重要】Abstract を見出し候補の先頭に強制追加（論文モードの標準構成を保証）
        if "Abstract" not in headings and "abstract" not in [h.lower() for h in headings]:
            headings.insert(0, "Abstract")

        prompts = load_coreprompts()
        exclude_keywords = prompts.get("EXCLUDE_SECTION_KEYWORDS", [])

        # DNA の intro_pre_heading を取得（見出しなし Introduction の独立セクション化に使用）
        dna = meta.get("dna", {})
        intro_pre_heading = dna.get("intro_pre_heading") or None

    tree, sections_dict = build_tree(
        chunks, anchors, headings, exclude_keywords,
        is_book=is_book,
        intro_pre_heading=intro_pre_heading if not is_book else None,
        dna=dna if not is_book else None,
    )
    
    if save_state:
        save_tree_to_json(tree, str(structure_state_path))
        with open(sections_state_path, "w", encoding="utf-8") as f:
            json.dump(sections_dict, f, ensure_ascii=False, indent=2)
            
    return tree, sections_dict
