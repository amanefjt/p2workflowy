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
# Utilities
# ============================================================

def normalize_heading(text: str) -> str:
    """比較のために見出しを正規化する（記号、数字、ローマ数字、余分な空白を除去）。"""
    # 削りすぎた場合のフォールバック用に、記号だけ除去した元テキストを退避
    original_clean = re.sub(r'[^\w\s]', '', text).strip()
    
    # 章番号・節番号（"1. ", "Chapter 1: ", "III. ", "1.1. " 等）を除去
    # ローマ数字 (I, II, III, IV, V, VI, VII, VIII, IX, X 等) にも対応
    t = re.sub(r'^(?:Chapter\s+)?(?:[IVXLCDM]+\b|[\d\.]+)\s*[:\.]?\s*', '', text, flags=re.I)
    # 記号を除去、小文字化、空白のトリミング
    t = re.sub(r'[^\w\s]', '', t)
    norm = " ".join(t.lower().split())
    
    # 正規化の結果、空文字や2文字未満になってしまった場合は、
    # 数字やローマ数字だけのタイトルである可能性が高いため、フォールバックを使用する
    if len(norm) < 2 and original_clean:
        return " ".join(original_clean.lower().split())
        
    return norm

def is_excluded_heading(text: str, keywords: List[str]) -> bool:
    """見出しが除外キーワード（References, Appendix 等）を含むか判定。"""
    t = text.lower()
    for kw in keywords:
        if kw.lower() in t:
            return True
    return False



def match_heading(text: str, headings: List[str]) -> Optional[tuple[str, str]]:
    """
    テキストの先頭部分が既知の見出しリストのいずれかと一致するか決定論的に判定する。
    """
    lines = text.split("\n")
    first_line = lines[0].strip() if lines else ""
    if not first_line:
        return None

    norm_first = normalize_heading(first_line)
    
    for head in headings:
        norm_head = normalize_heading(head)
        if not norm_head:
            continue
            
        # 決定論的な前方一致判定（語境界のチェックを追加）
        if norm_first.startswith(norm_head):
            # norm_head の直後の文字がスペース（または末尾）であることを確認
            if len(norm_first) > len(norm_head) and norm_first[len(norm_head)] != " ":
                continue
            # --- [強化] タイトル行等の誤爆防止フィルター ---
            # マッチした見出しが行全体に対して短すぎる場合（例: タイトルの冒頭数文字に過ぎない）、
            # それは見出しではなく本文（またはタイトル）の一部とみなす。
            # 閾値: 行の長さが見出しの長さの2.2倍を超える場合はマッチを却下する。
            # 例: "Arbitrary locations: in defence..." (50枚) vs "Arbitrary locations" (19文字)
            if len(norm_first) > len(norm_head) * 2.2:
                # ただし、見出し自体が十分長い（20文字以上）場合は、
                # 連結された本文である可能性が高いのでパースを許可する。
                if len(norm_head) < 20:
                    continue

            # 一致した場合、見出しとして分離
            # 連結されていた場合（本文が1行目に残っている場合）は、単語数ベースでカット
            words = first_line.split()
            head_words_count = len(norm_head.split())
            
            # 残りのテキストを構築
            matched_remaining = " ".join(words[head_words_count:]).strip()
            if lines[1:]:
                matched_remaining += "\n" + "\n".join(lines[1:])
            
            return head, matched_remaining.strip()
            
    return None



# ============================================================
# 4. Core Structuring Logic
# ============================================================

