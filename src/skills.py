import asyncio
from .constants import (
    STRUCTURING_WITH_HINT_PROMPT, SUMMARY_PROMPT, TRANSLATION_PROMPT, 
    MAX_TRANSLATION_CHUNK_SIZE
)
from .llm_processor import LLMProcessor
from .utils import Utils
import json
import re
from typing import List, Dict, Any, cast, Optional
from pathlib import Path

class PaperProcessorSkills:
    def __init__(self):
        self.llm = LLMProcessor()

    async def generate_resume(self, raw_text: str, context_guide: str = "", progress_callback=None) -> str:
        """
        【Phase 1】原文からレジュメ（Resume）を生成する
        """
        fmt_args = {
            "text": raw_text,
            "context_guide": context_guide
        }
        prompt = SUMMARY_PROMPT.format(**fmt_args)
        return await asyncio.to_thread(self.llm.call_api, prompt, progress_callback)

    async def structure_text_with_hint(self, raw_text: str, summary_text: str, context_guide: str = "", progress_callback=None, enable_chunking: bool = False) -> str:
        """
        【Phase 2】要約をヒントにして、生テキストを構造化する
        """
        # 書籍モード以前の状態を尊重し、デフォルトでは一括処理を行う
        fmt_args = {
            "raw_text": raw_text,
            "summary_hint": summary_text,
            "context_guide": context_guide
        }
        prompt = STRUCTURING_WITH_HINT_PROMPT.format(**fmt_args)
        return await asyncio.to_thread(self.llm.call_api, prompt, progress_callback)

    async def translate_academic(self, clean_markdown: str, glossary_text: str = "", summary_context: str = "", context_guide: str = "", progress_callback=None) -> str:
        """
        【Phase 3】Markdownをセクション単位で分割し、並列翻訳する
        """
        # 1. セクション単位での分割
        chunks = self._split_markdown_hierarchically(clean_markdown)
        
        prompts = []
        for chunk in chunks:
            if not chunk or not str(chunk).strip():
                continue
            
            # 見出しのみのチャンクを除外
            lines = chunk.strip().splitlines()
            if all(line.strip().startswith('#') for line in lines):
                continue
            
            format_args = {
                "summary_content": summary_context,
                "chunk_text": chunk,
                "glossary_content": glossary_text,
                "context_guide": context_guide
            }
            p = TRANSLATION_PROMPT.format(**format_args)
            prompts.append(p)

        if not prompts:
            return ""

        # セマフォで並列数を制限 (以前の標準である 3 に戻す)
        semaphore = asyncio.Semaphore(3)

        total = len(prompts)
        completed = 0

        async def call_with_logging(i, prompt_text):
            nonlocal completed
            async with semaphore:
                # チャンクごとの進捗表示
                if progress_callback:
                    progress_callback(f"チャンク {i+1}/{total} 翻訳中...")
                
                res_text = await asyncio.to_thread(self.llm.call_api, prompt_text, None)
                
                completed += 1
                if progress_callback:
                    progress_callback(f"チャンク {completed}/{total} 完了")
                
                return str(res_text).strip()

        tasks = [call_with_logging(i, p) for i, p in enumerate(prompts)]
        results = await asyncio.gather(*tasks)
        
        return "\n\n".join([r for r in results if r])

    def _split_markdown_hierarchically(self, text: str, max_length: int = MAX_TRANSLATION_CHUNK_SIZE) -> List[str]:
        """
        Markdownの見出し階層を考慮して構成。
        """
        sections = self._split_by_heading_level(text, level=2)
        final_chunks = []
        for section in sections:
            if len(section) <= max_length:
                final_chunks.append(section)
                continue
            
            subsections = self._split_by_heading_level(section, level=3)
            for sub in subsections:
                if len(sub) <= max_length:
                    final_chunks.append(sub)
                    continue
                
                subsubsections = self._split_by_heading_level(sub, level=4)
                for subsub in subsubsections:
                    if len(subsub) <= max_length:
                        final_chunks.append(subsub)
                        continue
                    
                    paragraphs = self._split_by_paragraph(subsub, max_length)
                    final_chunks.extend(paragraphs)
                    
        return final_chunks

    def _split_by_heading_level(self, text: str, level: int) -> List[str]:
        marker = "#" * level + " "
        lines = text.split('\n')
        chunks = []
        current_chunk = []
        for line in lines:
            if line.startswith(marker):
                if current_chunk:
                    chunks.append("\n".join(current_chunk))
                current_chunk = [line]
            else:
                current_chunk.append(line)
        if current_chunk:
            chunks.append("\n".join(current_chunk))
        return chunks

    def _split_by_paragraph(self, text: str, max_length: int) -> List[str]:
        paragraphs = text.split('\n\n')
        chunks = []
        current_chunk: List[str] = []
        current_len = 0
        for p in paragraphs:
            p_len = len(p) + 2
            if current_len + p_len > max_length and current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = [p]
                current_len = p_len
            else:
                current_chunk.append(p)
                current_len += p_len
        if current_chunk:
            chunks.append("\n\n".join(current_chunk))
        return chunks
