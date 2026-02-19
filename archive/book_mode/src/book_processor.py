# -*- coding: utf-8 -*-
"""
book_processor.py: 書籍モード "Map-Split-Reuse" パターンの実装

Phase 1: Full-Text Mapping  - AI が書籍全文から ToC + Anchor を JSON で返す
Phase 2: Anchor-Based Splitting - Levenshtein距離によるファジーマッチで物理分割
"""
import asyncio
import json
import re
from typing import List, Dict, Optional, Callable

from .llm_processor import LLMProcessor
from .constants import BOOK_TOC_MAPPING_PROMPT, EXCLUDE_SECTION_KEYWORDS


# --- ファジーマッチ: Levenshtein距離 ---

def levenshtein_distance(s1: str, s2: str) -> int:
    """2つの文字列間のLevenshtein距離（編集距離）を計算する"""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            # 挿入、削除、置換のコスト
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row

    return prev_row[-1]


def fuzzy_find(text: str, anchor: str, threshold_ratio: float = 0.3) -> int:
    """
    テキスト内でアンカー文字列にファジーマッチする位置を検索する。
    """
    if not anchor or not text:
        return -1

    anchor_lower = anchor.lower().strip()
    text_lower = text.lower()
    
    # 1. 完全一致を試みる（高速）
    exact_pos = text_lower.find(anchor_lower)
    if exact_pos != -1:
        return exact_pos

    # 2. 完全一致が見つからない場合、Levenshtein距離によるマッチング
    # スライディングウィンドウを全文に対して行うのは重すぎるため、
    # 候補となりそうな位置（最初に出現する数単語が一致する場所など）を絞り込むか、
    # あるいは単純に最初の数文字のfind結果周辺を探索する。
    
    # ここでは、アンカーの最初の10文字で検索し、見つかった周囲を探索する簡略版を採用
    seed = anchor_lower[:10]
    idx = 0
    best_pos = -1
    best_distance = int(len(anchor_lower) * threshold_ratio) + 1
    
    while True:
        pos = text_lower.find(seed, idx)
        if pos == -1:
            break
        
        # 見つかった位置周辺（anchor長程度）をウィンドウとして評価
        window_size = len(anchor_lower)
        candidate = text_lower[pos : pos + window_size]
        dist = levenshtein_distance(anchor_lower, candidate)
        
        if dist < best_distance:
            best_distance = dist
            best_pos = pos
            if dist == 0: break # 奇跡の完全一致
            
        idx = pos + 1
        if idx > len(text_lower) - len(seed):
            break
            
    return best_pos


# --- Phase 1: Full-Text Mapping ---

async def map_book_toc(
    llm: LLMProcessor,
    full_text: str,
    progress_callback: Optional[Callable] = None
) -> List[Dict[str, str]]:
    """
    Phase 1: 書籍全文をAIに渡し、ToC（目次）構造をJSON配列として取得する。
    """
    prompt = BOOK_TOC_MAPPING_PROMPT.format(full_text=full_text)
    raw_response = await asyncio.to_thread(llm.call_api, prompt, progress_callback)

    # JSON配列をパースする（コードフェンスが含まれている場合は除去）
    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
        if match:
            cleaned = match.group(1).strip()
        else:
            cleaned = cleaned.replace("```json", "").replace("```", "").strip()

    try:
        toc_list = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"AIの応答をJSONとしてパースできませんでした: {e}\n応答内容: {cleaned[:500]}")

    if not isinstance(toc_list, list):
        raise ValueError(f"AIの応答がJSON配列ではありません: {type(toc_list)}")

    validated = []
    exclude_keywords = {k.lower() for k in EXCLUDE_SECTION_KEYWORDS}

    for item in toc_list:
        if isinstance(item, dict) and "chapter_title" in item and "anchor_text" in item:
            title = str(item["chapter_title"]).strip()
            anchor = str(item["anchor_text"]).strip()
            
            is_excluded = False
            title_lower = title.lower()
            for k in exclude_keywords:
                if k in title_lower:
                    is_excluded = True
                    break
            
            if is_excluded:
                if progress_callback:
                    progress_callback(f"  (Skip) 除外対象の章をスキップ: {title}")
                continue

            validated.append({
                "chapter_title": title,
                "anchor_text": anchor
            })

    if not validated:
        raise ValueError("AIの応答から有効な章情報を取得できませんでした")

    return validated


# --- Phase 2: Anchor-Based Splitting ---

def split_by_anchors(
    full_text: str,
    toc_mappings: List[Dict[str, str]],
    progress_callback: Optional[Callable] = None
) -> List[Dict[str, str]]:
    """
    Phase 2: アンカーテキストに基づいて章を分割する。
    スキップを廃止し、フォールバック（タイトル検索）を用いる。
    """
    positions = []
    for mapping in toc_mappings:
        title = mapping["chapter_title"]
        anchor = mapping["anchor_text"]
        
        # 1. アンカーテキスト（本文冒頭）で探す
        pos = fuzzy_find(full_text, anchor)
        
        # 2. 見つからない場合、章タイトルで探す（フォールバック）
        if pos == -1:
            if progress_callback:
                progress_callback(f"  ⚠ アンカー未検出、タイトルで検索: '{title}'")
            pos = fuzzy_find(full_text, title)
            
        if pos != -1:
            positions.append({
                "title": title,
                "start": pos,
            })
            if progress_callback:
                progress_callback(f"  ✓ 検出: '{title}' at {pos}")
        else:
            if progress_callback:
                progress_callback(f"  ✖ 見つかりません (スキップ不可、位置不明として後続で処理): '{title}'")
            # 位置が特定できない場合でも、順番を守るためにダミーの -1 を保持
            positions.append({
                "title": title,
                "start": -1
            })

    # 開始位置が不明な章（start == -1）を補完する
    # 基本的には直前の章の直後、または次の章の直前とするが、
    # 完全に不明な場合は前の章の末尾（長さ0）になる
    processed_positions = []
    current_pos = 0
    
    # まず見つかった章の順序を尊重しつつ、-1 を埋める
    for i, p in enumerate(positions):
        if p["start"] != -1:
            current_pos = p["start"]
        processed_positions.append(p)

    # ソート（見つかったもののみ）
    found_positions = sorted([p for p in positions if p["start"] != -1], key=lambda x: x["start"])
    
    # 分割
    chapters = []
    # positions の順序（目次順）で分割を試みる
    # もし A(100), B(-1), C(200) なら、Bは100から200の間のどこか。
    # ここではシンプルに「見つかった次の章の開始位置まで」をその章とする。
    
    for i, p in enumerate(positions):
        title = p["title"]
        start = p["start"]
        
        # 次の「見つかっている章」を探す
        next_found_pos = -1
        for j in range(i + 1, len(positions)):
            if positions[j]["start"] != -1:
                next_found_pos = positions[j]["start"]
                break
        
        # この章の開始位置を確定
        actual_start = start
        if actual_start == -1:
            # ひとつ前の章の「本来の」終了位置（または開始位置）
            actual_start = chapters[-1]["end"] if chapters else 0
            
        # 終了位置の確定
        if next_found_pos != -1:
            actual_end = next_found_pos
        else:
            actual_end = len(full_text)
            
        chapter_text = full_text[actual_start:actual_end].strip()
        
        # 前の章と被らないように調整（next_found_pos が actual_start より前になることはないはず）
        chapters.append({
            "title": title,
            "text": chapter_text,
            "start": actual_start,
            "end": actual_end
        })

    return chapters
