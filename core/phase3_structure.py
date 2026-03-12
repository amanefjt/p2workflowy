"""
p2workflowy V2 Phase 3: Structuring & Clipping
Pre-scanner + Section Detector + 除外クリッピング → 英語ツリー構築。
indi_pre_scanner.md / indi_section_detector.md に完全準拠。
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

from thefuzz import fuzz

from .config import (print_log, 
    load_coreprompts,
)
from .models import RawChunk, TreeNode, load_chunks_from_json, save_tree_to_json


# ============================================================
# 1. Pre-scanner (indi_pre_scanner.md)
# ============================================================

def pre_scan(chunks: List[RawChunk], scan_limit: int = 30) -> dict:
    """
    冒頭チャンクをスキャンし、構造マーカー（アンカー）を検出する。

    Returns:
        dict: {
            "abstract_start_id": id | None,
            "introduction_start_id": id | None,
            "keywords_id": id | None,
            "metadata_ids": List[id],
        }
    """
    search_range = chunks[:scan_limit]

    result = {
        "abstract_start_id": None,
        "introduction_start_id": None,
        "keywords_id": None,
        "metadata_ids": [],
    }

    for chunk in search_range:
        text = chunk.text
        first_line = text.split("\n")[0].strip()

        # A. Keywords セクションの検知
        if re.match(r"^(Keywords?|Key\s*words):", text, re.IGNORECASE):
            result["keywords_id"] = chunk.id
            # 後方補完: keywords 検知時に abstract_start_id が未定義なら先頭チャンクを設定
            if result["abstract_start_id"] is None and search_range:
                result["abstract_start_id"] = search_range[0].id

        # B. Introduction の検知
        if re.match(r"^([1I]\.?\s+)?Introduction", first_line, re.IGNORECASE):
            result["introduction_start_id"] = chunk.id

        # C. Email アドレスの検知
        if re.search(r"[\w.-]+@[\w.-]+\.\w+", text):
            result["metadata_ids"].append(chunk.id)

        # D. 明示的な Abstract の検知
        if re.match(r"^Abstract$", first_line, re.IGNORECASE):
            result["abstract_start_id"] = chunk.id

    print_log(f"  [Phase 3] Pre-scanner 結果: {result}")
    return result


# ============================================================
# 2. レジュメからの見出し抽出 (Heading Extraction)
# ============================================================

def extract_headings_from_resume(resume_content: str) -> List[str]:
    """
    レジュメの内容からセクション見出し（英語）の一覧を抽出する。
    Gemini 3.1 Flash Lite 等のモデルがネストしたヘッダー（####）や
    角括弧の省略を行う場合があるため、柔軟に対応する。
    """
    headings = []
    lines = resume_content.split("\n")

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # ヘッダー記号 (#) と内容を分離。任意の数の # を許容する。
        match = re.match(r"^(#+)\s*(.*)$", stripped)
        if not match:
            continue
        
        content = match.group(2).strip()
        if not content:
            continue

        # オプションの角括弧 [ ] を除去
        bracket_match = re.match(r"^\[(.*)\]$", content)
        if bracket_match:
            content = bracket_match.group(1).strip()

        # 日本語が含まれている場合はスキップ（レジュメのタイトルや日本語説明文を除外）
        if re.search(r"[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf]", content):
            continue

        # 特定の構成用メタ見出しをスキップ
        if content.lower() in ["リサーチ・クエスチョン", "核心的主張", "各セクションの展開"]:
            continue

        # 数値プレフィックス（"1. ", "1) "等）があれば除去して正規化
        content_clean = re.sub(r"^\d+[\.\s\)]+", "", content).strip()
        
        if content_clean and content_clean not in headings:
            # 短すぎるものは見出しとして不適切（誤検知）の可能性が高いためスキップ
            if len(content_clean) > 3:
                headings.append(content_clean)

    print_log(f"  [Phase 3] レジュメから見出し {len(headings)} 件抽出: {headings}")
    return headings


# ============================================================
# 3. Fuzzy Matching (Section Detector)
# ============================================================

FUZZY_THRESHOLD = 80  # マッチング閾値


def match_heading(chunk_text: str, headings: List[str]) -> Optional[tuple[str, str]]:
    """
    チャンクテキストの先頭に見出しが含まれているか判定する。
    
    1. 正規表現による厳密（かつ空白に寛容な）マッチングを優先。
    2. ヒットしない場合、1行目のみを対象としたファジィマッチングを実行。

    Returns:
        Optional[tuple[str, str]]: (マッチした見出し名, 見出しを除去した残りの本文)
    """
    # 1. 正規表現による先頭マッチング (Case-Insensitive, Whitespace-Flexible)
    for heading in headings:
        # 見出し内のスペースを \s+ に置換して正規表現パターンを作成
        escaped_heading = re.escape(heading)
        pattern = re.sub(r' ', r'\\s+', escaped_heading)
        # 先頭一致をチェック
        match = re.match(rf"^({pattern})", chunk_text, re.IGNORECASE | re.DOTALL)
        
        if match:
            matched_str = match.group(1)
            remaining_text = chunk_text[len(matched_str):].strip()
            return heading, remaining_text

    # 2. 従来のファジィマッチング (Fallback)
    first_line = chunk_text.split("\n")[0].strip()
    if len(first_line) > 200:
        return None

    best_score = 0
    best_heading = None

    for heading in headings:
        score = fuzz.token_set_ratio(first_line, heading)
        if score > best_score:
            best_score = score
            best_heading = heading

    if best_score >= FUZZY_THRESHOLD:
        # ファジィマッチの場合は「1行目」を見出し本体とみなし、2行目以降を本文とする
        lines = chunk_text.split("\n")
        remaining_text = "\n".join(lines[1:]).strip()
        return best_heading, remaining_text

    return None


# ============================================================
# 4. 除外セクション判定 (Exclusion Check)
# ============================================================

EXCLUDE_THRESHOLD = 90  # 除外用ファジィ閾値


def is_excluded_heading(heading: str, exclude_keywords: List[str]) -> bool:
    """
    見出しが除外キーワードに該当するか判定する。
    - 部分一致（.lower()）
    - token_set_ratio >= 90
    """
    heading_lower = heading.lower()

    for kw in exclude_keywords:
        # 部分一致
        if kw.lower() in heading_lower:
            return True
        # 高精度ファジィ
        if fuzz.token_set_ratio(heading_lower, kw.lower()) >= EXCLUDE_THRESHOLD:
            return True

    return False


# ============================================================
# 5. ツリー構築 (Tree Building)
# ============================================================

def build_tree(
    chunks: List[RawChunk],
    anchors: dict,
    headings: List[str],
    exclude_keywords: List[str],
) -> tuple[List[TreeNode], Dict[str, List[dict]]]:
    """
    チャンクを構造化ツリーに変換する。

    Returns:
        tuple: (tree: List[TreeNode], sections_dict: Dict[str, List[dict]])
    """
    tree: List[TreeNode] = []
    sections_dict: Dict[str, List[dict]] = {}

    abstract_start_id = anchors.get("abstract_start_id")
    intro_start_id = anchors.get("introduction_start_id")
    metadata_ids = set(anchors.get("metadata_ids", []))

    # --- Abstract セクションの構築 ---
    if abstract_start_id is not None and intro_start_id is not None:
        abstract_node = TreeNode(
            id="abstract",
            text="Abstract",
            role="h2",
            seq_index=-2.0,
            children=[],
        )
        abstract_chunks = []

        for chunk in chunks:
            # abstract_start_id ～ introduction_start_id の直前
            cid = int(chunk.id)
            if abstract_start_id is not None and intro_start_id is not None:
                if cid >= int(abstract_start_id) and cid < int(intro_start_id):
                    if chunk.id in metadata_ids:
                        continue  # メタデータは除外
                    if anchors.get("keywords_id") is not None and cid == int(anchors.get("keywords_id")):
                        continue  # Keywords 行も除外
                    # Abstract のタイトル行自体を子に含めない
                    first_line = chunk.text.split("\n")[0].strip().lower()
                    if first_line == "abstract":
                        continue

                    child = TreeNode(
                        id=chunk.id,
                        text=chunk.text,
                        role="p",
                        seq_index=chunk.seq_index,
                    )
                    abstract_node.children.append(child)
                    abstract_chunks.append(chunk.to_dict())

        if abstract_node.children:
            tree.append(abstract_node)
            sections_dict["Abstract"] = abstract_chunks

    # --- Introduction 以降のセクション構築 ---
    # Introduction の開始位置を特定
    start_idx = 0
    if intro_start_id is not None:
        for i, chunk in enumerate(chunks):
            if int(chunk.id) == int(intro_start_id):
                start_idx = i
                break
    elif abstract_start_id is not None:
        # Introduction が見つからない場合、Abstract 以降のチャンクから
        for i, chunk in enumerate(chunks):
            if int(chunk.id) >= int(abstract_start_id):
                start_idx = i
                break

    current_heading: str | None = None
    current_node: TreeNode | None = None
    current_section_chunks: List[dict] = []
    excluded = False

    for chunk in chunks[start_idx:]:
        if chunk.id in metadata_ids:
            continue

        first_line = chunk.text.split("\n")[0].strip()

        # 短い先頭行（見出し候補）に対して除外キーワードチェック
        # レジュメの見出しリストにない除外対象セクション（NOTES 等）を検出
        if len(first_line) <= 200 and first_line:
            if is_excluded_heading(first_line, exclude_keywords):
                # 前のセクションを保存してからクリッピング
                if current_node is not None and not excluded:
                    unique_heading = current_heading
                    counter = 1
                    while unique_heading in sections_dict:
                        unique_heading = f"{current_heading} ({counter})"
                        counter += 1
                    current_node.text = unique_heading
                    tree.append(current_node)
                    sections_dict[unique_heading] = current_section_chunks
                print_log(f"  [Phase 3] 除外セクション検出（先頭行一致）: '{first_line}' → 以降をクリッピング")
                excluded = True
                break

        # 見出しの抽出と分離判定
        match_result = match_heading(chunk.text, headings)

        if match_result is not None:
            matched_heading, remaining_text = match_result
            
            if matched_heading != current_heading:
                # 前のセクションを保存
                if current_node is not None and not excluded:
                    unique_heading = current_heading
                    counter = 1
                    while unique_heading in sections_dict:
                        unique_heading = f"{current_heading} ({counter})"
                        counter += 1
                    current_node.text = unique_heading
                    tree.append(current_node)
                    sections_dict[unique_heading] = current_section_chunks

                # 除外チェック
                excluded = is_excluded_heading(matched_heading, exclude_keywords)
                if excluded:
                    print_log(f"  [Phase 3] 除外セクション検出: '{matched_heading}' → 以降をクリッピング")
                    break  # 除外セクション以降は完全に破棄

                # 新しいセクション開始
                current_heading = matched_heading
                current_node = TreeNode(
                    id=f"section_{chunk.id}",
                    text=matched_heading,
                    role="h2",
                    seq_index=chunk.seq_index,
                    children=[],
                )
                current_section_chunks = []

                # 見出しを除去した残りのテキストがあれば子ノードに追加
                if remaining_text:
                    child = TreeNode(
                        id=chunk.id,
                        text=remaining_text,
                        role="p",
                        seq_index=chunk.seq_index,
                    )
                    current_node.children.append(child)
                    current_section_chunks.append(chunk.to_dict())
            else:
                # 既に現在のセクション内であり、再度同じ見出しにマッチした場合（まれなケース）
                # 本文として処理
                if current_node is not None:
                    child = TreeNode(
                        id=chunk.id,
                        text=remaining_text if remaining_text else chunk.text,
                        role="p",
                        seq_index=chunk.seq_index,
                    )
                    current_node.children.append(child)
                    current_section_chunks.append(chunk.to_dict())

        else:
            # 見出しにマッチしない → 現在のセクションの本文
            if current_node is None:
                # まだセクションが開始されていない場合、[Unlabeled Section] を作成
                current_heading = "[Unlabeled Section]"
                current_node = TreeNode(
                    id="unlabeled_0",
                    text="[Unlabeled Section]",
                    role="h2",
                    seq_index=chunk.seq_index,
                    children=[],
                )
                current_section_chunks = []

            child = TreeNode(
                id=chunk.id,
                text=chunk.text,
                role="p",
                seq_index=chunk.seq_index,
            )
            current_node.children.append(child)
            current_section_chunks.append(chunk.to_dict())

    # 最後のセクションを保存
    if current_node is not None and not excluded:
        unique_heading = current_heading
        counter = 1
        while unique_heading in sections_dict:
            unique_heading = f"{current_heading} ({counter})"
            counter += 1
        current_node.text = unique_heading
        tree.append(current_node)
        sections_dict[unique_heading] = current_section_chunks

    return tree, sections_dict


# ============================================================
# メイン実行関数
# ============================================================

def run_phase3(
    phase1_state_path: str | Path,
    phase2_state_path: str | Path,
    structure_state_path: str | Path,
    sections_state_path: str | Path,
    save_state: bool = True,
    state: "Any" = None,
) -> tuple[List[TreeNode], Dict[str, List[dict]]]:
    """
    Phase 3 メイン処理: Pre-scan → 見出し抽出 → Fuzzy Matching → Tree 構築。

    Returns:
        tuple: (tree, sections_dict)
    """
    # Phase 1 の出力を読み込み
    phase1_state_path = Path(phase1_state_path)
    if not phase1_state_path.exists():
        raise FileNotFoundError(f"Phase 1 の出力が見つかりません: {phase1_state_path}")
    chunks = load_chunks_from_json(str(phase1_state_path))

    # Phase 2 の出力を読み込み
    phase2_state_path = Path(phase2_state_path)
    if not phase2_state_path.exists():
        raise FileNotFoundError(f"Phase 2 の出力が見つかりません: {phase2_state_path}")
    with open(phase2_state_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    resume_content = meta["resume_content"]

    # coreprompts.json から除外キーワードを取得
    prompts = load_coreprompts()
    exclude_keywords = prompts.get("EXCLUDE_SECTION_KEYWORDS", [])

    # 1. Pre-scanner
    print_log("  [Phase 3] Pre-scanning...")
    anchors = pre_scan(chunks)

    # 2. レジュメから見出し抽出
    headings = extract_headings_from_resume(resume_content)

    # 3+4+5. ツリー構築
    print_log("  [Phase 3] ツリー構築中...")
    tree, sections_dict = build_tree(chunks, anchors, headings, exclude_keywords)

    print_log(f"  [Phase 3] ツリー構築完了: {len(tree)} セクション")
    for node in tree:
        child_count = len(node.children)
        print_log(f"    [{node.role}] {node.text} ({child_count} チャンク)")

    # State 保存
    if save_state:
        save_tree_to_json(tree, str(structure_state_path))
        print_log(f"  [Phase 3] 構造ツリー保存: {structure_state_path}")

        with open(sections_state_path, "w", encoding="utf-8") as f:
            json.dump(sections_dict, f, ensure_ascii=False, indent=2)
        print_log(f"  [Phase 3] セクション辞書保存: {sections_state_path}")

    return tree, sections_dict
