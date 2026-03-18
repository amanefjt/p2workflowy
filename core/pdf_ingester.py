"""
p2workflowy V2: Phase 0 (PDF Ingestion) - ハイブリッド方式
PyMuPDF による高速テキスト抽出をデフォルトとし、
特定条件のページのみ Gemini VLM OCR にフォールバックする。
"""

import asyncio
import json
import re
import statistics
from pathlib import Path
from typing import Any, List, Set

import fitz
from PIL import Image

from .llm_client import call_gemini_async, get_default_model
from .config import print_log

# ヘッダー/フッター判定用の閾値
MIN_DUPLICATE_PAGES = 3    # 最低出現ページ数
MIN_DUPLICATE_RATIO = 0.05 # 全ページの5%以上

# ===== 設定値（調整しやすいよう外出し） =====
FOOTNOTE_FONT_RATIO = 0.60      # 本文中央値の60%以下 → 脚注とみなす
MIN_TEXT_CHARS = 100             # これ未満の テキスト量 → VLMフォールバック
HEADER_MARGIN_RATIO = 0.08      # ページ上部8%をノイズ除外
FOOTER_MARGIN_RATIO = 0.10      # ページ下部10%をノイズ除外
FOOTNOTE_AREA_RATIO = 0.80      # ページ下部20%に脚注検出
VLM_SEMAPHORE_LIMIT = 2         # VLM同時実行数の上限

# ===== PDF診断 (Pre-flight Check) 用定数 =====
SYMBOL_DENSITY_THRESHOLD = 0.03  # 3%
FRAGMENT_RATIO_THRESHOLD = 0.08  # 8%

# 正常な英単語ホワイトリスト (1-2文字)
# ユーザー要望 & 学術・目次ページ対応
COMMON_WORDS_WL = {
    'a', 'i', 'is', 'in', 'to', 'of', 'it', 'on', 'as', 'at', 
    'be', 'do', 'go', 'an', 'no', 'he', 'we', 'me', 'my', 'up', 
    'so', 'or', 'if', 'by', 'us', 'am', 'vs', 'ex', 'oh', 'hi',
    'et', 'al', 'pp', 'ed', 're', 'cf', 'st', 'nd', 'rd', 'th',
    'iv', 'vi', 'ix', 'xi', 'xv', 'xx', 'id'
}

VLM_PROMPT = """<task>
画像からテキストを抽出し、元のドキュメントの「視覚的な階層構造」を正確に反映したMarkdown形式で出力してください。
</task>

<rules>
1. 【階層の視覚的判断】
   - フォントサイズが最も大きく、中央揃えなどで強調されているものを「章 (Chapter)」と判断し、`# ` を付与してください。
   - それより少し小さく、太字などで強調されているものを「節 (Section)」と判断し、`## ` を付与してください。
   - 通常のフォントサイズのものは「本文」として、記号をつけずに出力してください。
2. 【ノイズの完全無視】
   ページの上部や下部にある「柱（章のタイトルなど）」や「ページ番号」は、本文の構造ではないため、**絶対に抽出しないでください**。
3. 【白紙・画像不在時の対応】
   画像にテキストが含まれていない、または白紙の場合は、説明や謝絶（「画像にはテキストがありません」等）を一切出力せず、ただ `[BLANK]` とのみ出力してください。
4. 【出力形式の厳守】
   出力は抽出したテキストのみとし、AIとしての挨拶や解説、マークダウンのコードブロック(```)での囲みは不要です。
</rules>"""

VLM_PROMPT_SINGLE = VLM_PROMPT


# ===== PDF診断ロジック =====

