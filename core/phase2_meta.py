"""
p2workflowy V2 Phase 2: Meta-Generation
LLM を用いたレジュメ生成、キーワード抽出、glossary マージ。
"""

import json
from pathlib import Path
from typing import List, Any, Optional

from .config import load_coreprompts, load_glossary_csv, print_log
from .models import load_chunks_from_json
from .llm_client import call_gemini
from .engine.meta_analyzer import MetaAnalyzer


# サンプリング閾値 (仕様書に基づき調整)
MAX_INPUT_CHARS = 500_000   # 論文モード：これ以上の場合はサンプリング
HEAD_CHARS = 100_000        # 冒頭部分
TAIL_CHARS = 50_000         # 末尾部分

MAX_BOOK_CHARS = 1_500_000  # 書籍モード：セーフティ・ハードリミット
BOOK_HEAD_CHARS = 1_000_000 # 書籍モード冒頭
BOOK_TAIL_CHARS = 500_000   # 書籍モード末尾


def _build_full_text(chunks_path: str | Path) -> str:
    """Phase 1 の出力チャンクを連結してフルテキストを構築する。"""
    chunks = load_chunks_from_json(str(chunks_path))
    return "\n\n".join(c.text for c in chunks)


def _sample_text(full_text: str, is_book: bool = False) -> str:
    """テキストが長すぎる場合、冒頭 + 末尾をサンプリングする。"""
    limit = MAX_BOOK_CHARS if is_book else MAX_INPUT_CHARS
    head_size = BOOK_HEAD_CHARS if is_book else HEAD_CHARS
    tail_size = BOOK_TAIL_CHARS if is_book else TAIL_CHARS

    if len(full_text) <= limit:
        return full_text

    head = full_text[:head_size]
    tail = full_text[-tail_size:]
    print_log(f"  [Phase 2] テキストサンプリング ({'Book' if is_book else 'Paper'}): {len(full_text)} 文字 → 冒頭 {head_size} + 末尾 {tail_size}")
    return head + "\n\n[...中略...]\n\n" + tail


def generate_resume(text: str, api_key: str | None = None, expertise: str = "文化人類学", model: str | None = None, thinking_level: str = "High", is_book: bool = False, state: Any = None, resume_context: Optional[str] = None) -> str:
    """
    SUMMARY_PROMPT または GLOBAL_SUMMARY_PROMPT を使ってレジュメ（構造化要約）を生成する。

    Args:
        text: 全文テキスト（必要に応じてサンプリング済み）
        api_key: APIキー
        expertise: 専門分野
        is_book: 書籍モードかどうか。False の場合は論文用プロンプトを使用。
        resume_context: 外部から注入するコンテキスト（書籍全体の要約など）。

    Returns:
        str: 生成されたレジュメ（Markdown）
    """
    prompts = load_coreprompts()
    
    # モードに応じてプロンプトテンプレートを切り替え
    if is_book:
        # 書籍モード: GLOBAL_SUMMARY_PROMPT を優先使用
        prompt_tpl = prompts.get("GLOBAL_SUMMARY_PROMPT", prompts["SUMMARY_PROMPT"])
    else:
        # 論文モード: SUMMARY_PROMPT_ronbun を優先使用
        prompt_tpl = prompts.get("SUMMARY_PROMPT_ronbun", prompts["SUMMARY_PROMPT"])

    # プロンプト構築
    context_guide = "論文全体の構造、各セクションの論理構成を抽出してください。"
    if "[untitled section]" in text:
        context_guide += "\n注意: '# [untitled section]' は、アブストラクト直後の明示的な見出しのない序論を表します。これを「序論」として適切に要約に含めてください。"
    
    # 外部コンテキスト（書籍全体の要約等）がある場合は、ガイドの冒頭に追加
    if resume_context:
        context_guide = f"【背景知識：書籍全体の要約】\n{resume_context}\n\n上記に基づき、この章のより詳細な要約を作成してください。\n" + context_guide

    prompt = prompt_tpl.replace(
        "{expertise}", expertise
    ).replace(
        "{context_guide}", context_guide
    ).replace(
        "{text}", text
    )

    print_log(f"  [Phase 2] レジュメ生成中... (入力: {len(text)} 文字)")
    # 長文出力を許可
    resume = call_gemini(
        prompt, api_key=api_key, temperature=0.3, model=model, 
        thinking_level=thinking_level, max_output_tokens=8192,
        log_dir=state.logs_dir if state else None,
        metrics_metadata={"section": "global_resume"}
    )
    print_log(f"  [Phase 2] レジュメ生成完了 ({len(resume)} 文字)")

    return resume


