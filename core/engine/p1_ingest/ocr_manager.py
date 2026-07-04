import asyncio
import re
import fitz
import json
import hashlib
import io
from pathlib import Path
from PIL import Image
from typing import List, Dict, Any, Set, Optional
from core.llm_client import call_gemini_async, get_default_model
from core.config import print_log, PROJECT_ROOT

class OCRManager:
    """
    Gemini VLM を用いた OCR 処理と PDF 品質診断を専門に扱うエンジン。
    """
    SYMBOL_DENSITY_THRESHOLD = 0.03
    FRAGMENT_RATIO_THRESHOLD = 0.08
    VLM_SEMAPHORE_LIMIT = 10
    MIN_TEXT_CHARS = 100
    
    # キャッシュファイルのパス
    CACHE_PATH = PROJECT_ROOT / "state" / "vlm_cache.json"
    
    COMMON_WORDS_WL = {
        'a', 'i', 'is', 'in', 'to', 'of', 'it', 'on', 'as', 'at', 
        'be', 'do', 'go', 'an', 'no', 'he', 'we', 'me', 'my', 'up', 
        'so', 'or', 'if', 'by', 'us', 'am', 'vs', 'ex', 'oh', 'hi',
        'et', 'al', 'pp', 'ed', 're', 'cf', 'st', 'nd', 'rd', 'th',
        'iv', 'vi', 'ix', 'xi', 'xv', 'xx', 'id'
    }

    # --- 共通の基盤プロンプト ---
    # 論理原則: 叙述の保全性原則 (04_vlm_narrative_integrity.md) に基づき、
    # 脚注による本文消失を防ぐ「本編（Main Narrative）優先」のハイブリッド構成。
    VLM_BASE_RULES = """
<rules>
1. 【階層の見極めとラベル付与】
   - **見出し（Heading）の判定ルール**:
     以下の「物理的・論理的孤立（Isolation）」の兆候がある行の冒頭に `# ` を付与してください。
     1. **垂直の「堀（Moat）」**: 前の段落（通常ピリオドで終了）や、後続の段落との間に、**通常の行間（Line Spacing）の3倍以上の広い空白**がある独立した 1 行。
     2. **右側の「断絶（Break）」**: 行がページの右端まで到達せず、途中で途切れている短い行。
     3. **視覚的特徴**: 中央配置、Italic（イタリック体のみで 1 行になっているものを含む）、ボールド、全大文字、またはローマ数字（I, II, III...）が含まれる。
     - **番号なし見出しの救済**: 上記の「孤立」条件を満たす Italic 体の行は、必ず `# ` 見出しとして抽出してください。
     - **結合抽出**: 「ローマ数字のみの行」とその直後の「タイトル行」は物理的に改行されていても、必ず1つの見出しとして `# II Title` のように結合してください。

2. 【本文（Main Narrative）の抽出と脚註の扱い】
   - **本文の優先と統合**: 
     - 画像から、著者の叙述が展開されている**ストーリー（Main Narrative）のみ**を正確に抽出してください。
     - 文中の注釈番号（¹、²等）の直後で文章を切断しないでください。論理的に文章が続いている限り、一つの連続したテキストとして本文を維持してください。
   - **ノイズ（脚註・ヘッダー）の除外**:
     - ページ上下の余白にあるヘッダー（章タイトル）、フッター（ページ番号）、および**物理的に最下部に隔離されている脚註（Footnotes）**は、叙述を妨げるノイズとして無視（Skip）してください。

3. 【出力形式】
   - 出力は抽出したテキストのみとし、挨拶や解説は一切含めないでください。
</rules>"""

    # --- 1-2ページ目：メタデータから本編への移行に特化したプロンプト ---
    VLM_FRONT_MATTER_PROMPT = f"""<task>
画像（見開き）からテキストを抽出し、論文の「構造」をMarkdown形式で出力してください。
**左側が1ページ目、右側が2ページ目です。両方の内容を出力してください。**
特に、AbstractやKeywordsなどのメタデータセクションが終わり、本文（Main Narrative）が始まる境界を厳格に特定してください。
</task>

<specific_rules>
- **論文タイトル**：最も大きく、論理的にタイトルと思われる行の冒頭に `# ` を付与してください。
- **本編の開始マーク**：論文タイトル（H2相当）の直後、または AbstractやKeywordsの直後に見出し（1. Introduction等）がなく本文が始まる場合、その冒頭に必ず `# [Unlabeled Section]` と記述してください。
- 各セクション（Abstract, Keywords, [Unlabeled Section]）は必ず独立した `# ` 見出しとして分離してください。
</specific_rules>
{VLM_BASE_RULES}"""

    # --- 3ページ目以降：継続的なコンテキスト維持に特化したプロンプト ---
    VLM_CONTINUITY_PROMPT = f"""<task>
画像（見開き）からテキストを抽出してください。
**左側が「前のページ（文脈用）」、右側が「現在のページ（抽出対象）」です。右側のページの内容のみを出力してください。**
</task>

<specific_rules>
- **連続性の判定**：左側（前ページ）から文章が物理的・論理的に続いている場合、右側（現ページ）の冒頭に見出しタグ `# ` を付けてはいけません。
- 新しい章や節が右側のページ内で始まる場合のみ、`# ` を付与してください。
</specific_rules>
{VLM_BASE_RULES}"""

    # 互換性のための古いプロンプト
    VLM_PROMPT = VLM_CONTINUITY_PROMPT

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key
        self.model = model or get_default_model("vlm")
        self.semaphore = asyncio.Semaphore(self.VLM_SEMAPHORE_LIMIT)
        
        # キャッシュのロード
        self.cache: Dict[str, str] = {}
        self._load_cache()

    def _load_cache(self):
        if self.CACHE_PATH.exists():
            try:
                self.cache = json.loads(self.CACHE_PATH.read_text(encoding="utf-8"))
            except Exception:
                self.cache = {}

    def _save_cache(self):
        self.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.CACHE_PATH.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2), encoding="utf-8")

    def _get_image_hash(self, img: Image.Image) -> str:
        """画像の MD5 ハッシュ値を計算する。"""
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return hashlib.md5(buf.getvalue()).hexdigest()

    def diagnose_pdf_quality(self, pdf_path: str) -> bool:
        """PDFのテキスト品質を診断し、破損が疑われる場合は False を返す。"""
        try:
            doc = fitz.open(pdf_path)
            for page in doc:
                text = page.get_text().strip()
                if len(text) < 100: continue
                
                # 指標A: 異常記号密度
                symbols = re.findall(r'[~|^\\_<{}\[\]]', text)
                if (len(symbols) / len(text)) > self.SYMBOL_DENSITY_THRESHOLD:
                    return False
                    
                # 指標B: 単語の断片化
                all_words = re.findall(r'\b[a-zA-ZÀ-ÿ]+\b', text)
                lowercase_words = [w for w in all_words if w and w[0].islower()]
                if not lowercase_words: continue
                fragments = [w for w in lowercase_words if len(w) <= 2 and w not in self.COMMON_WORDS_WL]
                
                if (len(fragments) / len(lowercase_words)) > self.FRAGMENT_RATIO_THRESHOLD:
                    return False
            doc.close()
            return True
        except Exception as e:
            print_log(f"  [OCRManager] 診断エラー: {e}")
            return False

    def should_use_vlm(self, page: fitz.Page, page_num: int, is_book: bool = False, heavy_ocr: bool = False, has_footnote: bool = False) -> bool:
        """ページを VLM で処理すべきかどうかを判定する。"""
        if page_num == 0: return True
        
        # スキャン画像判定
        page_area = page.rect.width * page.rect.height
        image_area = sum(((bbox := page.get_image_bbox(img)).width * bbox.height) for img in page.get_images() if page_area > 0)
        if page_area > 0 and (image_area / page_area) > 0.90: return True

        # テキスト量不足
        if len(page.get_text().strip()) < self.MIN_TEXT_CHARS: return True
        
        # 脚注検出（BookモードかつHeavy OCRの場合、VLMに振る安全策）
        if is_book and heavy_ocr and has_footnote: return True
        
        return False

    async def process_page_vlm(self, current_img: Image.Image, prev_img: Optional[Image.Image] = None, page_idx: int = 0, session_dir: Optional[Path] = None) -> str:
        """
        見開き結合（2-up）方式でページを OCR 処理する。
        """
        async with self.semaphore:
            # プロンプトの選択
            if page_idx <= 1:
                prompt_text = self.VLM_FRONT_MATTER_PROMPT
            else:
                prompt_text = self.VLM_CONTINUITY_PROMPT

            # 画像の結合
            if prev_img:
                combined_img = self._merge_images_horizontal(prev_img, current_img)
            else:
                combined_img = current_img

            # キャッシュチェック (画像ハッシュ)
            img_byte_arr = io.BytesIO()
            combined_img.save(img_byte_arr, format='PNG')
            img_hash = hashlib.md5(img_byte_arr.getvalue()).hexdigest()
            
            if img_hash in self.cache:
                print_log(f"  [OCRManager] Cache hit: Page {page_idx}")
                return self.cache[img_hash]

            # デバッグ保存
            if session_dir:
                debug_dir = session_dir / "debug_vlm"
                debug_dir.mkdir(parents=True, exist_ok=True)
                combined_img.save(debug_dir / f"page_{page_idx:03d}_vlm_input.png")

            # VLM 呼び出し
            result = await self._call_gemini_raw([combined_img, prompt_text])
            
            # キャッシュ保存
            if result:
                self.cache[img_hash] = result
                self._save_cache()
                
            return result

    def _merge_images_horizontal(self, img1: Image.Image, img2: Image.Image) -> Image.Image:
        """2枚の画像を横に結合する。"""
        w1, h1 = img1.size
        w2, h2 = img2.size
        
        # 高さを揃える（大きい方に合わせ、背景白）
        max_h = max(h1, h2)
        total_w = w1 + w2
        
        new_img = Image.new('RGB', (total_w, max_h), (255, 255, 255))
        new_img.paste(img1, (0, (max_h - h1) // 2))
        new_img.paste(img2, (w1, (max_h - h2) // 2))
        
        return new_img

    async def process_page_vlm(self, pdf_path: str, page_num: int) -> str:
        """（互換用）1ページを Gemini VLM OCR で処理する。"""
        async with self.semaphore:
            doc = fitz.open(pdf_path)
            try:
                page = doc[page_num]
                pix = page.get_pixmap(dpi=200)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            finally:
                doc.close()

            # VLM 呼び出し
            return await self._call_gemini_raw([img, self.VLM_PROMPT])

    async def _call_gemini_raw(self, content: list) -> str:
        try:
            result = await call_gemini_async(
                prompt=content,
                model=self.model,
                api_key=self.api_key,
                temperature=0.0,
                thinking_level="Low"
            )
            # コードブロックの除去
            result = re.sub(r"^```[a-zA-Z]*\n", "", result)
            result = re.sub(r"\n```$", "", result)
            return result.strip()
        except Exception as e:
            print_log(f"  [OCRManager] VLM失敗: {e}")
            return ""