def diagnose_pdf_quality(pdf_path: str) -> bool:
    """PDFのテキスト品質を診断し、破損が疑われる場合は False を返す。
    
    判定基準:
    1. 異常記号密度 (Symbol Density): ~ | ^ \ _ < { } [ ] 等の記号が 3% 以上
    2. 単語断片化 (Fragmentation): 大文字始まりを除外した小文字単語のうち、
       ホワイトリストに含まれない 1-2 文字の単語が 5% 以上
    """
    try:
        doc = fitz.open(pdf_path)
        for page in doc:
            # get_text() でプレーンテキストを取得
            text = page.get_text().strip()
            if len(text) < 100:  # 情報不足のページは判定不能のためスキップ
                continue
                
            # 指標A: 異常記号密度 (Symbol Density)
            # 破損時に頻出する記号を正規表現で抽出
            symbols = re.findall(r'[~|^\\_<{}\[\]]', text)
            if (len(symbols) / len(text)) > SYMBOL_DENSITY_THRESHOLD:
                print_log(f"  [Diagnostic] 異常記号密度を検知 (Page {page.number+1}: {len(symbols)/len(text):.1%})")
                doc.close()
                return False
                
            # 指標B: 単語の断片化 (Fragmentation)
            # 全ての単語を抽出し、大文字始まり（固有名詞の可能性）を除外した小文字単語を母集団とする
            # [a-zA-ZÀ-ÿ] により、アクセント付き文字（chuño 等）を単語として成立させる
            all_words = re.findall(r'\b[a-zA-ZÀ-ÿ]+\b', text)
            lowercase_words = [w for w in all_words if w and w[0].islower()]
            if not lowercase_words:
                continue
                
            # 母集団のうち、1-2文字で、かつ一般的な単語リストに含まれないものを「破片」とみなす
            fragments = [w for w in lowercase_words if len(w) <= 2 and w not in COMMON_WORDS_WL]
            
            if (len(fragments) / len(lowercase_words)) > FRAGMENT_RATIO_THRESHOLD:
                print_log(f"  [Diagnostic] 単語の断片化を検知 (Page {page.number+1}: {len(fragments)/len(lowercase_words):.1%})")
                doc.close()
                return False
                
        doc.close()
        return True # 全ページ合格
    except Exception as e:
        print_log(f"  [Diagnostic] 診断中にエラーが発生しました: {e}")
        return False # 安全のため、異常の可能性があるとして Route C を推奨する


# ===== ページルーティング =====

def should_use_vlm(page: fitz.Page, page_num: int = 0, is_book: bool = False, heavy_ocr: bool = False, has_footnote: bool = False) -> bool:
    """ページを VLM で処理すべきかどうかを判定する。

    以下のいずれか1つでも満たすと True:
    1. 1ページ目（page_num == 0）
    2. テキスト量が MIN_TEXT_CHARS 未満
    3. 書籍モードかつ高度OCRが有効で、脚注が検出された場合（安全策）
    """
    # 条件1: 1ページ目は無条件でVLM
    if page_num == 0:
        return True

    # 条件2: スキャン画像判定 (Trap Evasion)
    # ページ面積の 90% 以上を画像が占める場合、スキャンPDFとみなす
    try:
        page_area = page.rect.width * page.rect.height
        image_area = 0
        for img_info in page.get_images():
            # img_info[7] is the name like 'Im0'
            try:
                bbox = page.get_image_bbox(img_info)
                image_area += (bbox.width * bbox.height)
            except Exception:
                continue
        
        if page_area > 0 and (image_area / page_area) > 0.90:
            return True
    except Exception:
        pass

    # 条件3: テキスト量不足
    try:
        raw_text = page.get_text()
        if len(raw_text.strip()) < MIN_TEXT_CHARS:
            return True
    except (AttributeError, TypeError):
        # mockオブジェクトなどで get_text が期待通り動かない場合のフォールバック
        pass

    # 条件3: 脚注の検出（BookモードかつHeavy OCRの場合のみ VLM に振る）
    if is_book and heavy_ocr and has_footnote:
        return True

    return False


def _has_footnotes(page: fitz.Page) -> bool:
    """ページ下部に脚注が存在するかフォントサイズで判定する。"""
    page_height = page.rect.height

    # 全テキストspanのフォントサイズを収集
    text_dict = page.get_text("dict")
    font_sizes: List[float] = []
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:  # テキストブロックのみ
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if span.get("text", "").strip():
                    font_sizes.append(span["size"])

    # ガード: フォントサイズが取れなければ脚注なしとみなす
    if not font_sizes:
        return False

    # 本文サイズの特定（中央値）
    median_size = statistics.median(font_sizes)
    # 本文の 60% 以下を脚注サイズとみなす
    footnote_threshold = median_size * FOOTNOTE_FONT_RATIO

    # ページ下部20%の領域に小さいフォントがあるか確認
    footnote_area_top = page_height * FOOTNOTE_AREA_RATIO
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        # block[1] は y0
        if block["bbox"][1] < footnote_area_top:
            continue
            
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if not span.get("text", "").strip():
                    continue
                if span["size"] <= footnote_threshold:
                    return True

    return False


