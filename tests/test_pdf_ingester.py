import pytest
import re
from unittest.mock import MagicMock, patch
from core.pdf_ingester import (
    _finalize_paragraph, 
    _detect_repeating_elements,
    _should_join_page_boundary,
    extract_text_fast,
    should_use_vlm,
    remove_inline_running_headers
)

# ----------------------------------------------------------------
# 1. ページ跨ぎ判定 (_should_join_page_boundary) のテスト
# ----------------------------------------------------------------
@pytest.mark.parametrize("text1, text2, expected", [
    # A) 結合すべきケース (Space)
    ("This is a long sentence", "that continues.", "space"),
    ("He said,", "I will go.", "space"),
    # B) 結合すべきケース (Merge Hyphen)
    ("This is a very long inter-", "national conference.", "merge_hyphen"),
    ("Self-", "consciousness is key.", "merge_hyphen"),
    # C) 結合すべきでないケース (Newline / \n\n)
    ("End of the paragraph.", "New one starts.", "newline"),
    ("Section 1.1!", "The next part.", "newline"),
    ("Closing quote.\"", "Next speaker.", "newline"),
    # D) 特殊ケース
    ("Wait...", "for it.", "space"), # 三点リーダーは続くことが多い
    ("Item 1.", "Item 2.", "newline"),  # 箇条書き
    ("Step A)", "Step B)", "newline"),  # 箇条書き改変
    ("the", "End.", "space"),      # 小文字開始を優先
])
def test_should_join_page_boundary_logic(text1, text2, expected):
    """純粋関数 _should_join_page_boundary の網羅テスト"""
    assert _should_join_page_boundary(text1, text2) == expected


# ----------------------------------------------------------------
# 2. 物理足切り (clip_y) のテスト (Mocking)
# ----------------------------------------------------------------
def test_physical_clip_y():
    """extract_text_fast が clip_y を受けてブロックを除外するかのテスト"""
    mock_page = MagicMock()
    mock_page.rect.height = 1000
    # bbox in dict mode is a tuple/list: (y0, x1, y1) or similar depending on tool, 
    # but our code uses block["bbox"][1] for y0.
    mock_page.get_text.return_value = {
        "blocks": [
            {
                "type": 0, 
                "bbox": [50, 100, 500, 150], 
                "lines": [{"bbox": [50, 100, 500, 150], "spans": [{"text": "Main text top", "size": 10}]}]
            },
            {
                "type": 0, 
                "bbox": [50, 200, 500, 250], 
                "lines": [{"bbox": [50, 200, 500, 250], "spans": [{"text": "Main text bottom", "size": 10}]}]
            },
            {
                "type": 0, 
                "bbox": [50, 700, 500, 750], 
                "lines": [{"bbox": [50, 700, 500, 750], "spans": [{"text": "Footnote text", "size": 8}]}]
            },
        ]
    }
    
    # 足切りなし
    text_all = extract_text_fast(mock_page, clip_y=None)
    assert "Footnote text" in text_all
    
    # 足切りあり (y=600 でカット)
    text_clipped = extract_text_fast(mock_page, clip_y=600.0)
    assert "Main text top" in text_clipped
    assert "Main text bottom" in text_clipped
    assert "Footnote text" not in text_clipped


# ----------------------------------------------------------------
# 3. VLM ルーティングの真理値表テスト
# ----------------------------------------------------------------
@pytest.mark.parametrize("is_book, heavy_ocr, has_fn, expected_vlm", [
    # (is_book, heavy_ocr, has_fn) -> expected
    (False, False, False, False), # 通常モード -> Python
    (True,  False, False, False), # 決定論的Book -> Python (基本)
    (True,  True,  False, False), # 重OCRだが脚注なし -> Python
    (True,  True,  True,  True),  # 重OCRかつ脚注あり -> VLM (安全策)
    (False, True,  True,  False), # 非Bookなら重OCRでも Python (既存互換)
])
def test_vlm_routing_logic(is_book, heavy_ocr, has_fn, expected_vlm):
    """should_use_vlm の振り分けロジックの網羅テスト"""
    mock_page = MagicMock()
    # 十分な長さのテキストを返すことで MIN_TEXT_CHARS による VLM 送りを回避
    mock_page.get_text.return_value = "A" * 1000 
    
    # page_num=1 を渡すことで「1ページ目強制VLM」を回避してロジックを検証
    assert should_use_vlm(mock_page, page_num=1, is_book=is_book, heavy_ocr=heavy_ocr, has_footnote=has_fn) == expected_vlm

def test_should_use_vlm_trap_evasion():
    """巨大画像検知 (Trap Evasion) のテスト"""
    mock_page = MagicMock()
    mock_page.rect.width = 100
    mock_page.rect.height = 100
    # get_text は十分な量を返していても、画像があれば VLM
    mock_page.get_text.return_value = "A" * 1000
    
    # ページ面積 10000 に対し、画像面積 9100 (91%)
    mock_page.get_images.return_value = [("Im0",)]
    mock_bbox = MagicMock()
    mock_bbox.width = 91
    mock_bbox.height = 100
    mock_page.get_image_bbox.return_value = mock_bbox
    
    # should_use_vlm が画像面積を見て True を返すことを確認
    assert should_use_vlm(mock_page, page_num=1) is True

