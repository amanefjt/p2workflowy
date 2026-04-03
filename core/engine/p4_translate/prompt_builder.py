from typing import List, Dict, Any, Optional
from core.models import TreeNode

class TranslationPromptBuilder:
    """
    翻訳用プロンプトの構築を専門に扱うエンジン。
    用語集、履歴、コンテキストの注入を担う。
    """
    def __init__(self, prompt_template: str, glossary: Optional[Dict[str, str]] = None):
        self.prompt_template = prompt_template
        self.glossary = glossary or {}

    def format_glossary(self) -> str:
        """用語集をプロンプト用に整形する。"""
        if not self.glossary:
            return ""
        lines = ["# 用語集 (Glossary)", "以下の用語は、指定された日本語訳を優先的に使用してください。"]
        for en, ja in self.glossary.items():
            lines.append(f"- {en}: {ja}")
        return "\n".join(lines)

    def format_previous_translation(self, nodes: List[TreeNode], max_nodes: int = 3) -> str:
        """
        以前の翻訳結果を履歴として整形する。
        翻訳の一貫性を保つためのコンテキスト。
        """
        if not nodes:
            return ""
        recent = nodes[-max_nodes:]
        lines = ["# 以前の翻訳履歴 (Context)"]
        for n in recent:
            if n.role == "p":
                # 改行を除去して1行に簡略化
                snippet = n.text.replace("\n", " ")[:200]
                lines.append(f"- {snippet}")
        return "\n".join(lines)

    def build_section_prompt(
        self,
        section_name: str,
        resume_content: str,
        expertise: str = "文化人類学",
        thinking_level: str = "High",
        is_book: bool = False
    ) -> str:
        """セクションに応じた共通指示を構築する。 (将来的な拡張用)"""
        # 現在は主に llm_client.translate_batch 内部で組み立てているが、
        # 将来的にはこちらにロジックを集約する。
        pass