# 短行判定の閾値: ブロック幅の85%未満なら段落候補
SHORT_LINE_RATIO = 0.85

def _detect_repeating_elements(doc: fitz.Document) -> Set[str]:
    """Pass 1: 全ページをスキャンし、反復するヘッダー・フッター（ノイズ）を特定する。"""
    total_pages = len(doc)
    if total_pages < 2:
        return set()

    print_log(f"  [Pass 1] Global Scan: 重複要素の検知を開始...")
    candidates = []
    
    # 各ページの最上部/最下部のテキストを収集
    for i in range(total_pages):
        page = doc[i]
        rect = page.rect
        # 上部 10%, 下部 10% をサンプリング
        h_limit = rect.height * 0.10
        f_limit = rect.height * 0.90
        
        blocks = page.get_text("blocks")
        for b in blocks:
            # y0, y1 で判定
            if b[1] < h_limit or b[3] > f_limit:
                text = b[4].strip()
                normalized = re.sub(r'\d+', '', text).strip()
                if len(normalized) > 3: # 短すぎるものは除外
                    candidates.append(normalized)

    # 出現頻度をカウント
    ignored_patterns = set()
    from collections import Counter
    counts = Counter(candidates)
    
    threshold_count = max(MIN_DUPLICATE_PAGES, int(total_pages * MIN_DUPLICATE_RATIO))
    
    for text, count in counts.items():
        if count >= threshold_count:
            ignored_patterns.add(text)
            
    print_log(f"  [Pass 1] 検知完了: {len(ignored_patterns)} 個のパターンを除外リストに追加しました。")
    return ignored_patterns


def _should_join_page_boundary(text1: str, text2: str) -> str:
    """ページ跨ぎの文境界をどう結合すべきかを判定する（純粋関数）。

    Returns:
        "newline": 別の段落として \\n\\n で繋ぐ
        "space": 同じ段落としてスペースで繋ぐ
        "merge_hyphen": ハイフンを除去して結合する
    """
    t1 = text1.strip()
    t2 = text2.strip()
    if not t1 or not t2:
        return "newline"

    # 文末記号
    SENTENCE_END_RE = r'[.!?\)\]"\'”。]\d*$'
    
    # 1. ハイフネーション結合 (inter-\nnational)
    if t1.endswith("-"):
        # 次の開始が小文字なら単語の分断
        if t2[0].islower():
            return "merge_hyphen"
        # 大文字ならハイフン付きの固有名詞などの可能性があるが、
        # 基本的にはハイフンを残してスペース結合
        return "space"

    # 2. 箇条書き風の判定 (Item 1, 1., a) 等で終わる場合)
    # これを小文字判定より先に持ってくる（Item a 等が space にならないように）
    if re.search(r'(?:Item|\d+|[a-z])[\.\)]$', t1):
        return "newline"

    # 3. 次のページが小文字で始まる場合 -> 段落の継続
    if t2[0].islower():
        return "space"

    # 4. 前のページが文末記号で終わっていない場合 -> 段落の継続
    if not re.search(SENTENCE_END_RE, t1):
        # ただし、次が引用符などで始まる場合は、新しい発言の可能性あり
        if t2[0] in ('"', "'", "「", "『"):
            return "newline"
        return "space"

    # 5. それ以外（文末記号がある、または次が大文字で一段落っぽい）
    return "newline"

