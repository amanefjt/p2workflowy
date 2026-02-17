import asyncio
from .constants import (
    STRUCTURING_WITH_HINT_PROMPT, SUMMARY_PROMPT, TRANSLATION_PROMPT, MAX_TRANSLATION_CHUNK_SIZE
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

    async def generate_resume(self, raw_text: str, progress_callback=None) -> str:
        """
        【Phase 1】原文からレジュメ（Resume）を生成する
        """
        prompt = SUMMARY_PROMPT.format(text=raw_text)
        return await asyncio.to_thread(self.llm.call_api, prompt, progress_callback)

    async def structure_text_with_hint(self, raw_text: str, summary_text: str, progress_callback=None) -> str:
        """
        【Phase 2】要約をヒントにして、生テキストを構造化する
        """
        prompt = STRUCTURING_WITH_HINT_PROMPT.format(
            raw_text=raw_text,
            summary_hint=summary_text
        )
        return await asyncio.to_thread(self.llm.call_api, prompt, progress_callback)

    async def translate_academic(self, clean_markdown: str, glossary_text: str = "", summary_context: str = "", progress_callback=None) -> str:
        """
        【Phase 3】Markdownをセクション単位で分割し、並列翻訳する
        """
        # 1. セクション単位での分割 (階層的分割を採用)
        chunks = self._split_markdown_hierarchically(clean_markdown)
        
        # 2. プロンプト作成
        prompts = []
        for chunk in chunks:
            if not chunk or not str(chunk).strip():
                continue
            try:
                p = TRANSLATION_PROMPT.format(
                    summary_content=summary_context,
                    chunk_text=chunk,
                    glossary_content=glossary_text
                )
                prompts.append(p)
            except KeyError as e:
                print(f"Error building prompt: Missing key {e}")
                return f"[システムエラー: プロンプトの変数が一致しません: {e}]"

        if not prompts:
            return ""

        # 3. 並列実行
        # print(f"  ... {len(prompts)}個のセクションを並列翻訳中 (同時実行数: 3) ...")
        if progress_callback:
            progress_callback(f"{len(prompts)}個のチャンクを並列翻訳中...")
        
        # セマフォで並列数を制限
        semaphore = asyncio.Semaphore(3)

        async def call_with_logging(i, prompt_text):
            async with semaphore:
                chunk_msg = f"    [Chunk {i+1}/{len(prompts)}]"
                if progress_callback:
                    progress_callback(f"{chunk_msg} 翻訳開始")
                else:
                    print(f"{chunk_msg} Starting translation...")
                
                try:
                    # 個別のチャンクのリトライ状況を表示するために callback を作成
                    inner_cb = lambda msg: (progress_callback(f"{chunk_msg} {msg}") if progress_callback else print(f"{chunk_msg} {msg}"))
                    res = await asyncio.to_thread(self.llm.call_api, prompt_text, inner_cb)
                    
                    if progress_callback:
                        progress_callback(f"{chunk_msg} 完了")
                    else:
                        print(f"{chunk_msg} Finished.")
                    return str(res)
                except Exception as e:
                    err_msg = f"{chunk_msg} FAILED: {e}"
                    if progress_callback:
                        progress_callback(err_msg)
                    else:
                        print(err_msg)
                    return f"[翻訳処理中にエラーが発生しました: {str(e)}]"

        # ラッパーで実行
        logged_tasks = [call_with_logging(i, p) for i, p in enumerate(prompts)]
        results = await asyncio.gather(*logged_tasks)
        
        return "\n\n".join([str(res) for res in results])

    def _split_markdown_hierarchically(self, text: str, max_length: int = MAX_TRANSLATION_CHUNK_SIZE) -> List[str]:
        """
        Markdownの見出し階層を考慮して階層的に分割する。
        """
        # 1. まず H2 で分割
        sections = self._split_by_heading_level(text, level=2)
        
        final_chunks = []
        for section in sections:
            if len(section) <= max_length:
                final_chunks.append(section)
                continue
            
            # 2. H2 が大きすぎる場合、H3 で分割
            subsections = self._split_by_heading_level(section, level=3)
            for sub in subsections:
                if len(sub) <= max_length:
                    final_chunks.append(sub)
                    continue
                
                # 3. H3 も大きすぎる場合、H4 で分割
                subsubsections = self._split_by_heading_level(sub, level=4)
                for subsub in subsubsections:
                    if len(subsub) <= max_length:
                        final_chunks.append(subsub)
                        continue
                    
                    # 4. H4 も大きすぎる場合、段落 (\n\n) で分割
                    paragraphs = self._split_by_paragraph(subsub, max_length)
                    final_chunks.extend(paragraphs)
                    
        return final_chunks

    def _split_by_heading_level(self, text: str, level: int) -> List[str]:
        """指定されたレベルのMarkdown見出しで分割する"""
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
        """段落 (\n\n) を境界にして分割する"""
        paragraphs = text.split('\n\n')
        chunks = []
        current_chunk: List[str] = []
        current_len = 0
        
        for p in paragraphs:
            p_len = len(p) + 2 # \n\n 分
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


