import asyncio
from .constants import STRUCTURING_WITH_HINT_PROMPT, SUMMARY_PROMPT, TRANSLATION_PROMPT, MAX_TRANSLATION_CHUNK_SIZE
from .llm_processor import LLMProcessor

class PaperProcessorSkills:
    def __init__(self):
        self.llm = LLMProcessor()

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