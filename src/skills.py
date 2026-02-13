import asyncio
import json
import re
from .constants import (
    STRUCTURING_WITH_HINT_PROMPT, SUMMARY_PROMPT, TRANSLATION_PROMPT, 
    MAX_TRANSLATION_CHUNK_SIZE,
    BOOK_STRUCTURE_PROMPT, BOOK_CHAPTER_SUMMARY_PROMPT, 
    BOOK_STRUCTURING_PROMPT, BOOK_TRANSLATION_PROMPT
)
from .llm_processor import LLMProcessor

class PaperProcessorSkills:
    def __init__(self):
        self.llm = LLMProcessor()

    # --- Book Mode Skills ---

    async def analyze_book_structure(self, full_text: str, progress_callback=None) -> dict:
        """
        書籍全体を分析し、要約と正確な目次(TOC)をJSON形式で抽出する
        Returns:
            dict: {"book_summary": str, "chapters": list[dict]}
        """
        # プロンプト内にJSONの例（波括弧）が含まれているため、format()ではなくreplace()を使用する
        prompt = BOOK_STRUCTURE_PROMPT.replace("{text}", full_text)
        response_text = await asyncio.to_thread(self.llm.call_api, prompt, progress_callback)
        
        # JSON抽出のロバスト化
        try:
            # Markdownのコードブロックが含まれている場合への対応
            json_match = re.search(r'```json\s*(.*?)```', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = response_text
            
            data = json.loads(json_str)
            # 古いフォーマット（リストのみ）が返ってきた場合の互換性対応
            if isinstance(data, list):
                return {"book_summary": "Summary not generated.", "chapters": data}
            return data
            
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON from LLM: {e}")
            print(f"Raw Response: {response_text}")
            raise ValueError(f"Failed to parse TOC JSON: {e}")

    def split_by_anchors(self, full_text: str, structure_data: dict) -> list[dict]:
        """
        アンカーテキストに基づいて全文を章ごとに分割する
        Returns:
            list[dict]: [{"title": str, "text": str, "summary": str}, ...]
        """
        chapters_toc = structure_data.get("chapters", [])
        if not chapters_toc:
            return [{"title": "Full Text", "text": full_text}]

        indices = []
        valid_chapters = []

        # 検索開始位置
        current_search_pos = 0
        
        for chapter in chapters_toc:
            anchor = chapter.get("start_text", "").strip()
            title = chapter.get("title", "Unknown Chapter")
            
            if not anchor:
                print(f"Warning: No anchor text for '{title}'")
                continue
            
            # アンカーを検索
            pos = full_text.find(anchor, current_search_pos)
            
            if pos == -1:
                print(f"Warning: Anchor not found for '{title}': '{anchor}'")
                continue
                
            indices.append(pos)
            valid_chapters.append(chapter)
            current_search_pos = pos + 1
            
        chunks_info = []
        for i in range(len(indices)):
            start = indices[i]
            # 次の章の開始直前まで、または最後まで
            end = indices[i+1] if i + 1 < len(indices) else len(full_text)
            
            # スライス
            chunk_text = full_text[start:end]
            
            # 対応するTOC情報
            toc_item = valid_chapters[i]
            
            chunks_info.append({
                "title": toc_item.get("title"),
                "text": chunk_text
            })
            
        return chunks_info

    async def summarize_chapter(self, overall_summary: str, chapter_text: str, progress_callback=None) -> str:
        """
        特定の章を、全体文脈を踏まえて要約する
        """
        prompt = BOOK_CHAPTER_SUMMARY_PROMPT.format(
            overall_summary=overall_summary,
            chapter_text=chapter_text
        )
        return await asyncio.to_thread(self.llm.call_api, prompt, progress_callback)

    async def structure_chapter(self, overall_summary: str, chapter_text: str, progress_callback=None) -> str:
        """
        特定の章の英語構造を整理する
        長文の場合は分割して処理する
        """
        chunks = self._split_text_by_length(chapter_text)
        tasks = []
        
        for chunk in chunks:
            prompt = BOOK_STRUCTURING_PROMPT.format(
                overall_summary=overall_summary,
                chapter_text=chunk
            )
            tasks.append(asyncio.to_thread(self.llm.call_api, prompt, None))
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        final_results = []
        for res in results:
            if isinstance(res, Exception):
                final_results.append(str(res))
            else:
                final_results.append(res)
                
        return "\n\n".join(final_results)

    async def translate_chapter(self, overall_summary: str, chapter_summary: str, chapter_text: str, glossary_text: str = "", progress_callback=None) -> str:
        """
        特定の章を、全体文脈と章の要約を踏まえて翻訳する
        """
        # 章の翻訳もさらにチャンク分割が必要な場合がある（30,000文字超え等）
        chunks = self._split_text_by_length(chapter_text)
        tasks = []
        for chunk in chunks:
            prompt = BOOK_TRANSLATION_PROMPT.format(
                overall_summary=overall_summary,
                chapter_summary=chapter_summary,
                glossary_content=glossary_text,
                chunk_text=chunk
            )
            tasks.append(asyncio.to_thread(self.llm.call_api, prompt, None))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        final_results = []
        for res in results:
            if isinstance(res, Exception):
                final_results.append(f"[Chapter翻訳エラー: {str(res)}]")
            else:
                final_results.append(res)
        return "\n\n".join(final_results)

    # --- Paper Mode Skills (Existing) ---

    async def summarize_raw_text(self, raw_text: str, progress_callback=None) -> str:
        """
        【Phase 1】崩れたテキストから直接要約を作成する
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
        【Phase 3】Markdownをセクションごとに分割し、並列翻訳する
        """
        # 1. セクション分割
        chunks = self._split_markdown_by_headers(clean_markdown)
        
        # 2. タスク作成
        tasks = []
        for chunk in chunks:
            if not chunk.strip():
                continue
            
            try:
                # プロンプトの変数を埋め込む
                prompt = TRANSLATION_PROMPT.format(
                    summary_content=summary_context,  # 要約
                    chunk_text=chunk,                 # 翻訳対象のセクション
                    glossary_content=glossary_text    # 辞書
                )
                # 並列実行時は progress_callback を渡すと表示が乱れるため None にする
                tasks.append(asyncio.to_thread(self.llm.call_api, prompt, None))
            except KeyError as e:
                print(f"Error building prompt: Missing key {e}")
                return f"[システムエラー: プロンプトの変数が一致しません: {e}]"

        # 3. 並列実行
        print(f"  ... {len(tasks)}個のセクションを並列翻訳中 ...")
        
        if not tasks:
            return ""

        # エラーが起きても止まらないように return_exceptions=True にする
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # エラーがあれば文字列に変換して結合
        final_results = []
        for res in results:
            if isinstance(res, Exception):
                final_results.append(f"[翻訳処理中にエラーが発生しました: {str(res)}]")
            else:
                final_results.append(res)
        
        return "\n\n".join(final_results)

    def _split_markdown_by_headers(self, text: str) -> list[str]:
        """
        Markdownを見出し(#)単位で分割する。
        さらに、セクションが長すぎる場合は MAX_TRANSLATION_CHUNK_SIZE を目安に分割する。
        """
        lines = text.split('\n')
        chunks = []
        current_chunk = []
        
        for line in lines:
            if line.strip().startswith('#'):
                if current_chunk:
                    chunks.append("\n".join(current_chunk))
                current_chunk = [line]
            else:
                current_chunk.append(line)
        
        if current_chunk:
            chunks.append("\n".join(current_chunk))
            
        # 分割後の再チェックと強制分割処理
        final_chunks = []
        for chunk in chunks:
            if len(chunk) > MAX_TRANSLATION_CHUNK_SIZE:
                final_chunks.extend(self._split_text_by_length(chunk))
            else:
                final_chunks.append(chunk)
                
        return final_chunks

    def _split_text_by_length(self, text: str, max_length: int = MAX_TRANSLATION_CHUNK_SIZE) -> list[str]:
        """長大なテキストを改行基準で分割する"""
        if len(text) <= max_length:
            return [text]
            
        chunks = []
        current_chunk = []
        current_length = 0
        
        lines = text.split('\n')
        for line in lines:
            # 行単体で長すぎる場合はそのまま追加（やむを得ない）
            line_len = len(line) + 1 # 改行分
            
            if current_length + line_len > max_length:
                # 現在のチャンクを確定
                if current_chunk:
                    chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_length = line_len
            else:
                current_chunk.append(line)
                current_length += line_len
                
        if current_chunk:
            chunks.append("\n".join(current_chunk))
            
        return chunks