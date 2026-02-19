import pytest
from src.main import format_chapter_node
from src.utils import Utils

def test_format_chapter_node_indentation():
    """
    format_chapter_node が正しいインデント構造を生成することを確認する
    """
    chapter_title = "Chapter 1"
    ch_resume = "- Point 1\n- Point 2"
    ch_translated = "# Heading 1\n- Body 1\n- Body 2"

    # Utils.markdown_to_workflowy が期待通り動くか確認（依存）
    # ここではシンプルに振る舞いを確認
    
    result = format_chapter_node(chapter_title, ch_resume, ch_translated)
    
    lines = result.splitlines()
    
    # Check Chapter Node (Indent 2)
    assert lines[0] == "  - Chapter 1"
    
    # Check Resume Wrapper (Indent 4)
    assert lines[1] == "    - 章レジュメ"
    # Resume content (Indent 6)
    assert lines[2].startswith("      - Point 1")
    
    # Check Translation Body
    # 「本文翻訳」ラッパーがなく、Bodyが直接章の子（Indent 4）になっていること
    # Resumeが終わった後の行を探す
    
    # Resume is 3 lines: Wrapper + 2 lines of content
    # lines[1]: Wrapper
    # lines[2]: Point 1
    # lines[3]: Point 2
    
    # Translation starts at lines[4]
    # ch_translated has # Heading 1 (removed) + Body 1 + Body 2
    # Body 1 should be at Indent 4
    
    assert lines[4].startswith("    - Body 1")
    assert lines[5].startswith("    - Body 2")
    
    # 「本文翻訳」という文字列が含まれていないこと
    assert "本文翻訳" not in result