def extract_keywords(text: str, api_key: str | None = None, expertise: str = "文化人類学", model: str | None = None, thinking_level: str = "High", state: Any = None) -> list[dict]:
    """
    KEYWORD_EXTRACTION_PROMPT を使ってキーワードを抽出する。

    Args:
        text: 全文テキスト（必要に応じてサンプリング済み）
        api_key: APIキー
        expertise: 専門分野

    Returns:
        list[dict]: [{"en": "...", "ja": "...", "definition": "..."}, ...]
    """
    prompts = load_coreprompts()
    prompt_tpl = prompts["KEYWORD_EXTRACTION_PROMPT"]

    # プロンプト構築
    prompt = prompt_tpl.replace("{expertise}", expertise).replace("{text}", text)

    print_log(f"  [Phase 2] キーワード抽出中...")
    response = call_gemini(
        prompt,
        api_key=api_key,
        temperature=0.2,
        response_mime_type="application/json",
        model=model,
        thinking_level=thinking_level,
        log_dir=state.logs_dir if state else None,
        metrics_metadata={"section": "keywords"}
    )

    # JSON パース
    try:
        keywords = json.loads(response)
        if not isinstance(keywords, list):
            print_log(f"  [Phase 2] 警告: キーワードがリスト形式ではありません。空リストとして処理。")
            keywords = []
    except json.JSONDecodeError as e:
        print_log(f"  [Phase 2] 警告: キーワード JSON パースエラー: {e}")
        # JSON ブロックを抽出する試み
        import re
        json_match = re.search(r'\[.*\]', response, re.DOTALL)
        if json_match:
            try:
                keywords = json.loads(json_match.group())
            except json.JSONDecodeError:
                keywords = []
        else:
            keywords = []

    print_log(f"  [Phase 2] キーワード抽出完了: {len(keywords)} 件")
    return keywords


def merge_with_glossary(
    keywords: list[dict],
    glossary_path: str | None = None,
) -> list[dict]:
    """
    LLM 抽出キーワードと glossary.csv をマージする。
    glossary.csv のエントリが優先（上書き）。

    Args:
        keywords: LLM から抽出されたキーワードリスト
        glossary_path: glossary.csv のパス

    Returns:
        list[dict]: マージ済みキーワードリスト
    """
    glossary = load_glossary_csv(glossary_path)

    # LLM キーワードを辞書化（en をキーとして）
    keyword_dict: dict[str, dict] = {}
    for kw in keywords:
        en = kw.get("en", "").strip()
        if en:
            keyword_dict[en.lower()] = kw

    # glossary.csv のエントリで上書き/追加
    for en_term, ja_term in glossary.items():
        key = en_term.lower()
        if key in keyword_dict:
            # 既存キーワードの日本語を glossary で上書き
            keyword_dict[key]["ja"] = ja_term
        else:
            # 新規追加
            keyword_dict[key] = {
                "en": en_term,
                "ja": ja_term,
                "definition": "",
            }

    merged = list(keyword_dict.values())
    print_log(f"  [Phase 2] Glossary マージ完了: {len(merged)} 件 (glossary: {len(glossary)} 件)")
    return merged


def run_phase2_v3(
    phase1_state_path: str | Path,
    phase2_state_path: str | Path,
    glossary_path: str | None = None,
    api_key: str | None = None,
    save_state: bool = True,
    expertise: str = "文化人類学",
    model: str | None = None,
    thinking_level: str = "High",
    state: "Any" = None,
    is_book: bool = False,
    resume_content: Optional[str] = None, # 外部（BookManager等）から提供された要約
) -> dict:
    """
    Phase 2 V3 (Golden Rewrite): 文献の DNA (構造的アンカー) とメタデータを抽出する。
    """
    print_log(f"--- Phase 2 V3: Meta-Generation (DNA Extraction) ---")
    
    # 1. Phase 1 の出力を読み込み
    chunks = load_chunks_from_json(str(phase1_state_path))
    if not chunks:
        raise FileNotFoundError(f"Phase 1 の出力が空または見つかりません: {phase1_state_path}")

    # 2. DNA 抽出 (1ページ目の物理チャンクを使用)
    page1_chunks = [c for c in chunks if getattr(c, 'page_idx', 0) == 0]
    if not page1_chunks:
        # ページインデックスがない場合は冒頭の数個のチャンクを代用
        page1_chunks = chunks[:20]
        
    print_log(f"  [Phase 2 V3] DNA 抽出中 (Page 1 チャンク数: {len(page1_chunks)})...")
    analyzer = MetaAnalyzer()
    dna = analyzer.analyze_dna(page1_chunks)
    print_log(f"  [Phase 2 V3] DNA 抽出完了: Title='{dna.get('title', 'N/A')}'")

    # 3. テキストサンプリング & レジュメ生成
    full_text = "\n\n".join(c.text for c in chunks)
    text_for_llm = _sample_text(full_text, is_book=is_book)
    
    # 章ごとのレジュメ生成（外部から全体要約がある場合はそれをガイドとして注入）
    chapter_resume = generate_resume(
        text_for_llm, api_key=api_key, expertise=expertise, model=model, 
        thinking_level=thinking_level, is_book=is_book, state=state,
        resume_context=resume_content 
    )
    
    # 4. キーワード抽出 & マージ
    keywords = extract_keywords(
        text_for_llm, api_key=api_key, expertise=expertise, model=model, 
        thinking_level=thinking_level, state=state
    )
    keywords_data = merge_with_glossary(keywords, glossary_path)

    result = {
        "dna": dna,
        "resume_content": chapter_resume,
        "keywords_data": keywords_data,
    }

    # State 保存
    if save_state:
        with open(phase2_state_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print_log(f"  [Phase 2 V3] State 保存: {phase2_state_path}")

    return result


def run_phase2(
    phase1_state_path: str | Path,
    phase2_state_path: str | Path,
    glossary_path: str | None = None,
    api_key: str | None = None,
    save_state: bool = True,
    expertise: str = "文化人類学",
    model: str | None = None,
    thinking_level: str = "High",
    state: "Any" = None,
    is_book: bool = False,
) -> dict:
    """
    既存の run_phase2 (後方互換性)。V3 への移行を促すか、自動的に v3 を呼ぶようにする。
    """
    return run_phase2_v3(
        phase1_state_path, phase2_state_path, glossary_path, api_key, save_state, expertise, model, thinking_level, state, is_book
    )
