import json
from typing import List, Dict, Any, Optional
from core.models import TreeNode
from core.config import print_log

class TreeReconstructor:
    """
    元の英語ツリー構造を維持したまま、翻訳内容を統合するエンジン。
    Book Mode の「非対称階層（English Wrapper）」の生成も担う。
    """
    def __init__(self, is_book: bool = False, resume_only: bool = False):
        self.is_book = is_book
        self.resume_only = resume_only

    def rebuild(
        self,
        english_tree: List[TreeNode],
        translated_sections: Dict[str, List[TreeNode]],
    ) -> List[TreeNode]:
        """
        英語ツリーを「型紙」にして、翻訳プールからテキストを差し替えた日本語ツリーを再構成する。
        Book Mode の非対称構造（English Wrapper 等）はここでは生成せず、
        Phase 5 の Rendering エンジンに任せる（Chapter-as-Paper 原則）。
        """
        # 1. 全セクションの翻訳済みノードを統合した ID マップを作成
        global_id_map = {}
        for section_nodes in translated_sections.values():
            global_id_map.update(self._map_translated_nodes(section_nodes))

        japanese_tree: List[TreeNode] = []

        for en_node in english_tree:
            # 1:1 で再帰的にマッピング
            ja_node = self._recursive_map(en_node, global_id_map)
            japanese_tree.append(ja_node)

        return japanese_tree

    def _map_translated_nodes(self, pool: List[TreeNode]) -> Dict[str, TreeNode]:
        """翻訳済みプールから ID マップを作成する。"""
        id_map = {}
        def _collect(nodes):
            for n in nodes:
                if str(n.id).startswith("resume_"): continue
                id_map[str(n.id)] = n
                if n.children: _collect(n.children)
        _collect(pool)
        return id_map

    def _recursive_map(self, en_sub: TreeNode, id_map: Dict[str, TreeNode]) -> TreeNode:
        """
        英語ノードを型紙にして、テキストを日本語に差し替える。
        ロール、ID、階層構造を完全に維持する。
        """
        ja_sub = TreeNode(
            id=en_sub.id, text=en_sub.text, role=en_sub.role,
            seq_index=en_sub.seq_index, children=[],
            metadata=en_sub.metadata.copy() if en_sub.metadata else {}
        )
        
        str_id = str(en_sub.id)
        if str_id in id_map:
            # 翻訳済みノードからテキスト（または translation フィールド）を取得
            translated_node = id_map[str_id]
            ja_sub.text = getattr(translated_node, "translation", None) or translated_node.text
        elif en_sub.role == "p":
            # 段落（p）なのに翻訳がない場合は警告
            ja_sub.text = f"[翻訳欠落] {en_sub.text}"
            print_log(f"  [Reconstructor] 警告: ID {str_id} の翻訳が見つかりません。")
        
        if en_sub.children:
            for child in en_sub.children:
                ja_sub.children.append(self._recursive_map(child, id_map))
        
        return ja_sub
