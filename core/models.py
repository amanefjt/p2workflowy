"""
p2workflowy 黄金の再構築: 物理データ主権対応データモデル
規約 01_physical_evidence_sovereignty.md に準拠した決定論的データクラス。
"""

import json
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Union


@dataclass
class RawChunk:
    """Phase 1 Preprocessor 出力: 物理情報付きクレンジング済みテキストチャンク"""
    id: str
    text: str
    seq_index: float
    page_idx: int = -1
    # 物理証拠 (Sovereignty Props)
    font_size: float = 0.0
    is_bold: bool = False
    is_italic: bool = False
    font_name: Optional[str] = None
    bbox: Optional[List[float]] = None  # [x0, y0, x1, y1]
    # 論理的属性: VLM主権に基づく役割判定
    role: str = "p"  # "h1", "p", "metadata", "note"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RawChunk":
        # 未知のキーをフィルタリング: フィールド追加後に古い JSON を --resume で読んでも TypeError にならない
        valid_keys = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in valid_keys})


@dataclass
class TreeNode:
    """Phase 3-5 で使用: 構造化ツリーノード"""
    id: str
    text: str
    role: str = "p"     # "h1", "h2", "h3", "h4", "p"
    seq_index: float = 0.0
    children: List["TreeNode"] = field(default_factory=list)
    # 物理証拠 (Sovereignty Props: Structure 判定の主権)
    font_size: float = 0.0
    is_bold: bool = False
    is_italic: bool = False
    font_name: Optional[str] = None
    bbox: Optional[List[float]] = None
    page_idx: int = -1
    # 翻訳・要約用
    translation: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "text": self.text,
            "role": self.role,
            "seq_index": self.seq_index,
            "font_size": self.font_size,
            "is_bold": self.is_bold,
            "is_italic": self.is_italic,
            "font_name": self.font_name,
            "bbox": self.bbox,
            "page_idx": self.page_idx,
            "translation": self.translation,
            "metadata": self.metadata,
        }
        if self.children:
            d["children"] = [child.to_dict() for child in self.children]
        else:
            d["children"] = []
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "TreeNode":
        children_data = data.get("children", [])  # get() で元の辞書を破壊しない
        children = [cls.from_dict(c) for c in children_data]
        valid_keys = cls.__dataclass_fields__.keys()
        node_data = {k: v for k, v in data.items() if k != "children" and k in valid_keys}
        return cls(children=children, **node_data)


@dataclass
class ProcessingContext:
    """セッション全体の進行状態とメタデータを管理するコンテキストクラス"""
    session_id: str
    mode: str = "paper"  # "paper" or "book"
    
    # 5-Phase Path Management
    phase1_preprocessor: Optional[str] = None
    phase2_meta: Optional[str] = None
    phase3_structure: Optional[str] = None
    phase4_translate: Optional[str] = None
    phase5_export: Optional[str] = None
    
    # Global Metadata
    title: Optional[str] = None
    authors: List[str] = field(default_factory=list)
    abstract: Optional[str] = None
    
    # State flags
    is_complete: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ProcessingContext":
        valid_keys = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in valid_keys})


# --- JSON シリアライズ/デシリアライズ ヘルパー ---

def save_chunks_to_json(chunks: List[RawChunk], path: str) -> None:
    data = [c.to_dict() for c in chunks]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_chunks_from_json(path: str) -> List[RawChunk]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [RawChunk.from_dict(d) for d in data]


def save_tree_to_json(tree: List[TreeNode], path: str) -> None:
    data = [node.to_dict() for node in tree]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_tree_from_json(path: str) -> List[TreeNode]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [TreeNode.from_dict(d) for d in data]
