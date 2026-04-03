import re

def clean_heading_text(text: str) -> str:
    """見出しから不要な記号（# や []）を除去する。"""
    if not text: return ""
    # 1. 冒頭の # やスペースを除去
    cleaned = re.sub(r'^[#\s]+', '', text)
    # 2. 末尾の # やスペースを除去
    cleaned = re.sub(r'[#\s]+$', '', cleaned)
    # 3. 冒頭/末尾が完全に一致する [] や () を持つ場合は、必要に応じて剥がす（オプション）
    # 現状はシンプルに # 除去のみに留める（内容保護優先）
    return cleaned.strip()

def sanitize_wf_text(text: str) -> str:
    """Workflowy 出力用テキストのサニタイズ（タグ除去・改行平坦化・空白正規化）"""
    if not text:
        return ""
    # 1. 残留タグの除去 (</?chunk_12345>)
    text = re.sub(r'</?chunk_\d+>', '', text)
    # 2. 改行をスペースに置換し、連続する空白を1つに集約
    text = text.replace('\n', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def format_resume_markdown(resume_content: str, shift: int = 2) -> str:
    """レジュメの見出しレベルを調整する。
    shift=2: H1→H3（書籍全体レジュメ・論文レジュメ）
    shift=3: H1→H4（各章レジュメ）
    """
    if not resume_content:
        return ""
    lines = resume_content.split("\n")
    adjusted = []
    for line in lines:
        if line.strip().startswith("#"):
            match = re.match(r"^\s*(#+)\s*(.*)$", line)
            if match:
                current_level = len(match.group(1))
                title_text = clean_heading_text(match.group(2))
                new_level = current_level + shift
                adjusted.append("#" * new_level + " " + title_text)
                continue
        adjusted.append(line)
    return "\n".join(adjusted)
