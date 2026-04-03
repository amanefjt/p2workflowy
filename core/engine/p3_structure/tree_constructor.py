import re
from typing import List, Dict, Tuple, Optional
from core.models import TreeNode, RawChunk
from .heading_detector import HeadingDetector

class TreeConstructor:
    """
    見出し判定結果に基づき、フラットなノードを階層的な TreeNode ツリーに構築するエンジン。
    """
    def __init__(self, detector: HeadingDetector, is_book: bool = False, exclude_keywords: List[str] = None):
        self.detector = detector
        self.is_book = is_book
        self.exclude_keywords = [k.lower() for k in (exclude_keywords or [])]

    def construct(self, nodes: List[TreeNode]) -> Tuple[List[TreeNode], Dict[str, List[dict]]]:
        """
        TreeNode のリストを受け取り、見出しに基づいて再構造化されたツリーとセクション辞書を返す。
        """
        structured_tree: List[TreeNode] = []
        sections_dict: Dict[str, List[dict]] = {}
        
        flat_paragraphs = self._flatten_nodes(nodes)
        
        current_node: Optional[TreeNode] = None
        current_heading: Optional[str] = None
        current_section_chunks: List[dict] = []
        is_skipping = False
        
        for node in flat_paragraphs:
            # メタデータおよび VLM 固有の区切り行ノイズを除去
            if node.role == "metadata" or node.text.strip().lower() == "english text":
                continue

            # 見出し判定 (優先度: 1. VLM Role, 2. Physical Detector)
            is_vlm_h = node.role in ["h1", "h2", "h3", "h4"]
            title, remaining = node.text, None
            
            if is_vlm_h:
                is_h = True
                title = node.text.strip().replace("#", "").strip()
                # 表記の統一
                if title.lower() in ["[untitled section]", "[unlabeled section]"]:
                    title = "[Unlabeled Section]"
            else:
                is_h, title, remaining = self.detector.detect(node)
                if is_h and title.lower() in ["[untitled section]", "[unlabeled section]"]:
                    title = "[Unlabeled Section]"
            
            if is_h:
                # 前のセクションを保存（スキップ中でない場合のみ）
                if current_node and not is_skipping:
                    self._save_section(current_node, current_heading, current_section_chunks, structured_tree, sections_dict)
                
                # 新しい見出しが除外対象か判定
                clean_title = re.sub(r'^\d+[\.\s]+', '', title).strip().lower()
                is_skipping = any(k in clean_title for k in self.exclude_keywords)
                
                if is_skipping:
                    print(f"  [TreeConstructor] Skipping excluded section: {title}")
                    current_node = None
                    current_heading = None
                    current_section_chunks = []
                    continue

                current_heading = title
                current_node = self._create_root_node(node.id, title, seq_index=node.seq_index)
                current_section_chunks = []
                
                if remaining:
                    child = TreeNode(id=f"{node.id}_b", text=remaining, role="p", seq_index=node.seq_index + 0.001)
                    current_node.children.append(child)
                    current_section_chunks.append({"text": remaining, "id": child.id})
            else:
                # 本文ノード（または見出しでない何か）
                if is_skipping:
                    continue

                if current_node is None:
                    # 最初の見出しが出る前のテキストは [Unlabeled Section] に入れる
                    current_heading = "[Unlabeled Section]"
                    current_node = self._create_root_node(f"unlabeled_{node.id}", current_heading, seq_index=node.seq_index)

                current_node.children.append(node)
                current_section_chunks.append({"text": node.text, "id": node.id})

        # 最終セクションの保存
        if current_node and not is_skipping:
            self._save_section(current_node, current_heading, current_section_chunks, structured_tree, sections_dict)

        return structured_tree, sections_dict

    def _flatten_nodes(self, nodes: List[TreeNode]) -> List[TreeNode]:
        flat = []
        def _collect(n):
            # すべてのノード（h1, p, metadata等）を収集して再構築の対象にする
            flat.append(n)
            for child in n.children: _collect(child)
        for n in nodes: _collect(n)
        return flat

    def _create_root_node(self, node_id: str, title: str, seq_index: float) -> TreeNode:
        return TreeNode(
            id=node_id, text=title,
            role="h2" if not self.is_book else "h3",
            seq_index=seq_index, children=[]
        )

    def _save_section(self, node, heading, chunks, tree, s_dict):
        key = f"{node.id}|{heading}"
        tree.append(node)
        s_dict[key] = chunks