def test_process_page_vlm_splitting_logic():
    """process_page_vlm 内の画像分割ロジックの判定（モック）"""
    with patch("fitz.open") as mock_fitz_open, \
         patch("PIL.Image.frombytes") as mock_frombytes, \
         patch("core.pdf_ingester.call_gemini_async") as mock_call, \
         patch("numpy.array") as mock_np_array:
        
        mock_doc = MagicMock()
        mock_page = MagicMock()
        # Landscape 画像 (W: 1000, H: 500)
        mock_img = MagicMock()
        mock_img.size = (1000, 500)
        mock_frombytes.return_value = mock_img
        
        # OpenCV ロジック (_smart_crop_image) で np.array(img...) が呼ばれる
        # 3次元 (H, W, C) のダミー配列を返すようにする
        import numpy as np
        mock_np_array.return_value = np.zeros((500, 1000, 3), dtype=np.uint8)
        
        mock_fitz_open.return_value = mock_doc
        mock_doc[0] = mock_page
        mock_call.return_value = "Extracted Text"

        # 実際に非同期関数を走らせて、call_gemini_async が 2回（左右）呼ばれるかを確認
        import asyncio
        from core.pdf_ingester import process_page_vlm
        sem = asyncio.Semaphore(1)
        
        # クロップされた画像のモックを返すように設定
        mock_img.crop.return_value = MagicMock()
        mock_img.convert.return_value = mock_img # convert("RGB") も自身を返す
        
        asyncio.run(process_page_vlm("dummy.pdf", 0, "key", sem))
        
        # 左右 2 回呼ばれているはず
        assert mock_call.call_count == 2

def test_remove_inline_running_headers():
    """インライン柱除去のテスト（安全装置、マルチライン、および位置制約の挙動確認）"""
    keywords = {"The Ethnographic Effect I", "Property, Substance and Effect"}
    
    # テキスト1: 複数行にまたがる癒着（先頭2行以内）
    text1 = (
        "The Ethnographic Effect I 17 ctor or acted upon...\n" # 1行目: マッチ -> 除去
        "2 Property, Substance and Effect addressed to another audience.\n" # 2行目: マッチ -> 除去
        "This is a normal sentence."
    )
    res1 = remove_inline_running_headers(text1, keywords)
    assert "The Ethnographic Effect I 17" not in res1
    assert "2 Property, Substance and Effect" not in res1
    assert "ctor or acted upon..." in res1
    assert "addressed to another" in res1

    # テキスト2: 3行目以降にあるマッチ候補（誤爆防止: HEADER_CHECK_LIMIT ガード）
    text2 = (
        "Line 1\n"
        "Line 2\n"
        "The Ethnographic Effect I 17 This is in the middle of the page.\n" # 3行目: マッチ条件は満たすが idx >= 2 なのでスルー
        "Another line."
    )
    res2 = remove_inline_running_headers(text2, keywords)
    assert "The Ethnographic Effect I 17" in res2 # 削除されないこと

    # テキスト3: 目次やタイトル（既存のガードレールテスト）
    text3 = (
        "Table of Contents\n"
        "The Ethnographic Effect I 17\n" # 後続がない（目次） -> 維持されるべき
        "Some other text."
    )
    res3 = remove_inline_running_headers(text3, keywords)
    assert "The Ethnographic Effect I 17" in res3


# ----------------------------------------------------------------
# 4. Pass 1: 反復要素検知のテスト（モック）
# ----------------------------------------------------------------
@patch("fitz.open")
def test_detect_repeating_elements_logic(mock_open):
    """Pass 1: 反復要素検知のテスト（正規化含む）"""
    mock_doc = MagicMock()
    mock_pages = []
    # 5ページ分。ヘッダー "Ethnographic Effect | [Page]" が 3 ページ以上に出る
    for i in range(1, 6):
        p = MagicMock()
        p.rect.height = 800
        # get_text("blocks") format matches our usage in _detect_repeating_elements
        p.get_text.return_value = [
            (50, 20, 300, 40, f"Ethnographic Effect | {i}", 0, 0),
            (50, 100, 500, 200, "Actual content", 1, 0),
        ]
        mock_pages.append(p)
    
    mock_doc.__len__.return_value = 5
    # Python のイテレータとしても動作するように設定
    mock_doc.__iter__.return_value = iter(mock_pages)
    # インデックスアクセスもカバー
    mock_doc.__getitem__.side_effect = lambda i: mock_pages[i]
    
    patterns = _detect_repeating_elements(mock_doc)
    # "Ethnographic Effect | " が抽出されるべき (数字は re.sub で消える)
    # カウントしきい値(40%) = 5 * 0.4 = 2 ページ以上
    assert any("Ethnographic Effect" in p for p in patterns)

def test_opencv_lazy_import_safety():
    """OpenCVが未インストール環境でもクラッシュしないことの検証"""
    with patch.dict("sys.modules", {"cv2": None}):
        try:
            import cv2
            assert False, "Should not be able to import cv2"
        except (ImportError, ModuleNotFoundError):
            pass
        
        # 内部で import cv2 していても、トップレベルでなければロード可能
        import core.pdf_ingester
        assert True