def _find_footnote_separator_y(page: fitz.Page) -> float | None:
    """OpenCVを用いて脚注セパレーター（罫線）のY座標を特定する。"""
    try:
        import cv2
        import numpy as np
    except ImportError:
        print_log("  [PDF Ingester] OpenCV (opencv-python-headless) が未インストールのため、高度な脚注検知をスキップします。")
        return None

    # ページを画像として取得 (150 DPI で十分)
    pix = page.get_pixmap(dpi=150)
    img_data = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    gray = cv2.cvtColor(img_data, cv2.COLOR_RGB2GRAY)
    
    # 二値化 (白背景に黒文字を想定)
    _, binary = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
    
    # 水平線を強調するためのモルフォロジー演算
    # 長い水平線を検出するため、幅広のカーネルを使用
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
    detect_horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel, iterations=2)
    
    # ハフ変換による直線検出
    lines = cv2.HoughLinesP(detect_horizontal, 1, np.pi/180, threshold=100, minLineLength=50, maxLineGap=10)
    
    if lines is None:
        return None
        
    # ページ下半分の水平線を抽出
    page_height = pix.height
    candidate_ys = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        # ほぼ水平 (角度差が小さい) かつ ページ下部 40%
        if abs(y1 - y2) < 2 and y1 > page_height * 0.60:
            candidate_ys.append(y1)
            
    if not candidate_ys:
        return None
        
    # 最も上にある（本文に最も近い）セパレーターを採用
    found_y_pix = min(candidate_ys)
    # ピクセル座標を PDF ポイント座標に変換
    scale = page.rect.height / pix.height
    return found_y_pix * scale

def extract_text_fast(page: fitz.Page, ignored_patterns: Set[str] | None = None, clip_y: float | None = None) -> str:
    """PyMuPDFの dict 抽出による段落レベルのテキスト取得。
    ヘッダー/フッターを除外し、短行+文末判定で段落を分割する。
    OpenCV の足切り (clip_y) も考慮。
    """
    page_height = getattr(page.rect, "height", 0)
    header_limit = page_height * HEADER_MARGIN_RATIO
    footer_limit = page_height * (1.0 - FOOTER_MARGIN_RATIO)

    text_dict = page.get_text("dict")
    # 読み順（y0, x0）でブロックをソート
    blocks = []
    for b in text_dict.get("blocks", []):
        if b.get("type") == 0:
            blocks.append(b)
    blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))

    paragraphs = []
    current_paragraph_lines = []
    
    # ブロック間の幅を特定するためのガード
    for block in blocks:
        block_bbox = block["bbox"]
        
        # 1. 物理足切り (OpenCV で検出した線より下を無視)
        if clip_y is not None:
            if block_bbox[1] > clip_y:
                continue

        # 2. マージンベースのヘッダー/フッター除外
        if block_bbox[3] <= header_limit:
            continue
        if block_bbox[1] >= footer_limit:
            continue

        block_width = block_bbox[2] - block_bbox[0]
        if block_width <= 0:
            continue

        # 3. ブロックテキストの構築
        lines_in_block = block.get("lines", [])
        if not lines_in_block:
            continue

        block_text_parts = []
        for line in lines_in_block:
            line_text = "".join(span.get("text", "") for span in line.get("spans", []))
            block_text_parts.append(line_text)
        
        block_text = " ".join(block_text_parts).strip()
        if not block_text:
            continue

        # 4. Pass 1 パターン（ノイズ）の除去
        # 【修正】上下 15% の範囲内にあるブロックのみを対象とする（TOC等の本文領域の誤爆を防ぐ）
        if ignored_patterns:
            page_height = page.rect.height
            extended_limit_top = page_height * 0.15
            extended_limit_bottom = page_height * 0.85
            
            block_y0 = block_bbox[1]
            block_y1 = block_bbox[3]
            is_near_margin = (block_y1 <= extended_limit_top or block_y0 >= extended_limit_bottom)

            if is_near_margin and isinstance(ignored_patterns, set):
                norm_text = re.sub(r'\d+', '', block_text).strip()
                if norm_text in ignored_patterns:
                    continue

        # 5. 段落の再構築（短行判定 + 文末判定）
        for line in lines_in_block:
            line_text = "".join(span.get("text", "") for span in line.get("spans", []))
            if not line_text.strip():
                if current_paragraph_lines:
                    paragraphs.append(_finalize_paragraph(current_paragraph_lines))
                    current_paragraph_lines = []
                continue

            current_paragraph_lines.append(line_text)

            line_width = line["bbox"][2] - line["bbox"][0]
            is_short = (line_width / block_width < SHORT_LINE_RATIO)
            is_sentence_end = re.search(r'[.!?\)\]"\'”。]\d*$', line_text.strip())

            if is_short and is_sentence_end:
                paragraphs.append(_finalize_paragraph(current_paragraph_lines))
                current_paragraph_lines = []

    if current_paragraph_lines:
        paragraphs.append(_finalize_paragraph(current_paragraph_lines))

    return "\n\n".join(paragraphs)