def structure_nodes_by_headings(
    nodes: List[TreeNode],
    headings: List[str],
    exclude_keywords: List[str],
    is_book: bool = False,
    intro_pre_heading: dict | None = None,
    dna: dict | None = None,
) -> tuple[List[TreeNode], Dict[str, List[dict]]]:
    """
    TreeNodeのリスト（平坦または既存構造）を、見出しリストに基づいて再構造化する。
    """
    if exclude_keywords is None:
        exclude_keywords = []
    structured_tree: List[TreeNode] = []
    sections_dict: Dict[str, List[dict]] = {}
    
    current_heading: str | None = None
    current_node: TreeNode | None = None
    current_section_chunks: List[dict] = []
    excluded = False

    # 全ノードを平坦化してスキャン（既存の見出しは無視して再構築）
    flat_paragraphs: List[TreeNode] = []
    def _collect_p(n: TreeNode):
        if n.role == "p":
            flat_paragraphs.append(n)
        if hasattr(n, "children") and n.children:
            for child in n.children:
                _collect_p(child)
    
    for n in nodes:
        _collect_p(n)

    # ----------------------------------------------------------------
    # Abstract 前処理（[Unlabeled Section] として収集）
    # Introduction が見つかる前のチャンクを先取りする。
    # ----------------------------------------------------------------
    intro_found_idx = None
    for i, node in enumerate(flat_paragraphs):
        # 先頭チャンクが見出しリストの最初の見出し（Introduction 等）と一致したら Break
        match_result_pre = match_heading(node.text, headings)
        if match_result_pre is not None:
            intro_found_idx = i
            break
 
    # DNAのabstract.start_idを使ってメタデータ境界インデックスを特定
    # そのインデックスより前のチャンクは全てタイトル・著者・所属等のメタデータ
    _meta_boundary_idx: int | None = None
    _meta_filter: set[str] = set()
    if dna:
        abs_start_id = str((dna.get("abstract") or {}).get("start_id", ""))
        if abs_start_id:
            for _i, _n in enumerate(flat_paragraphs):
                if str(_n.id) == abs_start_id:
                    _meta_boundary_idx = _i
                    break
        # フォールバック: abstract.start_idが使えない場合はタイトル・著者の文字列一致
        if _meta_boundary_idx is None:
            if dna.get("title"):
                _meta_filter.add(dna["title"].strip().lower())
            for _author in dna.get("authors", []):
                if _author and _author.strip():
                    _meta_filter.add(_author.strip().lower())

    abstract_chunks_pre = []
    if intro_found_idx and intro_found_idx > 0:
        # Introduction 検出前のチャンクを Abstract セクションとして事前収集
        for _idx, pre_node in enumerate(flat_paragraphs[:intro_found_idx]):
            text_clean = pre_node.text.strip()
            # "English text" などのラベル・ノイズをストリップ（大文字小文字無視、冒頭一致）
            text_clean = re.sub(r'^(English text|日本語本文|Table of Contents|Abstract)\s*', '', text_clean, flags=re.IGNORECASE)

            if not text_clean:
                continue

            # abstract開始より前のチャンクはメタデータとして除外（タイトル・著者・所属等）
            if _meta_boundary_idx is not None and _idx < _meta_boundary_idx:
                print_log(f"  [Structure] メタデータ行をスキップ: '{text_clean[:60]}'")
                continue
            # フォールバック: タイトル・著者の文字列一致によるフィルタ
            if _meta_filter and text_clean.lower() in _meta_filter:
                print_log(f"  [Structure] メタデータ行をスキップ: '{text_clean[:60]}'")
                continue

            pre_node.text = text_clean # ノイズを除去したテキストに更新
            abstract_chunks_pre.append(pre_node)
        
        if abstract_chunks_pre:
            abstract_node = TreeNode(
                id="unlabeled_pre",
                text="[Unlabeled Section]",
                role="h2" if not is_book else "h3", # 論文ならh3相当(base=2+1), 書籍ならh3相当
                seq_index=-2.0,
                children=[]
            )
            for pre_node in abstract_chunks_pre:
                abstract_node.children.append(pre_node)
            structured_tree.append(abstract_node)
            sections_dict["unlabeled_pre|[Unlabeled Section]"] = [{"text": pn.text, "id": pn.id} for pn in abstract_chunks_pre]
            print_log(f"  [Structure] Abstract 前処理: {len(abstract_chunks_pre)} チャンクを [Unlabeled Section] として収集")

    # ----------------------------------------------------------------
    # Introduction 以降をスキャンして見出しベースの構造化
    # ----------------------------------------------------------------
    scan_start = intro_found_idx if intro_found_idx is not None else 0

    intro_start_id = str(intro_pre_heading.get("start_id", "")) if intro_pre_heading else ""

    for node in flat_paragraphs[scan_start:]:
        # --- [フィルター] VLM 抽出の区切り行 "English text" をスキップ ---
        stripped_text = node.text.strip()
        if stripped_text.lower() == "english text":
            print_log(f"  [Structure] 区切り行をスキップ: '{node.text[:40]}'")
            continue

        # --- DNA intro_pre_heading による [Unlabeled Section] 分岐 ---
        # Abstract 直後の見出しなし Introduction 本文を独立セクションとして扱う
        if (intro_start_id and
                str(node.id) == intro_start_id and
                current_node is not None and
                current_node.text != "[Unlabeled Section]"):
            # 現在のセクション（Abstract 等）を確定して保存
            section_key = f"{current_node.id}|{current_heading}"
            structured_tree.append(current_node)
            sections_dict[section_key] = current_section_chunks
            # 見出しなし Introduction 用の新しい [Unlabeled Section] を開く
            current_heading = "[Unlabeled Section]"
            current_node = TreeNode(
                id="unlabeled_intro",
                text="[Unlabeled Section]",
                role="h2" if not is_book else "h3",
                seq_index=node.seq_index - 0.001,
                children=[],
            )
            current_section_chunks = []
            sections_dict["unlabeled_intro|[Unlabeled Section]"] = current_section_chunks
            print_log("  [Structure] intro_pre_heading 検出 → [Unlabeled Section] を開きます")

        # 見出しの抽出と分離判定
        match_result = match_heading(node.text, headings)

        if match_result is not None:
            # 新しい見出しを発見 → 前のセクションを保存
            if current_node is not None:
                # キーを "ID|タイトル" にして一意性を保証 (Phase 4 紐付け用)
                section_key = f"{current_node.id}|{current_heading}"
                structured_tree.append(current_node)
                sections_dict[section_key] = current_section_chunks

            matched_heading, remaining_text = match_result
            current_heading = matched_heading
            # セクション全体の起点としてのノード (h2 role for paper sections)
            current_node = TreeNode(
                id=node.id, text=current_heading, 
                role="h2" if not is_book else "h3",
                seq_index=node.seq_index, children=[]
            )
            current_section_chunks = []
            
            # 残りのテキストがある場合は子ノードとして追加
            if remaining_text:
                child_id = f"{node.id}_b"
                child = TreeNode(id=child_id, text=remaining_text, role="p", seq_index=node.seq_index + 0.001)
                current_node.children.append(child)
                current_section_chunks.append({"text": remaining_text, "id": child_id})
            
            current_section_key = f"{current_node.id}|{current_heading}"
            sections_dict[current_section_key] = current_section_chunks
            continue
        else:
            # --- [V2.9.4 追加] 柱（Running Header）の亡霊迎撃フィルター ---
            if current_heading:
                norm_node_text = normalize_heading(node.text)
                norm_curr_heading = normalize_heading(current_heading)
                # 現在の章タイトルと完全に一致（または極めて近い単独行）は柱とみなして捨てる
                if norm_node_text == norm_curr_heading and len(norm_node_text) > 0:
                    print_log(f"  [Structure] 柱（Running Header）を検出・除去: '{node.text}'")
                    continue
            # ----------------------------------------------------------------

            if current_node is None:
                # Introduction 以前の前処理で Abstract を取得済みなら、
                # ここには来ないはずだが念のため Abstract ノードに追加する
                print_log(f"  [Structure] 警告: Abstract前処理後に未帰属チャンクが発生: '{node.text[:60]}'")
                continue
            
            current_node.children.append(node)
            current_section_chunks.append({"text": node.text, "id": node.id})

    if current_node and not excluded:
        section_key = f"{current_node.id}|{current_heading}"
        structured_tree.append(current_node)
        sections_dict[section_key] = current_section_chunks

    return structured_tree, sections_dict


