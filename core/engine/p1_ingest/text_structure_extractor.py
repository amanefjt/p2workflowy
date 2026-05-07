import json
import re
from typing import List
from core.models import RawChunk
from core.config import print_log, load_coreprompts


class TextStructureExtractor:
    """
    プレーンテキスト入力（Acrobat 等の抽出テキスト）から、LLM を使って
    セクション見出しを抽出し、対応する RawChunk に role を付与するエンジン。

    PDF の VLM OCR が担う「構造認識」を、テキスト向けに代替する。
    """

    def __init__(self, api_key: str, model: str = None):
        self.api_key = api_key
        self.model = model

    def extract_headings(self, chunks: List[RawChunk]) -> List[str]:
        """
        チャンクリストから学術論文のセクション見出しを LLM で抽出する。
        Returns: 見出し文字列のリスト（原文ママ）
        """
        from core.llm_client import call_gemini

        # コスト抑制: 先頭 120 チャンク（論文の本体構造はほぼここに収まる）
        sample = chunks[:120]
        text_for_llm = "\n\n".join(c.text for c in sample)

        prompts = load_coreprompts()
        prompt_template = prompts.get("TEXT_STRUCTURE_EXTRACTION_PROMPT", "")
        if not prompt_template:
            print_log("  [TextStructureExtractor] ⚠️ プロンプトが未定義。見出し抽出をスキップ。")
            return []

        prompt = prompt_template.format(text=text_for_llm)

        try:
            response = call_gemini(
                prompt,
                api_key=self.api_key,
                model=self.model,
                response_mime_type="application/json",
            )
            clean = re.sub(r"```(?:json)?|```", "", response).strip()
            arr_match = re.search(r"\[.*\]", clean, re.DOTALL)
            if not arr_match:
                raise ValueError(f"JSON 配列が見つかりませんでした: {clean[:80]}")
            headings = json.loads(arr_match.group(0))
            if isinstance(headings, list):
                result = [h for h in headings if isinstance(h, str) and h.strip()]
                print_log(f"  [TextStructureExtractor] {len(result)} 件の見出しを抽出しました。")
                return result
        except Exception as e:
            print_log(f"  [TextStructureExtractor] ⚠️ 見出し抽出失敗: {e}。空リストで続行。")
        return []

    def assign_roles(self, chunks: List[RawChunk], headings: List[str]) -> List[RawChunk]:
        """
        抽出した見出しリストを使って、一致するチャンクに role="h2" を付与する。
        見出しが見つからなかったチャンクは role="p" のまま。
        """
        if not headings:
            return chunks

        normalized_headings = [self._normalize(h) for h in headings]

        for chunk in chunks:
            norm_text = self._normalize(chunk.text)
            for norm_head in normalized_headings:
                if not norm_head:
                    continue
                # 前方一致: チャンクテキストが見出し文字列で始まる
                if norm_text.startswith(norm_head):
                    # 誤爆防止: チャンクが見出しより 3 倍以上長い場合はスキップ
                    if len(norm_text) > len(norm_head) * 3 and len(norm_head) >= 4:
                        continue
                    chunk.role = "h2"
                    break

        assigned = sum(1 for c in chunks if c.role == "h2")
        print_log(f"  [TextStructureExtractor] {assigned} チャンクに role='h2' を付与しました。")
        return chunks

    @staticmethod
    def _normalize(text: str) -> str:
        """比較用に正規化（番号接頭辞除去・小文字化・記号除去）。"""
        t = re.sub(r"^[\d\.]+\s*", "", text.strip())
        t = re.sub(r"[^\w\s]", " ", t)
        return " ".join(t.lower().split())