def _finalize_paragraph(lines: list[str]) -> str:
    """行リストを1つの段落テキストに整形する。"""
    joined = " ".join(line.strip() for line in lines)
    # ハイフン分割の結合
    joined = re.sub(r'(\w)-\s+(\w)', r'\1\2', joined)
    return joined.strip()


# ===== VLMルート（Geminiフォールバック） =====

def _smart_crop_image(img: Image.Image) -> Image.Image:
    """OpenCVを用いて、黒帯や大きな余白、指の影を除去する。"""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return img

    # PIL -> OpenCV (numpy)
    open_cv_image = np.array(img.convert("RGB"))
    # RGB -> BGR
    open_cv_image = open_cv_image[:, :, ::-1].copy()
    
    gray = cv2.cvtColor(open_cv_image, cv2.COLOR_BGR2GRAY)
    
    # 適応型二値化
    # 黒帯（0に近い）や指の影も反転させて白く浮き上がらせる
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                                 cv2.THRESH_BINARY_INV, 11, 2)
    
    # ノイズ除去のためのモルフォロジー
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    
    # 輪郭抽出
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return img
    # 全ての輪郭を囲むバウンディングボックスを求める
    x_min: int = 99999
    y_min: int = 99999
    x_max: int = 0
    y_max: int = 0
    
    found = False
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        # 極端に小さいノイズは無視
        if w < 10 or h < 10:
            continue
        x_min = min(x_min, int(x))
        y_min = min(y_min, int(y))
        x_max = max(x_max, int(x + w))
        y_max = max(y_max, int(y + h))
        found = True
        
    if not found:
        return img
        
    # 安全マージン (5px)
    margin = 5
    h_orig, w_orig = open_cv_image.shape[:2]
    x_min = max(0, x_min - margin)
    y_min = max(0, y_min - margin)
    x_max = min(w_orig, x_max + margin)
    y_max = min(h_orig, y_max + margin)
    
    # 【安全装置】クロップ後の面積が元の 50% 未満になる場合は、誤認（指の写り込み等）とみなして中止
    if (x_max - x_min) * (y_max - y_min) < (w_orig * h_orig * 0.5):
        return img
        
    cropped = open_cv_image[y_min:y_max, x_min:x_max]
    # BGR -> RGB -> PIL
    cropped_rgb = cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB)
    return Image.fromarray(cropped_rgb)


async def process_page_vlm(
    pdf_path: str,
    page_num: int,
    api_key: str | None = None,
    semaphore: asyncio.Semaphore = None,  # type: ignore
    model: str | None = None,
) -> str:
    """1ページを Gemini VLM OCR で処理する。見開きの場合は分割して並列処理する。"""
    async with semaphore:
        doc = fitz.open(pdf_path)
        try:
            page = doc[page_num]
            pix = page.get_pixmap(dpi=200)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        finally:
            doc.close()

        # --- 画像分割ロジック ---
        images_to_process = []
        is_split = False
        
        width, height = img.size
        # Landscape判定 (見開き)
        if width > height:
            is_split = True
            split_x = int(width * 0.5) # デフォルト
            
            # 【V3.2 動的分離線】中央 45-55% の範囲で、垂直方向の画素値の合計が最小（隙間）の列を探す
            try:
                import cv2
                import numpy as np
                cv_img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
                gray_tmp = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
                start_x = int(width * 0.45)
                end_x = int(width * 0.55)
                central_region = gray_tmp[:, start_x:end_x]
                column_sums = np.sum(central_region, axis=0)
                min_idx = np.argmin(column_sums)
                split_x = start_x + min_idx
            except Exception:
                pass

            overlap_px = int(width * 0.02)
            left_box = (0, 0, min(width, split_x + overlap_px), height)
            right_box = (max(0, split_x - overlap_px), 0, width, height)
            
            images_to_process.append(img.crop(left_box))
            images_to_process.append(img.crop(right_box))
        else:
            images_to_process.append(img)

        # --- 各画像にスマートクロップを適用 ---
        processed_images = [_smart_crop_image(sub_img) for sub_img in images_to_process]
        
        # --- 並列 VLM 呼び出し ---
        async def _call_vlm(target_img: Image.Image, prompt_text: str) -> str:
            try:
                result = await call_gemini_async(
                    prompt=[target_img, prompt_text],
                    model=model or get_default_model("vlm"),
                    api_key=api_key,
                    temperature=0.0,
                    max_retries=3,
                    retry_delay=5.0,
                )
                result = re.sub(r"^```[a-zA-Z]*\n", "", result)
                result = re.sub(r"\n```$", "", result)
                return result.strip()
            except Exception as e:
                print_log(f"  [PDF Ingester] ページ {page_num+1} (画像) のVLM処理に失敗: {e}")
                return ""

        current_prompt = VLM_PROMPT_SINGLE if is_split else VLM_PROMPT
        tasks = [_call_vlm(pi, current_prompt) for pi in processed_images]
        results = await asyncio.gather(*tasks)
        
        combined_text = "\n\n".join(r for r in results if r)
        for pi in processed_images:
            del pi
        del img
        return combined_text.strip()


