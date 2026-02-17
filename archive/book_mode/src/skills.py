import asyncio
from .constants import (
    STRUCTURING_WITH_HINT_PROMPT, SUMMARY_PROMPT, TRANSLATION_PROMPT, MAX_TRANSLATION_CHUNK_SIZE,
    BOOK_GLOBAL_RESUME_PROMPT, BOOK_TOC_EXTRACTION_PROMPT, BOOK_CHAPTER_RESUME_PROMPT,
    BOOK_CHAPTER_STRUCTURING_PROMPT, BOOK_SECTION_TOC_EXTRACTION_PROMPT
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


class BookProcessorSkills(PaperProcessorSkills):
    """
    【書籍モード】専用のスキルセット
    """
    async def generate_global_resume(self, raw_text: str, progress_callback=None) -> str:
        """
        【Step 1】全体に対して学術的レジュメを生成する
        """
        prompt = BOOK_GLOBAL_RESUME_PROMPT.format(text=raw_text)
        return await asyncio.to_thread(self.llm.call_api, prompt, progress_callback)

    async def extract_toc(self, raw_text: str, progress_callback=None) -> List[Dict[str, Any]]:
        """
        【Step 2】全体テキスト（特に冒頭）から目次を抽出する
        """
        # raw_text is already a str, no need for str()
        sample_text = raw_text[:50000]
        prompt = BOOK_TOC_EXTRACTION_PROMPT.format(text=sample_text)
        res_text = await asyncio.to_thread(self.llm.call_api, prompt, progress_callback)
        
        try:
            json_match = re.search(r'\[.*\]', res_text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                return cast(List[Dict[str, Any]], data)
            return cast(List[Dict[str, Any]], json.loads(res_text.strip()))
        except Exception as e:
            print(f"Error parsing TOC JSON: {e}")
            return []

    def split_by_toc(self, raw_text: str, toc: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        【Step 3】目次を参考に、テキストを分割する（正規表現によるファジーマッチング対応）
        TOCの順序を優先し、順次検索を行うことで誤マッチを防ぐ。
        """
        chapters: List[Dict[str, Any]] = []
        found_chapters: List[Dict[str, Any]] = []
        
        # 目次エリアの推定（誤マッチ防止のため、最初の検索はこの位置から開始）
        # ただし、順次検索を行うため、最初の章（Notesなど）が正しく見つかれば、
        # その後の章はlast_pos以降から検索されるため、冒頭の誤マッチ（Introduction等）は回避される。
        # したがって初期値は 0 とする。
        last_pos = 0
            
        print(f"Step 3: Splitting from pos {last_pos}...")
        
        for item in toc:
            title = str(item.get("title", "")).strip()
            start_text = str(item.get("start_text", "")).strip()
            
            if not title:
                continue
            
            # 検索用の正規表現を作成（複数の空白・改行を許容）
            def make_fuzzy_regex(text: str):
                # 特殊文字をエスケープし、空白を \s+ に置換
                escaped = re.escape(text)
                return escaped.replace(r'\ ', r'\s+')

            idx = -1
            search_text = raw_text[last_pos:]
            
            print(f"  Searching for: {title} (start_text: {start_text[:30]}...) in range[{last_pos}:]")
            
            # 優先度1: start_text (本文の冒頭) で検索
            if start_text and len(start_text) > 15:
                # 最初の40文字程度で試行
                pattern = make_fuzzy_regex(start_text[:40])
                match = re.search(pattern, search_text)
                if match:
                    idx = match.start() + last_pos
                    print(f"    Found by start_text: idx={idx}")
                    
                    # start_text の直前にあるタイトルを検索して位置を補正
                    if idx > last_pos:
                        try:
                            # last_pos から idx (start_text開始位置) の間でタイトルを探す
                            pre_text = raw_text[last_pos:idx]
                            # タイトルを正規表現パターンに変換
                            title_pat = make_fuzzy_regex(title)
                            # finditer で全ての出現を探し、最後のものを採用
                            matches = list(re.finditer(title_pat, pre_text, re.IGNORECASE))
                            
                            # 短縮タイトルでの再試行
                            if not matches:
                                # ":" または "–" (en dash) または "-" (hyphen) で分割
                                parts = re.split(r'[:–-]', title)
                                if len(parts) > 1:
                                    simple_title = parts[0].strip()
                                    if len(simple_title) > 5: # 短すぎると危険
                                        print(f"    Title search failed. Retrying with simple title: '{simple_title}'")
                                        title_pat = make_fuzzy_regex(simple_title)
                                        matches = list(re.finditer(title_pat, pre_text, re.IGNORECASE))
                            
                            if matches:
                                # 最後のマッチの開始位置
                                title_start_rel = matches[-1].start()
                                # idx を更新
                                new_idx = last_pos + title_start_rel
                                print(f"    Found title before start_text. Adjusted idx: {idx} -> {new_idx}")
                                idx = new_idx
                        except Exception as e:
                            print(f"    Warning: Title search failed: {e}")
            
            # 優先度2: タイトルで検索
            if idx == -1:
                pattern = make_fuzzy_regex(title)
                match = re.search(pattern, search_text)
                if match:
                    idx = match.start() + last_pos
                    print(f"    Found by title: idx={idx}")
            
            if idx != -1:
                found_chapters.append({"idx": idx, "item": item})
                # 次の検索開始位置を更新
                last_pos = idx + 1
            else:
                print(f"    NOT FOUND: {title}")
        
        # 見つかった順序（TOC順）で切り出す
        # sortは行わない
        
        for i in range(len(found_chapters)):
            pos_info = found_chapters[i]
            start_pos = int(pos_info["idx"])
            
            if i + 1 < len(found_chapters):
                end_pos = int(found_chapters[i+1]["idx"])
            else:
                end_pos = len(raw_text)
            
            chapter_text = str(raw_text[start_pos:end_pos]).strip()
            item_data = pos_info["item"]
            
            chapters.append({
                "title": str(item_data.get("title", "Unknown")),
                "type": str(item_data.get("type", "chapter")),
                "text": chapter_text
            })
            
        return chapters

    async def generate_chapter_resume(self, chapter_text: str, progress_callback=None) -> str:
        """
        【Step 4a】章のレジュメを生成
        """
        prompt = BOOK_CHAPTER_RESUME_PROMPT.format(text=chapter_text)
        return await asyncio.to_thread(self.llm.call_api, prompt, progress_callback)

    async def structure_chapter_text(self, raw_text: str, resume_text: str, progress_callback=None) -> str:
        """
        【Step 4b】章のレジュメをヒントにして、章の生テキストを構造化する
        """
        structure_hint = Utils.extract_structure_from_resume(resume_text)
        
        # 書籍章専用の構造化プロンプトを使用する
        prompt = BOOK_CHAPTER_STRUCTURING_PROMPT.format(
            raw_text=raw_text,
            summary_hint=structure_hint
        )
        return await asyncio.to_thread(self.llm.call_api, prompt, progress_callback)

    async def extract_chapter_sections(self, chapter_text: str, progress_callback=None) -> List[Dict[str, Any]]:
        """
        章のテキストから、内部の節（Section）構成を抽出する
        """
        # トークン削減のため、章の冒頭部分を中心に抽出
        sample_text = chapter_text[:30000]
        prompt = BOOK_SECTION_TOC_EXTRACTION_PROMPT.format(text=sample_text)
        res_text = await asyncio.to_thread(self.llm.call_api, prompt, progress_callback)
        try:
            json_match = re.search(r'\[.*\]', res_text, re.DOTALL)
            if json_match:
                return cast(List[Dict[str, Any]], json.loads(json_match.group(0)))
            return cast(List[Dict[str, Any]], json.loads(res_text.strip()))
        except Exception as e:
            print(f"Error parsing Section TOC JSON: {e}")
            return []

    def split_chapter_by_sections(self, chapter_text: str, section_toc: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        抽出した節構成を元に、章テキストを分割する（章内専用ロジック）
        split_by_toc() と異なり toc_area_estimate のスキップを行わない
        """
        sections: List[Dict[str, Any]] = []
        positions: List[Dict[str, Any]] = []
        
        for item in section_toc:
            title = str(item.get("title", "")).strip()
            start_text = str(item.get("start_text", "")).strip()
            if not title:
                continue
            
            def make_fuzzy_regex(text: str):
                escaped = re.escape(text)
                return escaped.replace(r'\ ', r'\s+')
            
            idx = -1
            # start_text で全文検索（toc_area_estimate スキップなし）
            if start_text and len(start_text) > 15:
                pattern = make_fuzzy_regex(start_text[:40])
                match = re.search(pattern, chapter_text)
                if match:
                    idx = match.start()
            
            # タイトルで全文検索（toc_area_estimate スキップなし）
            if idx == -1:
                pattern = make_fuzzy_regex(title)
                match = re.search(pattern, chapter_text)
                if match:
                    idx = match.start()
            
            if idx != -1:
                positions.append({"idx": idx, "item": item})
        
        positions.sort(key=lambda x: x["idx"])
        
        for i in range(len(positions)):
            start_pos = int(positions[i]["idx"])
            end_pos = int(positions[i+1]["idx"]) if i + 1 < len(positions) else len(chapter_text)
            section_text = str(chapter_text[start_pos:end_pos]).strip()
            sections.append({
                "title": str(positions[i]["item"].get("title", "Unknown")),
                "text": section_text
            })
        
        return sections

    async def process_section(self, section_text: str, section_title: str, chapter_resume: str, glossary_text: str = "", progress_callback=None, test_mode: bool = False, test_dir: Path | None = None, chapter_idx: int = 0, section_idx: int = 0) -> str:
        """
        節単位の「構造化」と「翻訳」を連続して実行する（並列実行用）
        """
        # ガード: 入力テキストが非常に短い場合はそのまま返す
        if len(section_text.strip()) < 200:
            if progress_callback:
                progress_callback(f"[{section_title}] テキストが短すぎるため構造化・翻訳をスキップ")
            return section_text
        
        # 1. 構造化
        structured_md = await self.structure_chapter_text(section_text, chapter_resume, progress_callback=progress_callback)
        
        # テストモード: 構造化結果を保存
        if test_mode and test_dir:
            filename = f"chapter_{chapter_idx:03d}_section_{section_idx:03d}_structured.md"
            (test_dir / "06_structured" / filename).write_text(structured_md, encoding="utf-8")
        
        # ガード: 構造化結果に本文がほぼ含まれない場合は翻訳をスキップ
        non_heading_lines = [l for l in structured_md.splitlines() if l.strip() and not l.strip().startswith('#')]
        if len(non_heading_lines) < 3:
            if progress_callback:
                progress_callback(f"[{section_title}] 構造化結果が見出しのみのため翻訳をスキップ")
            return structured_md
        
        # 2. 翻訳 (章レジュメをコンテキストとして使用)
        translated = await self.translate_academic(structured_md, glossary_text, summary_context=chapter_resume, progress_callback=progress_callback)
        
        # テストモード: 翻訳結果を保存
        if test_mode and test_dir:
            filename = f"chapter_{chapter_idx:03d}_section_{section_idx:03d}_translated.md"
            (test_dir / "07_translated" / filename).write_text(translated, encoding="utf-8")
        
        return translated
