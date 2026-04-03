import fitz
import json
import re
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional
from .llm_client import call_gemini, get_default_model
from .config import print_log, PROJECT_ROOT

class PDFSplitter:
    """PDF を目次(TOC)に基づいて章ごとに分割する。"""
    
    # OCRManager と共通のキャッシュファイルを使用
    CACHE_PATH = PROJECT_ROOT / "state" / "vlm_cache.json"

    def __init__(self, api_key: str, model: Optional[str] = None):
        self.api_key = api_key
        # model_optimization.md に基づき、TOC 解析には安定性の高いデフォルトモデルを使用
        self.model = model or get_default_model("default")
        self.cache: Dict[str, Any] = {}
        self._load_cache()

    def _load_cache(self):
        """キャッシュファイルをロードする。"""
        if self.CACHE_PATH.exists():
            try:
                self.cache = json.loads(self.CACHE_PATH.read_text(encoding="utf-8"))
            except:
                self.cache = {}

    def _save_cache(self):
        """キャッシュファイルを保存する。"""
        self.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.CACHE_PATH.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2), encoding="utf-8")

    def _get_pdf_hash(self, pdf_path: str) -> str:
        """PDF ファイルの冒頭 1MB をもとにハッシュ値を計算する。"""
        with open(pdf_path, "rb") as f:
            chunk = f.read(1024 * 1024)
        return hashlib.md5(chunk).hexdigest()

    def split(self, pdf_path: str, output_dir: Path) -> List[Dict[str, Any]]:
        """
        PDF を分割し、章ごとの情報を返す。
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        doc = fitz.open(pdf_path)
        
        # 1. ローカル TOC ファイル優先
        local_toc = Path(pdf_path + ".toc.json")
        if local_toc.exists():
            print_log(f"  [Splitter] ローカルの TOC ファイルを使用します: {local_toc.name}")
            try:
                toc_data = json.loads(local_toc.read_text(encoding="utf-8"))
            except Exception as e:
                print_log(f"  [Splitter] ローカル TOC 読込エラー: {e}")
                toc_data = []
        else:
            # 2. キャッシュチェック
            pdf_hash = self._get_pdf_hash(pdf_path)
            cache_key = f"{pdf_hash}_toc"
            if cache_key in self.cache:
                print_log(f"  [Splitter] 既存の TOC キャッシュを使用します。")
                toc_data = self.cache[cache_key]
            else:
                print_log(f"  [Splitter] TOC 解析を開始: {pdf_path}")
                toc_data = self._extract_toc(doc)
                if toc_data:
                    self.cache[cache_key] = toc_data
                    self._save_cache()

        if not toc_data:
            print_log("  [Splitter] TOC の抽出に失敗しました。全編を単独章として扱います。")
            doc.close()
            return [{"title": Path(pdf_path).stem, "path": pdf_path, "role": "chapter"}]
        
        # 2. 分割実行
        results = []
        target_roles = ["chapter", "preface", "introduction", "appendix"]
        
        for i, entry in enumerate(toc_data):
            title = entry.get("title", f"Chapter_{i+1}")
            start_page = entry.get("start_page", 1) - 1
            role = entry.get("role", "chapter")
            
            # ノイズセクションの除外
            if role not in target_roles and "Chapter" not in title:
                print_log(f"    - Skipping (Back Matter/Meta): {title} (P{start_page+1})")
                continue

            # 終了ページの判定
            if i < len(toc_data) - 1:
                end_page = toc_data[i+1].get("start_page", 1) - 2
            else:
                end_page = len(doc) - 1
            
            if start_page > end_page or start_page < 0:
                continue

            # ファイル名生成 (安全な名前に変換)
            safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')
            if not safe_title: safe_title = f"chapter_{i+1}"
            out_filename = f"{i+1:02d}_{safe_title}.pdf"
            out_path = output_dir / out_filename
            
            # 分割実行
            new_doc = fitz.open()
            new_doc.insert_pdf(doc, from_page=start_page, to_page=end_page)
            new_doc.save(str(out_path))
            new_doc.close()
            
            results.append({
                "title": title,
                "path": str(out_path),
                "role": role,
                "page_range": (start_page + 1, end_page + 1)
            })
            print_log(f"  [Splitter] Extracted: {out_filename} (P{start_page+1}-{end_page+1})")

        doc.close()
        return results

    def _extract_toc(self, doc: fitz.Document) -> List[Dict[str, Any]]:
        """LLM を用いて PDF から TOC を抽出・整理する。"""
        # 最初の15ページ程度をサンプルとして使用
        text_samples = ""
        for i in range(min(15, len(doc))):
            text_samples += f"--- Page {i+1} ---\n" + doc[i].get_text() + "\n"
        
        from .llm_client import load_coreprompts
        prompts = load_coreprompts()
        prompt = prompts.get("TOC_EXTRACTION_PROMPT", "").replace("{text}", text_samples)
        
        try:
            from .llm_client import call_gemini
            response = call_gemini(
                prompt,
                api_key=self.api_key,
                model=self.model,
                response_mime_type="application/json"
            )
            data = json.loads(response)
            return data.get("toc", [])
        except Exception as e:
            print_log(f"  [Splitter] TOC 解析エラー: {e}")
            return []