def build_tree(
    chunks: List[RawChunk],
    anchors: dict,
    headings: List[str],
    exclude_keywords: List[str],
    is_book: bool = False,
    intro_pre_heading: dict | None = None,
    dna: dict | None = None,
) -> tuple[List[TreeNode], Dict[str, List[dict]]]:
    """
    チャンクを構造化ツリーに変換する。
    """
    tree: List[TreeNode] = []
    sections_dict: Dict[str, List[dict]] = {}
    
    current_heading: str | None = None
    current_node: TreeNode | None = None
    current_section_chunks: List[dict] = []
    
    # 共通変数の初期化
    abstract_start_id = None
    intro_start_id = None
    metadata_ids = set()

    if is_book and "chapters" in anchors:
        # --- Book Mode: ChapterBoundary リストを TreeNode ツリーに変換 ---
        current_part_node: Optional[TreeNode] = None
        boundaries = anchors.get("chapters", [])

        for boundary in boundaries:
            chapter_node = TreeNode(
                id=str(boundary.start_page * 10000),
                text=boundary.title,
                role=boundary.role,
                seq_index=float(boundary.start_page),
                children=[],
            )

            section_chunks = []
            for i, para in enumerate(boundary.paragraphs):
                if not para.strip():
                    continue
                node_id = str(boundary.start_page * 10000 + i + 1)
                chapter_node.children.append(
                    TreeNode(
                        id=node_id,
                        text=para,
                        role="p",
                        seq_index=float(boundary.start_page) + i * 0.0001,
                    )
                )
                section_chunks.append({"text": para, "id": node_id})

            section_key = f"{chapter_node.id}|{boundary.title}"
            sections_dict[section_key] = section_chunks

            if boundary.role == "h2":
                current_part_node = chapter_node
                tree.append(chapter_node)
            else:
                if current_part_node is not None:
                    current_part_node.children.append(chapter_node)
                else:
                    tree.append(chapter_node)

    else:
        # --- Paper Mode フロー ---
        # 簡易的な TreeNode リストを作成して structure_nodes_by_headings に渡す
        base_nodes = []
        for c in chunks:
            base_nodes.append(TreeNode(id=c.id, text=c.text, role="p", seq_index=c.seq_index))
        
        tree, sections_dict = structure_nodes_by_headings(
            base_nodes, headings, exclude_keywords,
            is_book=is_book,
            intro_pre_heading=intro_pre_heading,
            dna=dna,
        )

    return tree, sections_dict