def remove_inline_running_headers(text: str, ignored_patterns: set[str]) -> str:
    """Pass 1 で検知したキーワードを用いて、本文先頭に癒着した柱を動的に除去する。"""
    if not text:
        return ""
    if not ignored_patterns:
        return text

    lines = text.split('\n')
    processed_lines = []
    
    HEADER_CHECK_LIMIT = 2
    non_empty_idx = 0
    is_in_toc = False
    
    for idx, line in enumerate(lines):
        line_processed = line
        line_strip = line.strip()
        
        if not line_strip:
            processed_lines.append(line_processed)
            continue
            
        # 目次セクションの開始を検知
        if re.search(r'Table of Contents|目次', line_strip, re.I):
            is_in_toc = True

        if non_empty_idx < HEADER_CHECK_LIMIT:
            for kw in ignored_patterns:
                escaped_kw = re.escape(kw)
                pattern = re.compile(rf"^({escaped_kw}\s*\d+|\d+\s*{escaped_kw})(\s+|$)", re.IGNORECASE)
                
                match = pattern.search(line_processed)
                if match:
                    remaining_text = line_processed[match.end():] 
                    if not remaining_text:
                        # 独立行の場合：目次セクション内であれば削除しない
                        if is_in_toc:
                            continue
                        # それ以外は削除（Running Header とみなす）
                        line_processed = ""  
                        break
                    
                    if remaining_text[0].islower():
                        line_processed = remaining_text 
                        break
        
        non_empty_idx += 1
        processed_lines.append(line_processed)
        
    # 空文字になった行（独立ヘッダー跡）は除外して結合。元からある空行は維持。
    result = '\n'.join(line for line in processed_lines if line.strip() or not line)
    return result


# ===== メインオーケストレーター =====

