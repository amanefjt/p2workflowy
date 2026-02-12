# -*- coding: utf-8 -*-
"""
skills.py: LLMの「認知スキル」をカプセル化したクラス
"""
from typing import Optional, Callable
from .llm_processor import LLMProcessor
from .constants import (
    STRUCTURING_PROMPT,
    STRUCTURING_WITH_HINT_PROMPT,
    SUMMARY_PROMPT,
    TRANSLATION_PROMPT,
    DEFAULT_MODEL
)

class PaperProcessorSkills:
    """学術論文処理の認知スキルを提供するクラス"""

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.llm = LLMProcessor(model_name=model_name)

    async def summarize_raw_text(self, raw_text: str, progress_callback: Optional[Callable] = None) -> str:
        """
        【Phase 1】崩れたテキストから直接意味的な要約（論理構造の地図）を作成する。
        """
        # 長い場合はチャンク分割して要約する機能が本来は必要だが、
        # Geminiの大きなコンテキストを活かし、ここでは1回で試みる（または統合要約を生成）。
        prompt = SUMMARY_PROMPT.format(text=raw_text)
        import asyncio
        return await asyncio.to_thread(self.llm.call_api, prompt, progress_callback)

    async def structure_text_with_hint(self, raw_text: str, summary_text: str, progress_callback: Optional[Callable] = None) -> str:
        """
        【Phase 2】要約（地図）をヒントにして、生テキストを Markdown 構造化する。
        """
        # 一度に出力できるトークン制限に注意しつつ実行
        prompt = STRUCTURING_WITH_HINT_PROMPT.format(
            raw_text=raw_text,
            summary_hint=summary_text
        )
        import asyncio
        structured = await asyncio.to_thread(self.llm.call_api, prompt, progress_callback)
        
        # クリーニング処理
        import re
        if "</thought>" in structured:
            structured = structured.split("</thought>")[-1].strip()
        structured = re.sub(r'^```(markdown)?\n', '', structured, flags=re.MULTILINE)
        structured = re.sub(r'\n```$', '', structured, flags=re.MULTILINE)
        structured = re.sub(r'<thought>.*?</thought>', '', structured, flags=re.DOTALL)
        
        return structured.strip()

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

    async def translate_academic(
        self, 
        clean_markdown: str, 
        glossary_text: str = "", 
        summary_text: str = "",
        progress_callback: Optional[Callable] = None
    ) -> str:
        """
        Markdown構造を維持し、要約と辞書をコンテキストとして与えて並列翻訳するスキル。
        """
        from .utils import Utils
        import re
        import asyncio
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # セクション分割
        sections = Utils.split_into_sections(clean_markdown)
        translated_parts = [""] * len(sections)

        def process_section(idx, section_content, section_title):
            try:
                if not section_content.strip():
                    return idx, ""
                
                # APIコール
                p = TRANSLATION_PROMPT.format(
                    text=section_content,
                    glossary_content=glossary_text,
                    summary_content=summary_text
                )
                trans = self.llm.call_api(p, None)
                
                # クリーニング
                if "<result>" in trans and "</result>" in trans:
                    match = re.search(r'<result>(.*?)</result>', trans, re.DOTALL)
                    if match: trans = match.group(1).strip()
                
                trans = re.sub(r'^```(markdown)?\n', '', trans, flags=re.MULTILINE)
                trans = re.sub(r'\n```$', '', trans, flags=re.MULTILINE)
                trans = re.sub(r'<thought>.*?</thought>', '', trans, flags=re.DOTALL)
                trans = re.sub(r'<[^>]+>', '', trans)
                trans = re.sub(r'\*\*([^*]+)\*\*', r'\1', trans) 
                
                return idx, trans.strip()
            except Exception as ex:
                return idx, f"[翻訳エラー: {section_title}]"

        # 並列実行
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_idx = {executor.submit(process_section, i, s["content"], s["title"]): i for i, s in enumerate(sections)}
            
            completed = 0
            for future in as_completed(future_to_idx):
                idx, result = future.result()
                translated_parts[idx] = result
                completed += 1
                if progress_callback:
                    progress_callback(f"翻訳中... ({completed}/{len(sections)})")

        return "\n\n".join([p for p in translated_parts if p])
