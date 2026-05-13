import asyncio
import json
from pathlib import Path
from typing import List, Dict, Any

from .models import TreeNode
from .config import load_coreprompts, load_glossary_csv, print_log
from .llm_client import translate_batch, generate_section_resume, tier_manager, GeminiTier, apply_tier_settings

# アトミック・エンジンのインポート
from .engine.p4_translate.parallel_translator import ParallelTranslator
from .engine.p4_translate.prompt_builder import TranslationPromptBuilder
from .engine.p4_translate.tree_reconstructor import TreeReconstructor

async def process_section_modular(
    section_name: str,
    chunks: List[dict],
    resume_context: str,
    translator: ParallelTranslator,
    prompt_builder: TranslationPromptBuilder,
    is_book: bool = False,
    state: Any = None,
    **kwargs
):
    """
    セクション（章）単位の翻訳処理。
    要約生成、バッチ翻訳、および局所ツリー構築を管理する。
    """
    print_log(f"  >>> [Start Section] {section_name}")
    
    # 1. セクション要約の生成 (Book Mode のみ)
    resume_text = ""
    if is_book:
        # 既存のキャッシュ（existing_resume）の抽出
        existing_resume = None
        if chunks and isinstance(chunks[0], dict) and "existing_resume" in chunks[0]:
            existing_resume = chunks[0]["existing_resume"]
            chunks = chunks[1:]

        if existing_resume:
            resume_text = existing_resume
        else:
            resume_text = await generate_section_resume(
                section_name=section_name, chunks=chunks, resume_content=resume_context,
                api_key=translator.api_key, model=translator.model,
                rate_limiter=translator.rate_limiter, log_dir=state.logs_dir if state else None
            )

    # 2. 並列翻訳の実行
    translated_nodes = await translator.translate_section_chunks(
        section_name=section_name,
        chunks=chunks,
        prompt_builder_func=lambda nodes: prompt_builder.format_previous_translation(nodes),
        translate_func=translate_batch,
        prompt_template=prompt_builder.prompt_template,
        glossary_content=prompt_builder.format_glossary(),
        resume_content=resume_text,
        state=state,
        **kwargs
    )
    
    print_log(f"  <<< [End Section] {section_name}")
    return section_name, translated_nodes, resume_text

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
    master_glossary = load_glossary_csv(glossary_path)
    resume_context = ""
    if Path(phase2_state_path).exists():
        with open(phase2_state_path, "r", encoding="utf-8") as f:
            p2_data = json.load(f)
            resume_context = p2_data.get("resume_content", "")
            # DNA キーワードを用語集に統合
            for kw in p2_data.get("keywords_data", []):
                if kw.get("en") and kw["en"] not in master_glossary:
                    master_glossary[kw["en"]] = kw.get("ja", "")

    # エンジンの初期化
    current_tier = GeminiTier.FREE if tier.lower() == "free" else GeminiTier.PAID
    tier_manager.set_tier(current_tier)
    
    translator = ParallelTranslator(api_key=api_key, model=model, tier=current_tier,
                                     max_concurrent_sections=max_concurrent_sections)
    prompt_builder = TranslationPromptBuilder(prompts["TRANSLATION_PROMPT"], glossary=master_glossary)
    reconstructor = TreeReconstructor(is_book=is_book, resume_only=resume_only)

    # セクションごとの並列処理
    tasks = []
    for section_name, chunks in sections_dict.items():
        if not chunks: continue
        tasks.append(process_section_modular(
            section_name, chunks, resume_context, translator, prompt_builder,
            is_book=is_book, state=state, expertise=expertise, thinking_level=thinking_level
        ))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 結果の集約
    translated_sections = {}
    section_resumes = {}
    for res in results:
        if isinstance(res, Exception):
            print_log(f"  [ERROR] セクション処理致命的失敗: {res}")
            continue
        sec_name, nodes, resume = res
        translated_sections[sec_name] = nodes
        section_resumes[sec_name] = resume

    # ツリーの再構成
    japanese_tree = reconstructor.rebuild(english_tree, translated_sections, section_resumes)

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