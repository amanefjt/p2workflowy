# -*- coding: utf-8 -*-
"""
skills.py: LLMの「認知スキル」をカプセル化したクラス
"""
from typing import Optional, Callable
from .llm_processor import LLMProcessor
from .constants import (
    STRUCTURING_PROMPT,
    SUMMARY_PROMPT,
    TRANSLATION_PROMPT,
    DEFAULT_MODEL
)

class PaperProcessorSkills:
    """学術論文処理の認知スキルを提供するクラス"""

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.llm = LLMProcessor(model_name=model_name)

    def restore_structure(self, raw_text: str, progress_callback: Optional[Callable] = None) -> str:
        """
        PDF由来の崩れた生テキストを、構造化されたMarkdownに変換するスキル。
        大容量ファイルの場合、分割して実行する。
        Gemini 2.0/3.0 Flash の能力に合わせ、チャンクサイズを拡大（3.5万文字）。
        """
        from .utils import Utils
        
        # 3.5万文字 = 約1万トークンの出力を見込む（出力制限16kトークン内）
        chunks = Utils.split_text_into_chunks(raw_text, chunk_size=35000)
        structured_parts = []
        
        for i, chunk in enumerate(chunks):
            if progress_callback:
                progress_callback(f"構造復元中 ({i+1}/{len(chunks)})")
            
            prompt = STRUCTURING_PROMPT.format(text=chunk)
            part = self.llm.call_api(prompt, progress_callback)
            
            # 不要なタグやマーカーを除去（より堅牢に）
            if "</thought>" in part:
                part = part.split("</thought>")[-1].strip()
            
            import re
            # Markdownコードブロックを削除
            part = re.sub(r'^```(markdown)?\n', '', part, flags=re.MULTILINE)
            part = re.sub(r'\n```$', '', part, flags=re.MULTILINE)
            
            # 思考プロセスがタグで囲まれて残っている場合を削除
            part = re.sub(r'<thought>.*?</thought>', '', part, flags=re.DOTALL)
            
            structured_parts.append(part.strip())
            
        full_text = "\n\n".join(structured_parts)
        
        # 重複見出し（Introduction/Conclusion）を最初の一つ以外削除する
        def deduplicate_headers(text: str, header_regex: str) -> str:
            lines = text.split('\n')
            new_lines = []
            found_first = False
            import re
            for line in lines:
                # "# Introduction" などの形式にマッチ（大文字小文字不問）
                if re.match(header_regex, line, re.IGNORECASE):
                    if not found_first:
                        new_lines.append(line)
                        found_first = True
                    else:
                        # 2回目以降はスキップ（削除）
                        continue
                else:
                    new_lines.append(line)
            return '\n'.join(new_lines)

        full_text = deduplicate_headers(full_text, r'^#\s+Introduction')
        full_text = deduplicate_headers(full_text, r'^#\s+Conclusion')
        
        # 連続した空行を2つまでに制限
        import re
        full_text = re.sub(r'\n{3,}', '\n\n', full_text)
        
        return full_text.strip()

    def summarize_workflowy(self, clean_markdown: str, progress_callback: Optional[Callable] = None) -> str:
        """
        構造化されたMarkdownから、Workflowy形式の要約を作成するスキル。
        """
        prompt = SUMMARY_PROMPT.format(text=clean_markdown)
        return self.llm.call_api(prompt, progress_callback)

    def translate_academic(
        self, 
        clean_markdown: str, 
        glossary_text: str = "", 
        progress_callback: Optional[Callable] = None
    ) -> str:
        """
        Markdown構造を維持し、辞書（Glossary）を適用して翻訳するスキル。
        """
        prompt = TRANSLATION_PROMPT.format(
            text=clean_markdown,
            glossary_content=glossary_text
        )
        translated = self.llm.call_api(prompt, progress_callback)
        
        # クリーニング
        import re
        
        # <result> タグがある場合はその中身を抽出
        if "<result>" in translated and "</result>" in translated:
            match = re.search(r'<result>(.*?)</result>', translated, re.DOTALL)
            if match:
                translated = match.group(1).strip()
        elif "</thought>" in translated:
            translated = translated.split("</thought>")[-1].strip()
            
        # Markdownコードブロックを削除
        translated = re.sub(r'^```(markdown)?\n', '', translated, flags=re.MULTILINE)
        translated = re.sub(r'\n```$', '', translated, flags=re.MULTILINE)
        
        # 思考タグの除去
        translated = re.sub(r'<thought>.*?</thought>', '', translated, flags=re.DOTALL)
        
        # 不要なメタタグを除去
        translated = re.sub(r'<[^>]+>', '', translated)
        
        # ボールドの過剰適用を抑制
        translated = re.sub(r'\*\*([^*]+)\*\*', r'\1', translated) 
        
        return translated.strip()
