import asyncio
import re
import fitz
import json
import hashlib
import io
from pathlib import Path
from PIL import Image
from typing import List, Dict, Any, Set, Optional
from core.llm_client import (
    call_gemini_async, get_default_model, tier_manager, GeminiTier,
    model_rotator, get_free_pool_rate_limiters,
)
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

    # --- 1ページ目：メタデータから本編への移行に特化したプロンプト ---
    VLM_FRONT_MATTER_PROMPT = f"""<task>
画像からテキストを抽出し、論文の「構造」をMarkdown形式で出力してください。
**これは1ページ目の画像です。このページの内容を出力してください。**
特に、AbstractやKeywordsなどのメタデータセクションが終わり、本文（Main Narrative）が始まる境界を厳格に特定してください。
</task>

<specific_rules>
- **論文タイトル**：最も大きく、論理的にタイトルと思われる行の冒頭に `# ` を付与してください。
- **本編の開始マーク**：論文タイトル（H2相当）の直後、または AbstractやKeywordsの直後に見出し（1. Introduction等）がなく本文が始まる場合、その冒頭に必ず `# [Unlabeled Section]` と記述してください。
- 各セクション（Abstract, Keywords, [Unlabeled Section]）は必ず独立した `# ` 見出しとして分離してください。
</specific_rules>
{VLM_BASE_RULES}"""

    # --- 前ページ文脈として渡すネイティブテキスト末尾の最大長 ---
    CONTEXT_TAIL_CHARS = 500

    # --- 印刷テキストが一切無いページの合図（call_gemini_async は空応答=異常として
    #     リトライし続けるため、真の空文字列は返させず必ずこのマーカーを返させる） ---
    NO_TEXT_MARKER = "[NO_PRINTED_TEXT]"

    # --- 2ページ目以降：現ページ1枚＋前ページのテキスト文脈（I-21） ---
    # 2-up 結合をやめ、現ページ画像1枚のみを渡す。前ページはテキスト文脈として
    # 継続判定にのみ使い、決して繰り返させない。図版ページ（キャプションのみ）にも対応する。
    VLM_SINGLE_PAGE_PROMPT = f"""<task>
これは書籍または論文の**1ページ**の画像です。このページに印刷されているテキストのみを
Markdown 形式で抽出してください。
</task>

<previous_page_context>
{{prev_context}}
</previous_page_context>

<specific_rules>
- 上の <previous_page_context> は**直前ページの末尾**のテキストです。これは、このページの
  1行目が前ページの続きなのか新しい見出しなのかを判断するためだけに使ってください。
  **この文脈テキストを出力に繰り返してはいけません。**
- 前ページから文章が物理的・論理的に続いている場合、このページ冒頭に見出しタグ `# ` を
  付けてはいけません。新しい章や節がこのページ内で始まる場合のみ `# ` を付与してください。
- **図版ページの注意**: このページが写真・図表など画像主体で、キャプションだけしか印刷
  テキストが無いことがあります。その場合はキャプションのみを出力してください。印刷されて
  いる文字が一切無い場合は、空文字列ではなく必ず `{NO_TEXT_MARKER}` という文字列だけを
  出力してください。画像の内容を描写せず、印刷されている文字だけを抽出してください。
</specific_rules>
{VLM_BASE_RULES}"""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key
        self.model = model  # None ならページ単位で動的決定する（_pick_page_target 参照）
        self.semaphore = asyncio.Semaphore(self.VLM_SEMAPHORE_LIMIT)
        self._rr_index = 0  # 無料枠Liteプールのラウンドロビン用カウンタ（ページ単位でインクリメント）

        # キャッシュのロード
        self.cache: Dict[str, str] = {}
        self._load_cache()

    def _pick_page_target(self):
        """ページ1件分の (model, rate_limiter, pinned) を決定する。

        DEFAULT_MODEL_VLM はティアに関わらず常に参照される値（get_default_model("vlm") はtier
        追従しない）だが、無料枠Liteプールのラウンドロビンは FREE tier かつユーザーが model を
        明示指定していない場合のみ適用する。PAID tier では有料キーのレート上限が十分高く、
        無料枠専用ペース（1 req/4s/モデル）を適用するとむしろ有料ユーザーの速度を不必要に
        落としてしまうため（Phase4のParallelTranslator._pick_batch_targetと同じ設計思想）。
        """
        if self.model is None and tier_manager.current_tier == GeminiTier.FREE:
            pool = model_rotator.pool_models()
            if len(pool) > 1:
                limiters = get_free_pool_rate_limiters(self.api_key)
                m = pool[self._rr_index % len(pool)]
                self._rr_index += 1
                return m, limiters[m], True
        resolved = self.model or get_default_model("vlm")
        return resolved, None, False

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

    async def process_page_vlm(
        self, current_img: Image.Image, prev_context_text: str = "",
        page_idx: int = 0, session_dir: Optional[Path] = None,
    ) -> str:
        """現ページ1枚を OCR する。前ページはテキスト文脈として渡す（I-21）。

        2-up 結合はしない。1画像に対象ページしか入らないため、図版ページ等で
        隣ページを書き起こす失敗モードが構造的に起きない。
        """
        async with self.semaphore:
            # プロンプト選択: 先頭ページは front-matter、以降は単ページ＋文脈
            if page_idx == 0:
                prompt_text = self.VLM_FRONT_MATTER_PROMPT
            else:
                prompt_text = self.VLM_SINGLE_PAGE_PROMPT.replace(
                    "{prev_context}", prev_context_text or "（前ページなし）"
                )

            # キャッシュキー: 単一画像バイト列 ＋ 文脈テキスト
            # （文脈が変われば出力も変わりうるため画像だけでは不十分）
            img_byte_arr = io.BytesIO()
            current_img.save(img_byte_arr, format="PNG")
            key_src = img_byte_arr.getvalue() + prompt_text.encode("utf-8")
            img_hash = hashlib.md5(key_src).hexdigest()

            if img_hash in self.cache:
                print_log(f"  [OCRManager] Cache hit: Page {page_idx}")
                raw_result = self.cache[img_hash]
            else:
                if session_dir:
                    debug_dir = session_dir / "debug_vlm"
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    current_img.save(debug_dir / f"page_{page_idx:03d}_vlm_input.png")

                # 無料枠Liteプールが複数モデルの場合はページ単位でラウンドロビン割り当て
                page_model, page_limiter, page_pinned = self._pick_page_target()
                if page_limiter is not None:
                    async with page_limiter:
                        raw_result = await self._call_gemini_raw(
                            [current_img, prompt_text], model=page_model, model_pinned=page_pinned
                        )
                else:
                    raw_result = await self._call_gemini_raw(
                        [current_img, prompt_text], model=page_model, model_pinned=page_pinned
                    )

                if raw_result:
                    self.cache[img_hash] = raw_result
                    self._save_cache()

            # NO_TEXT_MARKER は「印刷テキストが無い」という正当な結果であり、
            # 失敗ではない（呼び出し元に空文字列として伝える）。
            if raw_result.strip() == self.NO_TEXT_MARKER:
                return ""
            return raw_result

    async def _call_gemini_raw(self, content: list, model: Optional[str] = None, model_pinned: bool = False) -> str:
        # 例外は握り潰さず呼び出し元へ伝播させる。call_gemini_async は空応答を
        # 常に異常とみなしリトライ（I-19）するため、ここに到達する時点で
        # 「空文字列」は絶対に返らない。空文字列と「本当の失敗」を区別できなく
        # なると、呼び出し元（pdf_ingester）がフォールバックすべきか判断できない。
        try:
            result = await call_gemini_async(
                prompt=content,
                model=model or self.model or get_default_model("vlm"),
                api_key=self.api_key,
                temperature=0.0,
                thinking_level="Low",
                model_pinned=model_pinned,
            )
            # コードブロックの除去
            result = re.sub(r"^```[a-zA-Z]*\n", "", result)
            result = re.sub(r"\n```$", "", result)
            return result.strip()
        except Exception as e:
            print_log(f"  [OCRManager] VLM失敗: {e}")
            raise
