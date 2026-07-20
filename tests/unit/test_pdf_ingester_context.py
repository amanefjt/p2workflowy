"""pdf_ingester の前文脈組み立て（I-21）のテスト。"""

from core.engine.p1_ingest.pdf_ingester import build_prev_contexts


def test_first_page_has_empty_context():
    # 物理ページのネイティブテキスト
    native = ["page zero text", "page one text", "page two text"]
    # 各論理画像がどの物理ページ由来か（分割なし=1:1）
    src = [0, 1, 2]
    ctx = build_prev_contexts(native, src, tail_chars=100)
    assert ctx[0] == ""  # 先頭は前文脈なし


def test_context_is_previous_physical_page_tail():
    native = ["A" * 50, "B" * 50, "C" * 50]
    src = [0, 1, 2]
    ctx = build_prev_contexts(native, src, tail_chars=10)
    assert ctx[1] == "A" * 10   # 前ページ(物理0)の末尾10字
    assert ctx[2] == "B" * 10


def test_spread_split_halves_share_physical_page_text():
    # 物理ページ0が2分割 → 論理画像0,1がともに物理0由来。物理1は論理2。
    native = ["phys0", "phys1"]
    src = [0, 0, 1]
    ctx = build_prev_contexts(native, src, tail_chars=100)
    assert ctx[0] == ""       # 先頭
    assert ctx[1] == "phys0"  # 前の論理(物理0)の末尾
    assert ctx[2] == "phys0"  # 前の論理(物理0の右半分)の由来テキスト


def test_tail_shorter_than_limit_returns_whole():
    native = ["short"]
    src = [0, 0]
    ctx = build_prev_contexts(native, src, tail_chars=100)
    assert ctx[1] == "short"
