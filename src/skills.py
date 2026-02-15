import asyncio
import json
import re
from .constants import (
    STRUCTURING_WITH_HINT_PROMPT, SUMMARY_PROMPT, TRANSLATION_PROMPT, 
    MAX_TRANSLATION_CHUNK_SIZE,
    BOOK_STRUCTURE_PROMPT, BOOK_SUMMARY_PROMPT, BOOK_CHAPTER_SUMMARY_PROMPT, 
    BOOK_STRUCTURING_PROMPT, BOOK_TRANSLATION_PROMPT, BOOK_TRANSLATION_PROMPT_SIMPLE,
    PAPER_TRANSLATION_PROMPT_SIMPLE
)
from .llm_processor import LLMProcessor
from .utils import Utils

class PaperProcessorSkills:
    def __init__(self):
        self.llm = LLMProcessor()

    async def summarize_paper(self, raw_text: str, progress_callback=None) -> str:
        """
        SUMMARY_PROMPT を使って論文の要約を生成する
        """
        prompt = SUMMARY_PROMPT.replace("{text}", raw_text)
        return await asyncio.to_thread(self.llm.call_api, prompt, progress_callback)

    # --- Book Mode Skills ---

    async def analyze_book_structure(self, full_text: str, progress_callback=None) -> dict:
        """
        書籍全体を分析し、目次(TOC)をJSON形式で抽出する（要約は含まない）
        Returns:
            dict: {"chapters": list[dict]}
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
                return {"chapters": data}
            return data
            
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON from LLM: {e}")
            print(f"Raw Response: {response_text}")
            raise ValueError(f"Failed to parse TOC JSON: {e}")

    async def generate_book_summary(self, full_text: str, progress_callback=None) -> str:
        """
        BOOK_SUMMARY_PROMPT を使って書籍全体の要約を生成する
        """
        prompt = BOOK_SUMMARY_PROMPT.replace("{text}", full_text)
        return await asyncio.to_thread(self.llm.call_api, prompt, progress_callback)

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        """改行・タブ・複数スペースを単一スペースに正規化する"""
        return re.sub(r'\s+', ' ', text).strip()
    
    def _clean_running_heads(self, text: str) -> str:
        """
        簡易的なヘッダー/フッター削除処理
        数値のみの行や、短すぎる行を改行に置換して、検索の邪魔にならないようにする
        （完全な削除は難しいが、検索性を高めるための前処理）
        """
        # 1. 数字のみの行（ページ番号）を削除
        text = re.sub(r'^\s*\d+\s*$', '', text, flags=re.MULTILINE)
        return text

    def _find_anchor_position(self, full_text: str, start_snippet: str, title: str, search_from: int) -> tuple[int, str]:
        """
        アンカーテキストの位置を多段階で検索する（Anchor Text Method）。
        
        Returns:
            tuple[int, str]: (found_position, strategy_name)
        """
        # 前処理: 検索対象テキスト
        target_text = full_text[search_from:]
        normalized_target = self._normalize_whitespace(target_text)
        
        # 前処理: キーワード
        normalized_snippet = self._normalize_whitespace(start_snippet)
        normalized_title = self._normalize_whitespace(title)
        
        # --- Plan A: Title + Snippet (Most Robust) ---
        # タイトルと書き出しが近接している場所を探す
        # 正規化された空間で検索する
        
        # タイトルとスニペットの間に、多少のゴミ（ページ番号やサブタイトル）が入ることを許容する正規表現
        # しかし、正規化済みテキストでは改行がないため、単に "Title ... Snippet" のパターンを探す
        
        # タイトルを検索（ループして、書き出しが続くものを探す）
        keyword_search_pos = 0
        first_title_pos = -1 # Plan C用のバックアップ
        
        while True:
            title_pos = normalized_target.find(normalized_title, keyword_search_pos)
            if title_pos == -1:
                break
            
            if first_title_pos == -1:
                first_title_pos = title_pos
                
            # タイトルの後ろ 200文字以内 にスニペットがあるか？
            search_window = 200
            snippet_search_area = normalized_target[title_pos + len(normalized_title) : title_pos + len(normalized_title) + search_window]
            
            # スニペットの先頭15文字程度で照合してみる
            snippet_head = " ".join(normalized_snippet.split()[:5]) 
            if snippet_head in snippet_search_area:
                # Plan A 成功: タイトルの位置を特定
                
                # 正規化テキスト上でマッチしたのだから、元テキスト上でも
                # "Title" (fuzzy space) ... "SnippetStart" の並びがあるはず。
                
                # Regex pattern: Title + (any chars up to window) + SnippetStart
                # Escape title and snippet words
                title_words = normalized_title.split()
                snippet_words = normalized_snippet.split()[:5]
                
                if not title_words or not snippet_words:
                    break
                    
                p_title = r'\s*'.join(re.escape(w) for w in title_words)
                p_snippet = r'\s*'.join(re.escape(w) for w in snippet_words)
                
                # タイトルとスニペットの間は、任意の文字（改行含む）が0〜500文字
                pattern = f"({p_title}).{{0,500}}?({p_snippet})"
                
                # search_from以降で検索
                match = re.search(pattern, full_text[search_from:], re.DOTALL | re.IGNORECASE)
                if match:
                    return search_from + match.start(), "Plan A (Title + Snippet)"
                
                break

            # 次の候補へ
            keyword_search_pos = title_pos + 1
            
        # Plan A Re-Revised: Regex Search Directly
        # 正規化テキストでのループはマッピングが困難。
        # 代わりに、元テキストに対して直接「タイトル...(200文字以内)...書き出し」を探す正規表現を試みる。
        # ただし、タイトルが短すぎると誤爆する。
        
        # 正規化タイトルとスニペットから単語を抽出
        t_words = normalized_title.split()
        s_words = normalized_snippet.split()[:5]
        
        if t_words and s_words:
            p_t = r'\s+'.join(re.escape(w) for w in t_words)
            p_s = r'\s+'.join(re.escape(w) for w in s_words)
            
            # Context-aware Regex
            # Title followed by Snippet within reasonable distance
            regex_pattern = f"({p_t})(?:(?!{p_t}).){{0,500}}?{p_s}"
            
            match = re.search(regex_pattern, full_text[search_from:], re.DOTALL | re.IGNORECASE)
            if match:
                 return search_from + match.start(), "Plan A (Title + Snippet)"

        # --- Plan B: Snippet Only (Anchor Text) ---
        # タイトルが見つからない、またはタイトル周辺にスニペットがない場合
        # 本文の書き出し（スニペット）だけで検索する。これがユニークであれば強力。
        
        # 完全一致検索
        snippet_pos = normalized_target.find(normalized_snippet)
        if snippet_pos != -1:
             # スニペットが見つかった。
             # ただし、これだと「章の途中」から始まってしまうので、
             # その直前の「改行」や「タイトルらしきもの」まで遡りたいが、
             # 安全策として「スニペットの開始位置」を章の開始とする（タイトルが欠落するが、混入よりマシ）
             # 理想: スニペットの開始位置 - タイトル長 - α
             
             original_pos = self._map_normalized_pos_to_original(full_text, search_from, normalized_snippet)
             if original_pos != -1:
                 # 少しだけ前に戻ってみる（タイトルを拾えるかも）
                 # visual adjustment
                 return original_pos, "Plan B (Snippet Only)"
        
        # スニペットの部分一致（先頭10単語）
        snippet_head_words = normalized_snippet.split()[:10]
        if len(snippet_head_words) >= 5:
            partial_snippet = " ".join(snippet_head_words)
            partial_pos = normalized_target.find(partial_snippet)
            if partial_pos != -1:
                original_pos = self._map_normalized_pos_to_original(full_text, search_from, partial_snippet)
                if original_pos != -1:
                    return original_pos, "Plan B' (Partial Snippet)"

        # --- Plan C: Title Only (Fallback) ---
        # 最終手段。タイトルだけで探す。
        if first_title_pos != -1:
             # normalized空間での first_title_pos に対応する元テキスト位置を探す
             # ここは簡易的に _map で再検索させてもいいが、正規化テキストのフラグメントとして渡す
             
             # normalized_title そのものを使ってマップする
             # ただしこれは「最初の出現」を返してしまう。
             # first_title_pos はまさに「最初の出現」なので問題ない。
             
             original_pos = self._map_normalized_pos_to_original(full_text, search_from, normalized_title)
             if original_pos != -1:
                 return original_pos, "Plan C (Title Only)"

        return -1, "Failed"

    def _map_normalized_pos_to_original(self, full_text: str, search_from: int, normalized_fragment: str) -> int:
        """
        正規化されたフラグメントの位置を、元テキスト内の位置にマッピングする。
        元テキストを1文字ずつスキャンし、正規化フラグメントの先頭語群が始まる位置を見つける。
        """
        # 先頭の数語を取得して元テキストから検索
        first_words = normalized_fragment.split()[:3]
        if not first_words:
            return -1
        
        # 元テキスト内で先頭語を正規表現で検索（間の空白を柔軟に）
        # 特殊文字をエスケープする
        pattern = r'\s+'.join(re.escape(w) for w in first_words)
        match = re.search(pattern, full_text[search_from:])
        if match:
            return search_from + match.start()
        return -1

    def split_by_anchors(self, full_text: str, structure_data: dict) -> list[dict]:
        """
        アンカーテキストに基づいて全文を章ごとに分割する（Anchor Text Method実装版）
        Returns:
            list[dict]: [{"title": str, "text": str}, ...]
        """
        chapters_toc = structure_data.get("chapters", [])
        if not chapters_toc:
            return [{"title": "Full Text", "text": full_text}]

        indices = []
        valid_chapters = []

        # ノイズ除去（検索精度向上のため）
        # 元のテキストは保持し、検索用のテキストを作るアプローチもあるが、
        # ここではインデックスズレを防ぐため、元のテキストは変更せず、
        # 検索メソッド内で正規化を行う方針とする。
        # _clean_running_heads はここでの適用は控える（インデックスが変わるため）。
        
        current_search_pos = 0
        
        print(f"\n  --- アンカー照合開始 (全{len(chapters_toc)}章) ---")
        print(f"  入力テキスト長: {len(full_text)} 文字")
        
        for chapter in chapters_toc:
            title = chapter.get("title", "Unknown Chapter")
            start_snippet = (chapter.get("start_snippet") or chapter.get("anchor") or "").strip()
            
            # アンカー情報がまったくない場合
            if not start_snippet and not title:
                print(f"  ✗ '{title}' — 情報不足（スキップ）")
                continue
            
            pos, strategy = self._find_anchor_position(full_text, start_snippet, title, current_search_pos)
            
            if pos != -1:
                print(f"  ✓ '{title}' — {strategy} (pos={pos})")
                indices.append(pos)
                valid_chapters.append(chapter)
                # 次の検索は、見つかった場所の少し後から
                current_search_pos = pos + 1
            else:
                 print(f"  ✗ '{title}' — 発見できず")
                 # 見つからなくても、次の章を探し続ける（順番が前後している可能性や、この章だけOCR失敗の可能性）
                 # current_search_pos は更新しない
        
        print(f"  --- 照合結果: {len(valid_chapters)}/{len(chapters_toc)} 章が一致 ---\n")
            
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
            
        # もし最初の章が 0 以外の位置から始まっている場合、
        # それ以前のテキスト（前書きや目次など）をどうするか？
        # 現状は無視される（chunks_infoに含まれない）。これは意図通り（目次除去）。
            
        return chunks_info

    async def summarize_chapter(self, chapter_text: str, progress_callback=None) -> str:
        """
        BOOK_CHAPTER_SUMMARY_PROMPT を使って章ごとの要約を生成する
        """
        prompt = BOOK_CHAPTER_SUMMARY_PROMPT.replace("{text}", chapter_text)
        return await asyncio.to_thread(self.llm.call_api, prompt, progress_callback)

    async def _structure_text_chunked(self, raw_text: str, prompt_creator, chunk_size: int = 20000, progress_callback=None) -> str:
        """
        共通のチャンク分割・並列構造化ロジック
        """
        chunks = self._split_text_by_length(raw_text, chunk_size)
        total_chunks = len(chunks)

        if progress_callback:
            progress_callback(f"テキストを {total_chunks} チャンクに分割して並列構造化中...")

        async def process_chunk(index, chunk):
            prompt = prompt_creator(chunk, index)
            try:
                # LLM呼び出し
                response = await asyncio.to_thread(self.llm.call_api, prompt, None)
                # 個別のチャンクもサニタイズしておく
                return Utils.sanitize_structured_output(response)
            except Exception as e:
                return f"\n[構造化エラー (Chunk {index+1}): {str(e)}]\n"

        # 並列実行
        tasks = [process_chunk(i, chunk) for i, chunk in enumerate(chunks)]
        results = await asyncio.gather(*tasks)

        # 結果を結合
        combined_text = "\n\n".join(results)
        
        # 全体に対しても再度サニタイズ（重複ヘッダーの除去など）
        return Utils.sanitize_structured_output(combined_text)

    async def structure_text_with_hint(self, raw_text: str, summary_text: str, title: str = "", progress_callback=None) -> str:
        """
        【Phase 2】要約をヒントにして、生テキストを構造化する (Paper Mode)
        """
        # OCRノイズ（Running Heads）をクリーンアップ
        if title:
            raw_text = Utils.clean_header_noise(raw_text, title)
        
        def create_prompt(chunk, index):
            # Paper Mode Prompt
            return STRUCTURING_WITH_HINT_PROMPT.format(
                raw_text=chunk,
                summary_hint=summary_text
            )

        return await self._structure_text_chunked(raw_text, create_prompt, chunk_size=20000, progress_callback=progress_callback)

    async def structure_chapter(self, overall_summary: str, chapter_text: str, chapter_summary: str = "", chapter_title: str = "", progress_callback=None) -> str:
        """
        【Phase 3】特定の章の英語構造を整理する (Book Mode)
        要約から節タイトルを抽出する複雑なロジックを廃止し、Paper Modeと同じ堅牢なチャンク処理を採用。
        """
        # OCRノイズ（Running Heads）をクリーンアップ
        if chapter_title:
            chapter_text = Utils.clean_header_noise(chapter_text, chapter_title)

        def create_prompt(chunk, index):
            # Book Mode Prompt (Strict Rules)
            # prompts.json の BOOK_STRUCTURING_PROMPT に合わせた引数を渡す
            # chapter_summary は使用しない（ハルシネーション防止）
            return BOOK_STRUCTURING_PROMPT.format(
                title=chapter_title,
                raw_text=chunk
            )
        
        return await self._structure_text_chunked(chapter_text, create_prompt, chunk_size=20000, progress_callback=progress_callback)

    async def translate_chapter(self, overall_summary: str, chapter_summary: str, chapter_text: str, glossary_text: str = "", is_simple: bool = False, progress_callback=None) -> str:
        """
        特定の章を、全体文脈と章の要約を踏まえて翻訳する
        """
        # 単純な文字数分割ではなく、構造化されたMarkdownを見出しごとに分割して翻訳する
        # これにより、文脈の分断や重複（AIによる勝手な見出し補完）を防ぐ
        chunks = self._split_markdown_by_headers(chapter_text)
        
        # 並列数を制限するためのセマフォ (章ごとの翻訳リクエストが爆発しないように)
        sem = asyncio.Semaphore(5)

        async def _translate_chunk(chunk):
            if not chunk.strip():
                return ""
            
            async with sem:
                # replaceチェーンではなく、明示的にキーを指定して置換する
                # (formatメソッドだと中括弧のエスケープが必要になるため、replaceの方が安全な場合もあるが、
                # ここではtranslate_academicに合わせて明示的なキー置換を行う)
                base_prompt = BOOK_TRANSLATION_PROMPT_SIMPLE if is_simple else BOOK_TRANSLATION_PROMPT
                prompt = base_prompt.replace("{overall_summary}", overall_summary) \
                                              .replace("{chapter_summary}", chapter_summary) \
                                              .replace("{glossary_content}", glossary_text) \
                                              .replace("{chunk_text}", chunk)
                
                try:
                    response = await asyncio.to_thread(self.llm.call_api, prompt, None)
                    return Utils.sanitize_translated_output(response)
                except Exception as e:
                    print(f"Error in translating chunk: {e}")
                    raise e

        tasks = [_translate_chunk(chunk) for chunk in chunks]
        
        # エラーが起きても止まらないように return_exceptions=True にする
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        final_results = []
        for res in results:
            if isinstance(res, Exception):
                final_results.append(f"[Chapter翻訳エラー: {str(res)}]")
            else:
                if not res: continue
                # H1タグが含まれているとWorkFlowyで章が分割されてしまうため、強制的にH2に置換する
                # (例: "# 導入" -> "## 導入")
                sanitized_res = re.sub(r'^# ', '## ', res, flags=re.MULTILINE)
                final_results.append(sanitized_res)
                
        return "\n\n".join(final_results)

    def _extract_section_titles(self, summary_text: str) -> list[str]:
        """
        要約テキストから節タイトル（英語）を抽出する。
        パターン: 「節の要約：...（English Title）」
        """
        titles = []
        # 全角括弧、半角括弧、または単にコロン後の英語タイトルを抽出
        # より柔軟なパターンに変更
        patterns = [
            r'節の要約[：:].*?[（\(](.+?)[）\)]', # 括弧あり
            r'節の要約[：:]\s*([a-zA-Z0-9\s\'\-\:\&\.]+?)(?:\s|$)' # 括弧なし英語のみ
        ]
        
        for p in patterns:
            matches = re.findall(p, summary_text)
            for title in matches:
                title = title.strip()
                # 短すぎるものや既に含まれているものを除外
                if title and len(title) > 3 and title not in titles:
                    titles.append(title)
        
        if titles:
            print(f"  要約から {len(titles)} 個の節タイトルを抽出: {titles}")
        else:
            # フォールバック: インデントされた見出し（Workflowy形式）からの抽出を試みる
            lines = summary_text.split('\n')
            for line in lines:
                # 「- TITLE」形式で日本語名が含まれない行を探す
                match = re.match(r'^\s*-\s+([a-zA-Z0-9\s\'\-\:\&\.]{5,})$', line)
                if match:
                    titles.append(match.group(1).strip())
            
            if titles:
                print(f"  要約の見出し構造から {len(titles)} 個の節タイトルを抽出: {titles}")
            else:
                print("  要約から節タイトルを抽出できませんでした（フォールバック使用）")
        return titles

    def _split_text_by_sections(self, raw_text: str, section_titles: list[str]) -> list[str]:
        """
        節タイトルの位置に基づいてテキストをセマンティックに分割する。
        """
        if not section_titles:
            print("  節タイトルなし → 文字数ベース分割にフォールバック")
            return self._split_text_by_length(raw_text)
        
        # 各タイトルの位置を検索（順序を維持し、ランニングヘッドによる誤検出を防ぐ）
        positions = []
        last_pos = 0
        found_titles = []

        for title in section_titles:
            # 既に検索した位置より後を探す
            search_area = raw_text[last_pos:]
            
            # 完全一致（大文字小文字無視を優先）
            pos_in_area = search_area.lower().find(title.lower())
            
            if pos_in_area != -1:
                abs_pos = last_pos + pos_in_area
                positions.append(abs_pos)
                found_titles.append(title)
                print(f"  ✓ 節タイトル '{title}' を位置 {abs_pos} で発見")
                # 次の検索開始位置を更新（最低でもタイトルの長さ分は進める）
                last_pos = abs_pos + len(title)
            else:
                print(f"  ✗ 節タイトル '{title}' が元テキスト（未検索領域）に見つかりません")
        
        if not positions:
            print("  節タイトルが一つも見つかりません → 文字数ベース分割にフォールバック")
            return self._split_text_by_length(raw_text)
        
        # 分割
        chunks = []
        # 冒頭部分
        if positions[0] > 0:
            intro = raw_text[:positions[0]].strip()
            if intro:
                chunks.append(intro)
        
        for i in range(len(positions)):
            start = positions[i]
            end = positions[i + 1] if i + 1 < len(positions) else len(raw_text)
            chunk = raw_text[start:end].strip()
            if chunk:
                chunks.append(chunk)
        
        print(f"  → {len(chunks)} 個のチャンクに分割完了")
        return chunks

    async def translate_academic(self, clean_markdown: str, glossary_text: str = "", summary_context: str = "", is_simple: bool = False, progress_callback=None) -> str:
        """
        【Phase 3】Markdownをセクションごとに分割し、並列翻訳する
        """
        # 1. セクション分割
        chunks = self._split_markdown_by_headers(clean_markdown)
        
        # 2. タスク作成
        tasks = []
        # プロンプトの切り替え
        base_prompt = PAPER_TRANSLATION_PROMPT_SIMPLE if is_simple else TRANSLATION_PROMPT

        for chunk in chunks:
            if not chunk.strip():
                continue
            
            try:
                # プロンプトの変数を埋め込む
                # formatメソッドを使用 (PAPER_TRANSLATION_PROMPT_SIMPLEもキーは同じに設計)
                prompt = base_prompt.format(
                    summary_content=summary_context,  # 要約
                    chunk_text=chunk,                 # 翻訳対象のセクション
                    glossary_content=glossary_text    # 辞書
                )
                
                async def _translate_single_chunk(p):
                    res = await asyncio.to_thread(self.llm.call_api, p, None)
                    return Utils.sanitize_translated_output(res)

                # 並列実行時は progress_callback を渡すと表示が乱れるため None にする
                tasks.append(_translate_single_chunk(prompt))
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
        """
        テキストを指定された最大長で分割する。
        可能な限り段落区切り（\n\n）や行区切り（\n）で分割し、文章の途中切断を防ぐ。
        """
        if len(text) <= max_length:
            return [text]
            
        chunks = []
        current_block = ""
        
        # 段落（\n\n）で分割
        # split('\n\n') だと空文字が含まれる可能性があるので注意
        paragraphs = text.split('\n\n')
        
        for para in paragraphs:
            if not para:
                continue
                
            # この段落を追加できるか？
            # [現在のブロック] + [\n\n] + [この段落]
            # current_block が空なら \n\n は不要
            needed = len(current_block) + 2 + len(para) if current_block else len(para)
            
            if needed > max_length:
                # 入らない場合
                if current_block:
                    # 先に現在のブロックを確定してチャンク化
                    chunks.append(current_block)
                    current_block = ""
                
                # 段落単体がデカすぎる場合 -> 行で分割して追加
                if len(para) > max_length:
                    lines = para.split('\n')
                    for line in lines:
                        needed_line = len(current_block) + 1 + len(line) if current_block else len(line)
                        
                        if needed_line > max_length:
                            if current_block:
                                chunks.append(current_block)
                            current_block = line
                        else:
                            if current_block:
                                current_block += "\n" + line
                            else:
                                current_block = line
                else:
                    # 段落が max_length に収まるなら、それを新しい current_block の先頭にする
                    current_block = para
            else:
                # そのまま追加
                if current_block:
                    current_block += "\n\n" + para
                else:
                    current_block = para
                    
        if current_block:
            chunks.append(current_block)
            
        return chunks