"""
書籍 PDF の目次（TOC）抽出とタイトル適用。
LLM による目次ページ解析と、チャンク列からの決定論的 TOC 復元の両方を提供する。
"""

import json
import re
from pathlib import Path
from typing import Any, List, Optional

import fitz

from core.config import print_log
from core.models import RawChunk
from core.text_utils import _SENTENCE_END_RE, _TRAILING_WORDS
from .heading_matcher import normalize_heading


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


def extract_toc_via_llm(pdf_path: str | Path, api_key: str | None = None, model: str | None = None, state: Any = None) -> dict:
    """
    PDF冒頭からTOCをLLMで抽出する。
    
    Returns:
        {
            "toc": [{"title": "Introductions: The Compulsion of Relations", "page": 1}, ...],
            "body_start_page": 15   # アラビア数字ページ1がはじまるPDF物理ページ番号
        }
    """
    from core.llm_client import call_gemini

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
    from core.llm_client import call_gemini
    
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

