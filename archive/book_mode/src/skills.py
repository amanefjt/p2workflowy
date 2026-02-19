import asyncio
from .constants import (
    STRUCTURING_WITH_HINT_PROMPT, SUMMARY_PROMPT, TRANSLATION_PROMPT, 
    MAX_TRANSLATION_CHUNK_SIZE, MAX_STRUCTURING_CHUNK_SIZE
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
        # format引数を辞書で用意（将来的な拡張にも対応）
        fmt_args = {
            "text": raw_text,
            "context_guide": context_guide
        }
        prompt = SUMMARY_PROMPT.format(**fmt_args)
        return await asyncio.to_thread(self.llm.call_api, prompt, progress_callback)

    async def structure_text_with_hint(self, raw_text: str, summary_text: str, context_guide: str = "", progress_callback=None, enable_chunking: bool = True) -> str:
        """
        【Phase 2】要約をヒントにして、生テキストを構造化する
        enable_chunking: Trueの場合、長文を分割して処理する（書籍モード用）
        """
        if not enable_chunking:
             # Paper Mode (Legacy/Simple path)
            fmt_args = {
                "raw_text": raw_text,
                "summary_hint": summary_text,
                "context_guide": context_guide
            }
            prompt = STRUCTURING_WITH_HINT_PROMPT.format(**fmt_args)
            return await asyncio.to_thread(self.llm.call_api, prompt, progress_callback)

        # Book Mode (Chunking path)
        chunk_size = MAX_STRUCTURING_CHUNK_SIZE
        
        chunks = []
        start = 0
        while start < len(raw_text):
            end = start + chunk_size
            if end < len(raw_text):
                # 末尾の改行を探して区切る
                next_newline = raw_text.find('\n', end)
                if next_newline != -1 and next_newline - end < 2000:
                    end = next_newline + 1
            chunks.append(raw_text[start:end])
            start = end

        if not chunks:
            return ""

        # チャンクごとに構造化 (順次処理)
        structured_parts = []
        total_chunks = len(chunks)
        
        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
                
            if progress_callback:
                progress_callback(f"Structuring Part {i+1}/{total_chunks}")
            
            # コンテキストガイドにパート情報を付与
            current_context = f"{context_guide} (Part {i+1}/{total_chunks})"
            
            fmt_args = {
                "raw_text": chunk,
                "summary_hint": summary_text,
                "context_guide": current_context
            }
            prompt = STRUCTURING_WITH_HINT_PROMPT.format(**fmt_args)
            part_res = await asyncio.to_thread(self.llm.call_api, prompt)
            structured_parts.append(part_res)
        
        # 結合
        return "\n\n".join(structured_parts)

    async def translate_academic(self, clean_markdown: str, glossary_text: str = "", summary_context: str = "", context_guide: str = "", progress_callback=None) -> str:
        """
        【Phase 3】Markdownをセクション単位で分割し、並列翻訳する
        """
        # 1. セクション単位での分割 (階層的分割を採用)
        chunks = self._split_markdown_hierarchically(clean_markdown)
        
        # 2. プロンプト作成
        prompts = []
        valid_chunks = []
        
        for chunk in chunks:
            if not chunk or not str(chunk).strip():
                continue
            
            # 見出しのみのチャンクを除外
            lines = chunk.strip().splitlines()
            if all(line.strip().startswith('#') for line in lines):
                continue
            
            valid_chunks.append(chunk)

            # 安全なフォーマット（辞書ベース）
            format_args = {
                "summary_content": summary_context,
                "chunk_text": chunk,
                "glossary_content": glossary_text,
                "context_guide": context_guide
            }
            try:
                p = TRANSLATION_PROMPT.format(**format_args)
                prompts.append(p)
            except KeyError as e:
                print(f"Error building prompt: Missing key {e}")
                return f"[システムエラー: プロンプトの変数が一致しません: {e}]"

        if not prompts:
            return ""

        # 3. 並列実行
        if progress_callback:
            progress_callback(f"{len(prompts)}個のチャンクを並列翻訳中...")
        
        # セマフォで並列数を制限
        semaphore = asyncio.Semaphore(2)

        async def call_with_logging(i, prompt_text):
            async with semaphore:
                chunk_msg = f"    [Chunk {i+1}/{len(prompts)}]"
                
                try:
                    # 開始ログ
                    start_msg = f"{chunk_msg} 翻訳開始..."
                    if progress_callback: progress_callback(start_msg)
                    else: print(start_msg)

                    inner_cb = lambda msg: (progress_callback(f"{chunk_msg} {msg}") if progress_callback else print(f"{chunk_msg} {msg}"))
                    
                    res_text = await asyncio.to_thread(self.llm.call_api, prompt_text, inner_cb)
                    res_text = str(res_text).strip()

                    # 完了ログ
                    finish_msg = f"{chunk_msg} 翻訳完了! ({len(res_text)}文字)"
                    if progress_callback: progress_callback(finish_msg)
                    else: print(finish_msg)

                    # --- メタコメンタリー（翻訳拒否）のフィルタリング ---
                    meta_keywords = [
                        "申し訳ありませんが",
                        "翻訳対象となる",
                        "ご提示いただけますでしょうか",
                        "プロンプトの指示",
                        "AIとして",
                        "I cannot translate",
                        "context provided",
                        "target text",
                        "only the heading",
                        "翻訳できません",
                        "提供されたテキスト"
                    ]
                    
                    # キーワードが含まれ、かつ全体の長さが短い場合はメタコメントとみなす
                    if len(res_text) < 500:
                        for kw in meta_keywords:
                            if kw in res_text:
                                error_log = f"{chunk_msg} (Warn) Refusal detected, skipping: {res_text[:50]}..."
                                if progress_callback:
                                    progress_callback(error_log)
                                return "" 

                    return res_text
                    
                except Exception as e:
                    err_msg = f"{chunk_msg} FAILED: {e}"
                    if progress_callback:
                        progress_callback(err_msg)
                    return f"[翻訳処理中にエラーが発生しました: {str(e)}]"

        # ラッパーで実行
        logged_tasks = [call_with_logging(i, p) for i, p in enumerate(prompts)]
        results = await asyncio.gather(*logged_tasks)
        
        # 空文字列（メタコメント除去分）を除外して結合
        valid_results = [r for r in results if r]
        return "\n\n".join(valid_results)

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


