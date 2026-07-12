import asyncio
import json
from pathlib import Path
from typing import List, Dict, Any

from .models import TreeNode
from .config import load_coreprompts, load_glossary_entries, print_log
from .llm_client import translate_batch, tier_manager, GeminiTier, apply_tier_settings

# アトミック・エンジンのインポート
from .engine.p4_translate.parallel_translator import ParallelTranslator
from .engine.p4_translate.prompt_builder import TranslationPromptBuilder
from .engine.p4_translate.tree_reconstructor import TreeReconstructor
from .engine.p4_translate.term_layer import build_term_layer


def build_translation_context(book_resume: str, document_resume: str, is_book: bool) -> str:
    """翻訳プロンプトの {resume_content} に注入する上位コンテキストを組み立てる。

    論文モード: 論文レジュメそのもの。
    書籍モード: 書籍全体レジュメ＋章レジュメ（どちらか欠けても成立する）。
    """
    if not is_book:
        return document_resume or ""
    parts = []
    if book_resume:
        parts.append(f"【書籍全体の要約】\n{book_resume}")
    if document_resume:
        parts.append(f"【この章の要約】\n{document_resume}")
    return "\n\n".join(parts)


async def process_section_modular(
    section_name: str,
    chunks: List[dict],
    translation_context: str,
    translator: ParallelTranslator,
    prompt_builder: TranslationPromptBuilder,
    is_book: bool = False,
    state: Any = None,
    **kwargs
):
    """セクション（章）単位の翻訳処理。バッチ翻訳と局所ツリー構築を管理する。"""
    print_log(f"  >>> [Start Section] {section_name}")

    translated_nodes = await translator.translate_section_chunks(
        section_name=section_name,
        chunks=chunks,
        prompt_builder_func=lambda nodes: prompt_builder.format_previous_translation(nodes),
        translate_func=translate_batch,
        prompt_template=prompt_builder.prompt_template,
        glossary_content=prompt_builder.format_glossary(),
        resume_content=translation_context,
        state=state,
        **kwargs
    )

    print_log(f"  <<< [End Section] {section_name}")
    return section_name, translated_nodes

async def _run_phase4_async(
    phase2_state_path: str | Path,
    structure_state_path: str | Path,
    sections_state_path: str | Path,
    phase4_state_path: str | Path,
    glossary_path: str | None,
    api_key: str | None,
    save_state: bool = True,
    expertise: str = "文化人類学",
    model: str | None = None,
    thinking_level: str = "High",
    state: Any = None,
    tier: str = "paid",
    resume_only: bool = False,
    is_book: bool = False,
    book_resume: str = "",
    pdf_mode: str = "default",
    max_concurrent_sections: int = 4,
) -> List[TreeNode]:
    """Phase 4 メイン実行処理（オーケストレーター）。"""
    
    # 状態のロード
    with open(sections_state_path, "r", encoding="utf-8") as f:
        sections_dict: Dict[str, List[dict]] = json.load(f)
    with open(structure_state_path, "r", encoding="utf-8") as f:
        english_tree = [TreeNode.from_dict(d) for d in json.load(f)]

    # 設定と用語集のロード
    prompts = load_coreprompts()
    glossary_entries = load_glossary_entries(glossary_path)
    keywords_data = []
    resume_context = ""
    if Path(phase2_state_path).exists():
        with open(phase2_state_path, "r", encoding="utf-8") as f:
            p2_data = json.load(f)
            resume_context = p2_data.get("resume_content", "")
            keywords_data = p2_data.get("keywords_data", [])
    # 用語レイヤー: 本文抽出 ＋ glossary CSV を en→ja 対応表として統合（定義文は保持しない）
    term_entries = build_term_layer(keywords_data, glossary_entries)

    # エンジンの初期化
    current_tier = GeminiTier.FREE if tier.lower() == "free" else GeminiTier.PAID
    tier_manager.set_tier(current_tier)
    
    translator = ParallelTranslator(api_key=api_key, model=model, tier=current_tier,
                                     max_concurrent_sections=max_concurrent_sections)
    prompt_builder = TranslationPromptBuilder(prompts["TRANSLATION_PROMPT"], glossary=term_entries)
    reconstructor = TreeReconstructor(is_book=is_book, resume_only=resume_only)

    # 翻訳コンテキストの組み立て（全セクション共通・毎バッチの {resume_content} に注入）
    translation_context = build_translation_context(book_resume, resume_context, is_book)

    # セクションごとの並列処理
    tasks = []
    for section_name, chunks in sections_dict.items():
        if not chunks: continue
        tasks.append(process_section_modular(
            section_name, chunks, translation_context, translator, prompt_builder,
            is_book=is_book, state=state, expertise=expertise, thinking_level=thinking_level
        ))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 結果の集約
    translated_sections = {}
    for res in results:
        if isinstance(res, Exception):
            print_log(f"  [ERROR] セクション処理致命的失敗: {res}")
            continue
        sec_name, nodes = res
        translated_sections[sec_name] = nodes

    # ツリーの再構成
    japanese_tree = reconstructor.rebuild(english_tree, translated_sections)

    if save_state:
        with open(phase4_state_path, "w", encoding="utf-8") as f:
            json.dump([n.to_dict() for n in japanese_tree], f, ensure_ascii=False, indent=2)
            
    return japanese_tree

def run_phase4(
    phase2_state_path: str | Path,
    structure_state_path: str | Path,
    sections_state_path: str | Path,
    phase4_state_path: str | Path,
    glossary_path: str | None,
    **kwargs
) -> List[TreeNode]:
    """同期実行ラッパー。"""
    from .llm_client import run_async
    return run_async(_run_phase4_async(
        phase2_state_path, structure_state_path, sections_state_path, 
        phase4_state_path, glossary_path, **kwargs
    ))