async def run_pdf_ingestion_async(
    pdf_path: str,
    api_key: str | None = None,
    state: Any = None,
    pdf_mode: str = "hybrid",
    model: str | None = None,
    is_book: bool = False,
    heavy_ocr: bool = False,
) -> str:
    """PDFからテキストを抽出する（2パス・ハイブリッド解析）。"""
    if state:
        state.update_status("PDF解析中...", 5)

    print_log(f"  [PDF Ingester] PDF読み込み開始: {pdf_path} (is_book={is_book}, heavy_ocr={heavy_ocr})")
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    
    # 【デバッグ用】環境変数でページ数を制限（第3章まで: ~115ページ）
    import os
    max_p_str = os.environ.get("DEBUG_MAX_PAGES")
    if max_p_str and max_p_str.isdigit():
        total_pages = min(total_pages, int(max_p_str))
        print_log(f"  [PDF Ingester] DEBUG: 処理ページ数を {total_pages} に制限します。")
    
    # --- Pass 1: Global Scan (is_book=True の場合のみ) ---
    ignored_patterns = set()
    if is_book:
        ignored_patterns = _detect_repeating_elements(doc)

    # --- VLM キャッシュのロード ---
    vlm_cache_data: dict[str, str] = {}
    if state and state.vlm_cache.exists():
        try:
            with open(state.vlm_cache, "r", encoding="utf-8") as f:
                loaded_cache = json.load(f)
                if isinstance(loaded_cache, dict):
                    vlm_cache_data = {str(k): str(v) for k, v in loaded_cache.items()}
            print_log(f"  [PDF Ingester] VLM キャッシュをロードしました ({len(vlm_cache_data)} ページ分)")
        except Exception as e:
            print_log(f"  [PDF Ingester] キャッシュ読み込みエラー: {e}")

    # --- ルーティング判定 & Pass 2 (抽出) ---
    all_page_results: dict[int, str] = {}
    vlm_page_nums: list[int] = []
    
    for i in range(total_pages):
        # キャッシュチェック
        cache_key = str(i)
        if cache_key in vlm_cache_data:
            all_page_results[i] = vlm_cache_data[cache_key]
            print_log(f"  [PDF Ingester] ページ {i+1}: キャッシュヒット")
            continue

        page = doc[i]
        
        # 脚注判定 (Heavy OCR なら OpenCV, そうでなければフォント)
        has_footnote = False
        footnote_y = None
        if heavy_ocr:
            footnote_y = _find_footnote_separator_y(page)
            if footnote_y:
                has_footnote = True
        
        # 既存のフォントベース判定も併用
        if not has_footnote and _has_footnotes(page):
            has_footnote = True

        # ルーティング判定: VLM か 高速抽出か
        # Book Mode の場合は Bipolar Routing（二極化）に従い、ページごとの判定をスキップする
        use_vlm = (pdf_mode == "full_vlm")
        if not use_vlm and not is_book:
            # Paper Mode の時だけ、不鮮明なページのみ VLM を使うハイブリッド判定を維持
            use_vlm = should_use_vlm(page, i, is_book=is_book, heavy_ocr=heavy_ocr, has_footnote=has_footnote)

        if use_vlm:
            vlm_page_nums.append(i)
            print_log(f"  [PDF Ingester] ページ {i+1}: VLM ルートを選択")
        else:
            # Python 高速抽出 (Pass 2) + 物理足切り + ノイズ除去
            text = extract_text_fast(page, ignored_patterns=ignored_patterns, clip_y=footnote_y)
            all_page_results[i] = text
            print_log(f"  [PDF Ingester] ページ {i+1}: Python ルートを選択 (高速抽出)")

    doc.close()

    # --- VLM処理 ---
    if vlm_page_nums:
        semaphore = asyncio.Semaphore(VLM_SEMAPHORE_LIMIT)
        async def _process_vlm_page(page_num: int) -> tuple[int, str]:
            return page_num, await process_page_vlm(pdf_path, page_num, api_key, semaphore, model=model)
        
        tasks = [_process_vlm_page(pn) for pn in vlm_page_nums]
        for coro in asyncio.as_completed(tasks):
            pn, res = await coro
            all_page_results[pn] = res
            
            # キャッシュに保存
            vlm_cache_data[str(pn)] = res
            if state:
                try:
                    with open(state.vlm_cache, "w", encoding="utf-8") as f:
                        json.dump(vlm_cache_data, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    print_log(f"  [PDF Ingester] キャッシュ保存エラー: {e}")

                p = int((len(all_page_results) / total_pages) * 100)
                state.update_status(f"PDF解析中... ({len(all_page_results)}/{total_pages})", p)

    # --- 柱のインライン除去 (V3.1) ---
    if is_book and ignored_patterns:
        print_log("  [PDF Ingester] 癒着した Running Header の除去を行っています...")
        for i in range(total_pages):
            if i in all_page_results:
                all_page_results[i] = remove_inline_running_headers(all_page_results[i], ignored_patterns)

    # --- ページ間結合 (Franken-chunks 解決) ---
    print_log("  [PDF Ingester] ページ間テキストの結合処理 (Franken-chunks 解決) ...")
    full_text = ""
    for i in range(total_pages):
        current_text = all_page_results.get(i, "").strip()
        if not current_text:
            continue
            
        if not full_text:
            full_text = current_text
            continue
            
        # 結合判定
        action = _should_join_page_boundary(full_text, current_text)
        
        if action == "merge_hyphen":
            full_text = full_text.rstrip("-") + current_text
        elif action == "space":
            full_text += " " + current_text
        else:  # newline
            full_text += "\n\n" + current_text
            
    return full_text


def run_pdf_ingestion(pdf_path: str, api_key: str | None = None, state: Any = None) -> str:
    """同期呼び出しラッパー"""
    from .llm_client import run_async
    return run_async(run_pdf_ingestion_async(pdf_path, api_key, state))
