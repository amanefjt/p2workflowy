"""
フラットなノード列を見出し情報に基づいて階層 TreeNode ツリーへ再構築するエンジン。
"""

import re
from typing import Dict, List, Optional, Tuple

from core.config import print_log
from core.models import RawChunk, TreeNode
from .heading_matcher import normalize_heading, is_excluded_heading, match_heading


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


def structure_nodes_by_role(
    chunks: List[RawChunk],
    toc_list: List[str] | None = None,
) -> tuple[List[TreeNode], Dict[str, List[dict]]]:
    """
    書籍モード専用: Docling が付与した role（h1=章, h2=節, p=本文）を使って
    TreeNode の親子構造を構築する。structure_nodes_by_markdown の role 版。

    TOC（目次）リストが存在する場合、role="h1" の見出しが TOC に含まれるか検証し、
    含まれない場合は自動的に節（h3）へ降格（Demote）させる。
    """
    import difflib

    tree: List[TreeNode] = []
    sections_dict: Dict[str, List[dict]] = {}

    current_h2: Optional[TreeNode] = None
    current_h3: Optional[TreeNode] = None
    unlabeled_key = "unlabeled_0|[Unlabeled Section]"
    current_section_key: str = unlabeled_key

    norm_toc = [normalize_heading(t) for t in (toc_list or [])]

    def is_valid_chapter(title: str) -> bool:
        if not norm_toc:
            return True  # TOCがない場合はDoclingのroleを信じる
        norm_title = normalize_heading(title)
        if norm_title in norm_toc:
            return True
        for t in norm_toc:
            if norm_title in t or t in norm_title:
                return True
            ratio = difflib.SequenceMatcher(None, norm_title, t).ratio()
            if ratio > 0.85:
                return True
        return False

    for chunk in chunks:
        text = chunk.text.strip()
        if not text:
            continue

        if chunk.role == "h1":
            if not is_valid_chapter(text):
                node = TreeNode(id=chunk.id, text=text, role="h3", seq_index=chunk.seq_index, children=[])
                if current_h2 is not None:
                    current_h2.children.append(node)
                else:
                    tree.append(node)
                sections_dict.setdefault(current_section_key, []).append(
                    {"id": node.id, "text": node.text, "role": "h3"}
                )
                current_h3 = node
                continue

            node = TreeNode(id=chunk.id, text=text, role="h3", seq_index=chunk.seq_index, children=[])
            tree.append(node)
            current_section_key = f"{chunk.id}|{text}"
            sections_dict[current_section_key] = []
            current_h2 = node
            current_h3 = None

        elif chunk.role == "h2":
            node = TreeNode(id=chunk.id, text=text, role="h3", seq_index=chunk.seq_index, children=[])
            if current_h2 is not None:
                current_h2.children.append(node)
            else:
                tree.append(node)
            sections_dict.setdefault(current_section_key, []).append(
                {"id": node.id, "text": node.text, "role": "h3"}
            )
            current_h3 = node

        else:
            node = TreeNode(id=chunk.id, text=text, role="p", seq_index=chunk.seq_index)
            if current_h2 is None:
                current_h2 = TreeNode(
                    id="unlabeled_0", text="[Unlabeled Section]",
                    role="h3", seq_index=chunk.seq_index, children=[],
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
