from core.models import TreeNode
from core.engine.p4_translate.prompt_builder import TranslationPromptBuilder, WINDOW_MAX_CHARS


def _node(text, role="p"):
    return TreeNode(id="x", text=text, role=role, seq_index=0.0)


def _builder():
    return TranslationPromptBuilder("tpl")


def test_empty_nodes_returns_empty():
    assert _builder().format_previous_translation([]) == ""


def test_paragraphs_are_kept_whole_and_in_order():
    nodes = [_node("first para"), _node("second para"), _node("third para")]
    out = _builder().format_previous_translation(nodes)
    assert "first para" in out and "third para" in out
    assert out.index("first para") < out.index("third para")
    # 切り抜きされていない（200字トリムの廃止）
    long = "あ" * 500
    out2 = _builder().format_previous_translation([_node(long)])
    assert long in out2


def test_window_char_limit():
    para = "あ" * 900  # 900字 × 3 = 2700字 > WINDOW_MAX_CHARS(2000)
    nodes = [_node(f"{i}:" + para) for i in range(3)]
    out = _builder().format_previous_translation(nodes)
    assert "2:" in out and "1:" in out   # 末尾から2段落分（~1800字）は入る
    assert "0:" not in out               # 3つ目は上限超過で入らない


def test_single_oversized_paragraph_still_included():
    huge = "あ" * (WINDOW_MAX_CHARS + 500)
    out = _builder().format_previous_translation([_node(huge)])
    assert huge in out  # 最低1段落は必ず入れる（空ウィンドウ回避）


def test_non_p_roles_are_skipped():
    nodes = [_node("heading text", role="h2"), _node("para text")]
    out = _builder().format_previous_translation(nodes)
    assert "heading text" not in out and "para text" in out