# ============================================================
# 5. Route C: VLM Markdown Structuring
# ============================================================

def structure_nodes_by_markdown(
    chunks: List[RawChunk],
    is_book: bool = False,
    toc_list: List[str] | None = None,
) -> tuple[List[TreeNode], Dict[str, List[dict]]]:
    """
    Route C: VLM が出力した Markdown 記号（# / ##）を正規表現でパースし、
    TreeNode の親子構造を構築する。
    
    TOC（目次）リストが存在する場合、`# ` (h2候補) が TOC に含まれるか検証し、
    含まれない場合は自動的に h3 (節) へと降格（Demote）させる。
    """
    import difflib
    
    tree: List[TreeNode] = []
    sections_dict: Dict[str, List[dict]] = {}

    current_h2: Optional[TreeNode] = None
    current_h3: Optional[TreeNode] = None
    unlabeled_key = "unlabeled_0|[Unlabeled Section]"
    current_section_key: str = unlabeled_key

    # TOCリストの正規化（照合用）
    norm_toc = [normalize_heading(t) for t in (toc_list or [])]

    def is_valid_chapter(title: str) -> bool:
        if not is_book or not norm_toc:
            return True # TOCがない場合はVLMを信じる
        
        norm_title = normalize_heading(title)
        # 1. 完全一致
        if norm_title in norm_toc:
            return True
        # 2. 類似度判定 (SequenceMatcher)
        for t in norm_toc:
            # 部分一致または高い類似度（80%以上）
            if norm_title in t or t in norm_title:
                return True
            ratio = difflib.SequenceMatcher(None, norm_title, t).ratio()
            if ratio > 0.85:
                return True
        return False

    for chunk in chunks:
        raw_text = chunk.text.strip()

        # --- トップレベル見出し（章: h2）---
        if re.match(r'^#\s+', raw_text) and not re.match(r'^##', raw_text):
            title = re.sub(r'^#+\s+', '', raw_text).strip()
            
            if not is_valid_chapter(title):
                # TOCにないため、h3 (Section) へ降格
                node = TreeNode(
                    id=chunk.id, text=title, role="h3", seq_index=chunk.seq_index, children=[]
                )
                if current_h2 is not None:
                    current_h2.children.append(node)
                else:
                    tree.append(node)
                
                sections_dict.setdefault(current_section_key, []).append(
                    {"id": node.id, "text": node.text, "role": "h3"}
                )
                current_h3 = node
                continue 

            # 正規のh2処理
            node = TreeNode(
                id=chunk.id, text=title, role="h2" if not is_book else "h3", seq_index=chunk.seq_index, children=[]
            )
            tree.append(node)
            current_section_key = f"{chunk.id}|{title}"
            sections_dict[current_section_key] = []
            current_h2 = node
            current_h3 = None

        # --- サブ見出し（節: h3）---
        elif re.match(r'^##\s+', raw_text):
            title = re.sub(r'^#+\s+', '', raw_text).strip()
            node = TreeNode(
                id=chunk.id, text=title, role="h3", seq_index=chunk.seq_index, children=[]
            )
            # フェイルセーフ: current_h2 がない（孤立した ##）場合はトップレベルへ
            if current_h2 is not None:
                current_h2.children.append(node)
            else:
                tree.append(node)
            
            sections_dict.setdefault(current_section_key, []).append(
                {"id": node.id, "text": node.text, "role": "h3"}
            )
            current_h3 = node

        # --- 本文（p）---
        else:
            node = TreeNode(
                id=chunk.id, text=raw_text, role="p", seq_index=chunk.seq_index
            )
            
            # 親見出しがない場合（見出しのない論文や、VLMが冒頭から本文を返した場合）の救済
            if current_h2 is None:
                current_h2 = TreeNode(
                    id="unlabeled_0", text="[Unlabeled Section]", 
                    role="h2" if not is_book else "h3", # 論文ならh2, 書籍ならh3相当
                    seq_index=chunk.seq_index, children=[]
                )
                tree.append(current_h2)
                current_section_key = unlabeled_key
                sections_dict[current_section_key] = []
            
            parent = current_h3 or current_h2
            parent.children.append(node)
            sections_dict.setdefault(current_section_key, []).append(
                {"id": node.id, "text": node.text, "role": "p"}
            )

    return tree, sections_dict


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


