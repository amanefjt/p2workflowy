import json
import re
import fitz
from pathlib import Path
from typing import List, Dict, Any, Optional
from core.models import RawChunk, ProcessingContext
from core.config import print_log

class TOCManager:
    """
    目次（TOC）の抽出、ページオフセット計算、およびキャッシュ管理を行うエンジン。
    """
    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key
        self.model = model

    def extract_headings_from_resume(self, resume_text: str) -> List[str]:
        """
        レジュメ・テキストからセクション見出しを逆抽出する。
        """
        headings = []
        lines = [L.strip() for L in resume_text.split("\n") if L.strip()]
        
        for L in lines:
            if re.match(r'^(Section|Heading|Chapter|PART)\s*[:\d\-]*', L, re.I):
                h = re.sub(r'^(Section|Heading|Chapter|PART)\s*[:\d\-]*\s*', '', L, flags=re.I).strip()
                if h: headings.append(h)
                continue
            if re.match(r'^[\d\.I\-\·]+\s+', L):
                h = re.sub(r'^[\d\.I\-\·]+\s+', '', L).strip()
                if h and len(h) < 100: headings.append(h)
                continue
            # 番号付き見出しの抽出（例: 1. Introduction, II. Background）
            m = re.match(r'^[\dIVX]+\.\s+(.+)$', L)
            if m:
                h = m.group(1).strip()
                if h and len(h) < 100: headings.append(h)
        return headings

    def get_toc(self, state: Any, input_path: Path, chunks: List[RawChunk], is_book: bool = True) -> Dict[str, Any]:
        """
        TOCを抽出またはキャッシュからロード。
        """
        toc_cache_path = state.session_dir / "phase3_toc.json" if state else input_path.parent / "phase3_toc.json"
        
        # 1. キャッシュの確認
        if toc_cache_path.exists():
            try:
                with open(toc_cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                if isinstance(cached.get("toc"), list) and len(cached["toc"]) > 0:
                    return cached
            except Exception:
                pass

        # 2. キャッシュがない場合、LLM で抽出
        if not self.api_key:
            return {"toc": [], "body_start_page": 1}

        if is_book:
            # 書籍モード: PDF 直接参照
            result = self._extract_toc_via_llm_pdf(input_path, state)
        else:
            # 論文モード: チャンク参照（任意だが一貫性のために提供）
            titles = self._extract_toc_via_llm_chunks(chunks)
            result = {"toc": [{"title": t, "page": -1} for t in titles], "body_start_page": 1}

        # 3. キャッシュ保存
        try:
            with open(toc_cache_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        return result

    def _extract_toc_via_llm_pdf(self, pdf_path: Path, state: Any) -> Dict[str, Any]:
        """PDF 冒頭から TOC を LLM で抽出する。"""
        from core.llm_client import call_gemini

        doc = fitz.open(str(pdf_path))
        pages_text = []
        for i in range(min(40, len(doc))):
            pages_text.append(f"[PDF Page {i+1}]\n{doc[i].get_text()}")
        text_for_llm = "\n\n".join(pages_text)

        prompt = f"""以下はPDF書籍の冒頭ページのテキストです。
1. 目次（Contents / Table of Contents）を抽出し、章タイトルとページ番号のリストを作成。
2. アラビア数字ページ1が、PDF物理何ページ目か特定。
出力形式: {{"toc": [{{"title": "...", "page": 1}}], "body_start_page": 15}}
テキスト:
{text_for_llm}"""

        response = call_gemini(
            prompt, api_key=self.api_key, model=self.model,
            response_mime_type="application/json",
            log_dir=state.logs_dir if state else None
        )

        try:
            clean = re.sub(r"```(?:json)?|```", "", response).strip()
            # LLMが説明文や追記を割り込む場合があるため、
            # 最初の JSON オブジェクト ({...}) のみを抽出する（meta_analyzer と同じパターン）
            json_match = re.search(r"\{.*\}", clean, re.DOTALL)
            if not json_match:
                raise ValueError(f"JSONオブジェクトが見つかりませんでした: {clean[:100]}...")
            data = json.loads(json_match.group(0))
            return {"toc": data.get("toc", []), "body_start_page": data.get("body_start_page", 1)}
        except Exception as e:
            print_log(f"  [TOCManager] ⚠️ TOC JSONパース失敗。空の TOC で続行します。 ({e})")
            return {"toc": [], "body_start_page": 1}

    def _extract_toc_via_llm_chunks(self, chunks: List[RawChunk]) -> List[str]:
        """VLM チャンクから TOC タイトルリストのみを抽出する。"""
        from core.llm_client import call_gemini
        
        sample_size = min(100, len(chunks))
        text_for_llm = "\n\n".join([c.text for c in chunks[:sample_size]])

        prompt = f"""以下から「章タイトル」のみを抽出しJSON形式で返してください。
{{"toc": ["Title 1", "Title 2"]}}
テキスト:
{text_for_llm}"""

        response_str = call_gemini(
            prompt, api_key=self.api_key, model=self.model,
            response_mime_type="application/json"
        )
        try:
            clean = re.sub(r"```(?:json)?|```", "", response_str).strip()
            # JSON 配列 [...] を抽出する（説明文が割り込まれていても安全）
            json_match = re.search(r"\[.*\]", clean, re.DOTALL)
            if not json_match:
                raise ValueError(f"JSON配列が見つかりませんでした: {clean[:100]}...")
            data = json.loads(json_match.group(0))
            return data
        except Exception as e:
            print_log(f"  [TOCManager] ⚠️ チャンク TOC JSONパース失敗。 ({e})")
            return []
