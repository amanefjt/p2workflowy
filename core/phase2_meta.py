"""
p2workflowy V2 Phase 2: Meta-Generation
LLM を用いたレジュメ生成、キーワード抽出、glossary マージ。
"""

import json
from pathlib import Path
from typing import List

from .config import load_coreprompts, load_glossary_csv, print_log
from .models import load_chunks_from_json
from .llm_client import call_gemini


# サンプリング閾値
MAX_INPUT_CHARS = 5_000_000  # これ以上の場合はサンプリング（Gemini 1.5 Pro/Flashのコンテキストを考慮）
HEAD_CHARS = 500_000        # 大幅に拡大
TAIL_CHARS = 200_000        # 大幅に拡大


def _build_full_text(chunks_path: str | Path) -> str:
    """Phase 1 の出力チャンクを連結してフルテキストを構築する。"""
    chunks = load_chunks_from_json(str(chunks_path))
    return "\n\n".join(c.text for c in chunks)


def _sample_text(full_text: str) -> str:
    """テキストが長すぎる場合、冒頭 + 末尾をサンプリングする。"""
    if len(full_text) <= MAX_INPUT_CHARS:
        return full_text

    head = full_text[:HEAD_CHARS]
    tail = full_text[-TAIL_CHARS:]
    print_log(f"  [Phase 2] テキストサンプリング: {len(full_text)} 文字 → 冒頭 {HEAD_CHARS} + 末尾 {TAIL_CHARS}")
    return head + "\n\n[...中略...]\n\n" + tail


def generate_resume(text: str, api_key: str | None = None, expertise: str = "文化人類学") -> str:
    """
    SUMMARY_PROMPT を使ってレジュメ（構造化要約）を生成する。

    Args:
        text: 全文テキスト（必要に応じてサンプリング済み）
        api_key: APIキー
        expertise: 専門分野

    Returns:
        str: 生成されたレジュメ（Markdown）
    """
    prompts = load_coreprompts()
    prompt_tpl = prompts["SUMMARY_PROMPT"]

    # プロンプト構築
    prompt = prompt_tpl.replace("{expertise}", expertise).replace("{context_guide}", "").replace("{text}", text)

    print_log(f"  [Phase 2] レジュメ生成中... (入力: {len(text)} 文字)")
    resume = call_gemini(prompt, api_key=api_key, temperature=0.3)
    print_log(f"  [Phase 2] レジュメ生成完了 ({len(resume)} 文字)")

    return resume


def extract_keywords(text: str, api_key: str | None = None, expertise: str = "文化人類学") -> list[dict]:
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


def run_phase2(
    phase1_state_path: str | Path,
    phase2_state_path: str | Path,
    glossary_path: str | None = None,
    api_key: str | None = None,
    save_state: bool = True,
    expertise: str = "文化人類学",
) -> dict:
    """
    Phase 2 メイン処理: レジュメ生成 → キーワード抽出 → Glossary マージ。

    Args:
        phase1_state_path: Phase 1 の出力 JSON パス
        phase2_state_path: Phase 2 の出力 JSON パス
        glossary_path: glossary.csv のパス（省略時はデフォルト）
        save_state: state/phase2_meta.json に保存するか
        expertise: 専門分野

    Returns:
        dict: {"resume_content": str, "keywords_data": list}
    """
    # Phase 1 の出力を読み込み
    phase1_state_path = Path(phase1_state_path)
    if not phase1_state_path.exists():
        raise FileNotFoundError(
            f"Phase 1 の出力が見つかりません: {phase1_state_path}\n"
            "先に Phase 1 を実行してください。"
        )

    # Load full text from phase1 state
    full_text = _build_full_text(phase1_state_path)

    # Sample text for LLM input
    text_for_llm = _sample_text(full_text)

    # 1. レジュメ生成
    resume_content = generate_resume(text_for_llm, api_key=api_key, expertise=expertise)

    # 2. キーワード抽出
    keywords = extract_keywords(text_for_llm, api_key=api_key, expertise=expertise)

    # 3. Glossary マージ
    keywords_data = merge_with_glossary(keywords, glossary_path)

    result = {
        "resume_content": resume_content,
        "keywords_data": keywords_data,
    }

    # State 保存
    if save_state:
        with open(phase2_state_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print_log(f"  [Phase 2] State 保存: {phase2_state_path}")

    return result