def extract_headings_from_resume(resume: str) -> List[str]:
    """レジュメからセクション見出し候補を抽出する。"""
    headings = []
    lines = resume.split("\n")
    for line in lines:
        line_strip = line.strip()
        # 1. 見出し行 (#, ##, ### 等) または リスト項目 (- ## ...) を抽出
        match = re.match(r'^(?:[-\*]\s*)?(#+)\s*(.*)$', line_strip)
        if match:
            title = match.group(2).strip()
            
            # 【修正】メタ見出し（構成パーツ名）の除外ロジックを部分一致から「厳密な一致」に変更
            # LLMが付与するメタ見出しのキーワードセット
            meta_keywords = {
                "リサーチ・クエスチョン", "全体のリサーチ・クエスチョン", 
                "核心的主張", "核心的主張（Thesis）", "全体の核心的主張", "中心的な主張", 
                "構成と理論的貢献", "論理展開", "詳細な論理展開", 
                "詳細な各章の論理展開", "章内の論理展開", "各節の主張とその根拠",
                "要約", "書籍全体のレジュメ", "各セクションの展開", "各章の構成と理論的貢献"
            }
            
            # 先頭の数字（"1. ", "2. "）等を取り除いたプレーンな状態で完全一致判定を行う
            clean_for_meta_check = re.sub(r'^[\d\.]+\s*', '', title).strip()
            if clean_for_meta_check in meta_keywords:
                continue
            
            # 日本語タイトル（英語タイトル）という形式の場合、英語側を優先的に抽出
            # 例: "III. 比較の比較（Comparisons of Comparisons—1）"
            # 英語見出しにハイフン、エムダッシュ、数字が含まれるケースも考慮
            en_match = re.search(r'[（\(]([a-zA-Z\s\-\u2014\d]+)[\)）]', title)
            if en_match:
                extracted_en = en_match.group(1).strip()
                if extracted_en:
                    headings.append(extracted_en)
            
            # 元のタイトルも正規化して追加（念のため日本語マッチ用）
            title_clean = re.sub(r'^[#\s]+', '', title)
            title_clean = title_clean.strip("[] ").strip()
            
            # 節番号・ローマ数字を除去（単語の境界 \b を考慮して単語の一部が削られるのを防ぐ）
            title_clean = re.sub(r'^(?:[IVXLCDM]+\b|[\d\.]+)\s*[:\.]?\s*', '', title_clean, flags=re.I)
            
            if title_clean and title_clean not in headings:
                headings.append(title_clean)
                
    return headings