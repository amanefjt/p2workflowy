"""
p2workflowy 黄金の再構築: Meta Analyzer (Phase 2)
文献の「DNA」（タイトル、著者、アブストラクト等の位置）を特定するエンジン。
"""

import json
import re
from typing import List, Dict, Any
from ..models import RawChunk
from ..llm_client import call_gemini, load_coreprompts
from ..base.exceptions import MetaExtractionError

class MetaAnalyzer:
    """1ページ目の RawChunk 群から、文献の構造的 DNA（メタデータと主要セクションの境界）を抽出する。"""

    def __init__(self):
        self.prompts = load_coreprompts()

    def analyze_dna(self, page1_chunks: List[RawChunk]) -> Dict[str, Any]:
        """
        1ページ目のチャンクからメタ情報を抽出する。
        
        Returns:
            Dict: {
                "title": str,
                "authors": List[str],
                "abstract": {"start_id": str, "end_id": str, "text_preview": str},
                "keywords": {"id": str, "text": str},
                "intro_pre_heading": {"start_id": str, "end_id": str}
            }
        """
        if not page1_chunks:
            raise MetaExtractionError("分析対象の第1ページチャンクが空です。")

        # チャンクを JSON 形式にシリアライズ (role フィールドを含む)
        chunks_data = [c.to_dict() for c in page1_chunks]
        chunks_json = json.dumps(chunks_data, ensure_ascii=False, indent=2)

        prompt_template = self.prompts.get("DNA_EXTRACTION_PROMPT")
        if not prompt_template:
            raise MetaExtractionError("coreprompts.json に DNA_EXTRACTION_PROMPT が定義されていません。")

        # ヒントの補強
        prompt_hint = "\n【論理的ヒント：VLM Labeling】\n- VLM が role='h1' を付与している場合、それはタイトルの最有力候補です。\n- role='metadata' のチャンクには、著者名や発行情報が含まれています。\n- これらを物理情報（font_size）よりも優先して解釈してください。"
        prompt = prompt_template.format(chunks_json=chunks_json) + prompt_hint

        # Gemini API を呼び出し
        try:
            # JSON モードを指定して呼び出し
            response = call_gemini(
                prompt,
                response_mime_type="application/json"
            )
            
            # JSON パース
            dna = self._parse_json_response(response)
            
            # 必須キーの検証 (最低限 title があれば成功とみなす)
            if "title" not in dna:
                raise MetaExtractionError("LLM の応答に 'title' が含まれていません。")
                
            return dna

        except Exception as e:
            if isinstance(e, MetaExtractionError):
                raise e
            raise RuntimeError(f"DNA extraction from LLM failed: {str(e)}")

    def _parse_json_response(self, response: str) -> Dict[str, Any]:
        """LLM の応答テキストから JSON オブジェクトを抽出・デコードする。"""
        # Markdown のコードフェンス除去
        clean_response = re.sub(r"```json|```", "", response).strip()
        
        # 最初と最後のブラケットを探す
        match = re.search(r"\{.*\}", clean_response, re.DOTALL)
        if not match:
            raise ValueError(f"LLM の応答に JSON オブジェクトが見つかりませんでした: {response[:100]}...")
        
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM の応答を JSON としてパースできませんでした: {str(e)}")
