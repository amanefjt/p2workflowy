"""
p2workflowy V2: データモデル定義
indi_io_spec.md に準拠した RawChunk / TreeNode データクラス。
"""

from dataclasses import dataclass, field, asdict
from typing import List, Union
import json


@dataclass
class RawChunk:
    """Phase 1 出力: クレンジング済みテキストチャンク"""
    id: Union[str, int]
    text: str
    seq_index: float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RawChunk":
        return cls(
            id=data["id"],
            text=data["text"],
            seq_index=data["seq_index"],
        )


@dataclass
class TreeNode:
    """Phase 3-5 で使用: 構造化ツリーノード"""
    id: Union[str, int]
    text: str
    role: str          # "h2" または "p"
    seq_index: float   # 物理的な出現順序を保持
    children: List["TreeNode"] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "text": self.text,
            "role": self.role,
            "seq_index": self.seq_index,
        }
        if self.children:
            d["children"] = [child.to_dict() for child in self.children]
        else:
            d["children"] = []
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "TreeNode":
        children = [cls.from_dict(c) for c in data.get("children", [])]
        return cls(
            id=data["id"],
            text=data["text"],
            role=data["role"],
            seq_index=data["seq_index"],
            children=children,
        )


# --- JSON シリアライズ/デシリアライズ ヘルパー ---

def save_chunks_to_json(chunks: List[RawChunk], path: str) -> None:
    """RawChunk リストを JSON ファイルに保存する。"""
    data = [c.to_dict() for c in chunks]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_chunks_from_json(path: str) -> List[RawChunk]:
    """JSON ファイルから RawChunk リストを読み込む。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [RawChunk.from_dict(d) for d in data]


def save_tree_to_json(tree: List[TreeNode], path: str) -> None:
    """TreeNode リストを JSON ファイルに保存する。"""
    data = [node.to_dict() for node in tree]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_tree_from_json(path: str) -> List[TreeNode]:
    """JSON ファイルから TreeNode リストを読み込む。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [TreeNode.from_dict(d) for d in data]
