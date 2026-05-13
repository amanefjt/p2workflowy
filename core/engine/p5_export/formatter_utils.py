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

def parse_resume_heading_line(
    stripped_line: str, in_section_expansion: bool
) -> "tuple[int, str, bool, bool] | None":
    """strip 済みの行を見出しとして解析する。
    戻り値: (level, cleaned_text, new_in_section_expansion, add_bracket) または None。
    「各セクション」を含む H1 の直下の H2 は add_bracket=True でフラット化対象を示す。
    """
    if not stripped_line.startswith("#"):
        return None
    m = re.match(r"^(#+)\s*(.*)", stripped_line)
    if not m:
        return None
    level = len(m.group(1))
    cleaned = clean_heading_text(m.group(2))
    if level == 1:
        return level, cleaned, "各セクション" in cleaned, False
    if level == 2 and in_section_expansion:
        return level, cleaned, in_section_expansion, True
    return level, cleaned, in_section_expansion, False


def format_resume_markdown(resume_content: str, shift: int = 2) -> str:
    """レジュメの見出しレベルを調整する。
    shift=2: H1→H3（書籍全体レジュメ・論文レジュメ）
    shift=3: H1→H4（各章レジュメ）
    「3. 各セクションの展開」内のH2節見出しは H1+shift にフラット化しブラケットを付ける。
    """
    if not resume_content:
        return ""
    lines = resume_content.split("\n")
    adjusted = []
    in_section_expansion = False
    for line in lines:
        result = parse_resume_heading_line(line.strip(), in_section_expansion)
        if result is not None:
            level, title, in_section_expansion, add_bracket = result
            if add_bracket:
                adjusted.append("#" * (1 + shift) + " [" + title + "]")
            elif level == 1:
                adjusted.append("#" * (1 + shift) + " " + title)
            else:
                adjusted.append("#" * (level + shift) + " " + title)
            continue
        adjusted.append(line)
    return "\n".join(adjusted)
