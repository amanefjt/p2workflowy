from typing import List, Any, Optional
from core.models import TreeNode
from core.engine.p4_translate.term_layer import TermEntry, format_term_layer

# 直前訳ウィンドウの最大文字数。断片ではなく連続した直前訳文（段落丸ごと）を渡す。
# 根拠: docs/superpowers/specs/2026-07-10-translation-context-research-notes.md
WINDOW_MAX_CHARS = 2000

class TranslationPromptBuilder:
    """
    翻訳用プロンプトの構築を専門に扱うエンジン。
    用語集、履歴、コンテキストの注入を担う。
    """
    def __init__(self, prompt_template: str, glossary: Optional[List[TermEntry]] = None):
        self.prompt_template = prompt_template
        self.glossary = glossary or []

    def format_glossary(self) -> str:
        """用語レイヤーをプロンプト用に整形する（term_layer.format_term_layer へ委譲）。"""
        return format_term_layer(self.glossary)

    def format_previous_translation(self, nodes: List[TreeNode]) -> str:
        """
        直前の翻訳結果を「連続した文脈」として整形する。
        末尾から遡って段落（role=="p"）を丸ごと集め、合計 WINDOW_MAX_CHARS を上限とする。
        最低 1 段落は必ず含める（巨大段落による空ウィンドウを防ぐ）。
        """
        if not nodes:
            return ""
        selected: List[str] = []
        total = 0
        for n in reversed(nodes):
            if n.role != "p":
                continue
            text = n.text.strip()
            if not text:
                continue
            if selected and total + len(text) > WINDOW_MAX_CHARS:
                break
            selected.append(text)
            total += len(text)
            if total >= WINDOW_MAX_CHARS:
                break
        if not selected:
            return ""
        selected.reverse()
        return "\n\n".join(["# 直前の翻訳文脈 (Context)"] + selected)

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
