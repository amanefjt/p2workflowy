"""
p2workflowy V2 Phase 4: Sliding-Window Translation
LLM を用いたセクション別の非同期サイトトランスレーション。
"""

import asyncio
import json
import random
from pathlib import Path
from typing import List, Dict, Any

from aiolimiter import AsyncLimiter

from .config import (
    load_coreprompts, load_glossary_csv,
    print_log
)
from .models import TreeNode, save_tree_to_json
from .llm_client import call_gemini_async


# ============================================================
# グローバル設定
# ============================================================
# Trial C: 1回あたりのバッチサイズ（文字数上限を緩和してリクエスト数を削減）
MAX_BATCH_CHUNKS = 20
MAX_BATCH_CHARS = 6000

# 1分間に15リクエストの制限
rate_limiter = AsyncLimiter(15, 60)


# ============================================================
# テキスト構築ヘルパー
# ============================================================

def format_glossary(glossary: dict) -> str:
    """用語集辞書をプロンプト埋め込み用の文字列に変換する。"""
    if not glossary:
        return "なし"
    lines = [f"- {en}: {ja}" for en, ja in glossary.items()]
    return "\n".join(lines)


def format_previous_translation(previous_nodes: List[TreeNode]) -> str:
    """
    直前の翻訳結果（Sliding Window用コンテキスト）を文字列化する。
    """
    if not previous_nodes:
        return "なし（セクション先頭）"
    
    # 直前のバッチ（最大3チャンク）のテキストを取得して結合
    nodes_list = list(previous_nodes)
    recent_nodes = nodes_list[-3:] if len(nodes_list) >= 3 else nodes_list
    return "\n\n".join([node.text for node in recent_nodes])


# ============================================================
# 翻訳メインロジック (非同期)
# ============================================================

async def translate_batch(
    chunks: List[dict],
    glossary_content: str,
    previous_translation: str,
    prompt_template: str,
    resume_content: str,
    section_name: str = "Unknown",
    api_key: str | None = None,
    expertise: str = "文化人類学",
    model: str | None = None,
    thinking_level: str = "High",
) -> List[TreeNode]:
    """
    動的バッチ（Trial C: 最大6000文字/20チャンク）を一度の API コールで翻訳する。
    """
    # JSONコンフリクト文字のサニタイズ（Geminiの構文解析エラー防止）とIDベーススキーマ化
    sanitized_chunks =[]
    for chunk in chunks:
        c_copy = chunk.copy()
        t = c_copy.get("text", "")
        # 置換ルール
        t = t.replace('\\"', '”')
        t = t.replace('"', '”')
        t = t.replace("'", '’')
        t = t.replace('[', '［')
        t = t.replace(']', '］')
        t = t.replace('(', '（')
        t = t.replace(')', '）')
        
        sanitized_chunks.append({
            "id": str(c_copy.get("id")),
            "en": t
        })

    input_json = json.dumps(sanitized_chunks, ensure_ascii=False)

    prompt = prompt_template.replace(
        "{expertise}", expertise
    ).replace(
        "{context_guide}", ""
    ).replace(
        "{resume_content}", resume_content
    ).replace(
        "{glossary_content}", glossary_content
    ).replace(
        "{previous_translation}", previous_translation
    ).replace(
        "{chunk_json}", input_json
    )

    # ログ用チャンクID文字列
    chunk_ids = [c.get("id") for c in chunks]
    chunk_ids_str = f"{min(chunk_ids)}-{max(chunk_ids)}" if chunk_ids else "N/A"

    # メトリクス用のメタデータ
    metrics_metadata = {
        "section": section_name,
        "batch_id": chunk_ids_str
    }

    max_retries = 5
    translated_dict = {}

    response_schema = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "id": {"type": "STRING"},
                "ja": {"type": "STRING"}
            },
            "required": ["id", "ja"]
        }
    }

    for attempt in range(max_retries):
        try:
            # 流量制限 (AsyncLimiter)
            async with rate_limiter:
                input_chars = len(input_json)
                print_log(f"  [Phase 4] API Request: {section_name} (Chunks: {chunk_ids_str}, JSON chars: {input_chars})")

                # timeout は llm_client 側の設定に任せる
                response_text = await call_gemini_async(
                    prompt=prompt,
                    api_key=api_key,
                    temperature=0.3,
                    response_mime_type="application/json",
                    # 性能と安定性の向上のためスキーマ強制を解除（No Schema 戦略）
                    # response_schema=response_schema,
                    max_retries=1,
                    metrics_metadata=metrics_metadata,
                    model=model,
                    thinking_level=thinking_level,
                )
            
            # response_schema を解除したため、Markdown 形式 (```json ... ```) への耐性を高める
            import re
            json_match = re.search(r'(\[.*\])', response_text, re.DOTALL)
            if json_match:
                response_text = json_match.group(1)
            
            data = json.loads(response_text)
            
            if not isinstance(data, list):
                raise ValueError("出力がJSON配列ではありません。")
            
            # IDによるバリデーションとマッピング
            temp_dict = {str(item.get("id")): item.get("ja", "") for item in data if isinstance(item, dict)}
            
            missing_ids =[]
            for c in chunks:
                str_id = str(c.get("id"))
                if str_id not in temp_dict:
                    missing_ids.append(str_id)
                    
            if missing_ids:
                raise ValueError(f"ID欠損: {missing_ids}")
                
            print_log(f"  [Phase 4] API Response: Success ({len(data)} items received, all IDs matched)")
            translated_dict = temp_dict
            break  # 成功

        except Exception as e:
            print_log(f"  [Phase 4] 翻訳/バリデーションエラー [{section_name} Chunks:{chunk_ids_str}] (試行 {attempt + 1}/{max_retries}): {type(e).__name__}: {e}")
            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) + random.uniform(1.0, 3.0)
                await asyncio.sleep(wait_time)

    translated_nodes =[]
    if translated_dict:
        for chunk in chunks:
            str_id = str(chunk.get("id"))
            translated_nodes.append(TreeNode(
                id=chunk.get("id"),
                text=translated_dict.get(str_id, ""),
                role="p",
                seq_index=chunk.get("seq_index", 0.0)
            ))
    else:
        # 最終的に失敗した場合（リトライ上限到達など）、エラーメッセージ付きで元のテキストを返す
        for chunk in chunks:
            translated_nodes.append(TreeNode(
                id=chunk.get("id"),
                text=f"【翻訳エラー: IDマッピング失敗/タイムアウト】\n{chunk.get('text', '')}",
                role="p",
                seq_index=chunk.get("seq_index", 0.0)
            ))

    return translated_nodes


async def process_section(
    section_name: str,
    chunks: List[dict],
    resume_content: str,
    master_glossary: dict,
    prompt_template: str,
    progress_state: List[int],
    semaphore: asyncio.Semaphore,
    api_key: str | None = None,
    expertise: str = "文化人類学",
    model: str | None = None,
    thinking_level: str = "High",
    state: Any = None,
) -> tuple[str, List[TreeNode]]:
    """
    セクション内のチャンクを動的にバッチ化して翻訳を進める。
    """
    translated_nodes: List[TreeNode] =[]
    chunk_list = list(chunks)

    # 用語集は常に全件プロンプトに注入する
    glossary_content = format_glossary(master_glossary)

    # セクション間並列のためのセマフォ
    async with semaphore:
        i = 0
        while i < len(chunk_list):
            batch_chunks: List[dict] = []
            batch_chars: int = 0
            
            # 1回あたりのバッチサイズ（MAX_BATCH_CHUNKS, MAX_BATCH_CHARS を使用）
            while i < len(chunk_list) and len(batch_chunks) < MAX_BATCH_CHUNKS:
                next_chunk = chunk_list[i]
                chunk_text = next_chunk.get("text", "")
                chunk_len = len(chunk_text)
                
                if len(batch_chunks) > 0 and (batch_chars + chunk_len) > MAX_BATCH_CHARS:
                    break
                    
                batch_chunks.append(next_chunk)
                batch_chars += chunk_len
                i += 1
            
            # Sliding Window コンテキストを取得
            previous_translation = format_previous_translation(translated_nodes)
            
            # API側の渋滞を防ぐために微小ディレイを挟む（直列のため短縮）
            await asyncio.sleep(random.uniform(0.5, 1.5))

            batch_translated_nodes = await translate_batch(
                chunks=batch_chunks,
                glossary_content=glossary_content,
                previous_translation=previous_translation,
                prompt_template=prompt_template,
                resume_content=resume_content,
                section_name=section_name,
                api_key=api_key,
                expertise=expertise,
                model=model,
                thinking_level=thinking_level,
            )
            
            translated_nodes.extend(batch_translated_nodes)
                
            # プログレス更新
            progress_state[0] += len(batch_chunks)
            curr = progress_state[0]
            total = progress_state[1]
            print_log(f"  [Phase 4] 翻訳進捗: {curr}/{total} チャンク")
            if state:
                # 70% から 90% の間で進捗を表示
                percent = 70 + int((curr / total) * 20)
                state.update_status(f"Phase 4: Translating ({curr}/{total} chunks)...", percent)

    return section_name, translated_nodes


# ============================================================
# ツリー再構築
# ============================================================

def rebuild_translated_tree(
    english_tree: List[TreeNode],
    translated_sections: Dict[str, List[TreeNode]]
) -> List[TreeNode]:
    """
    元の英語ツリー構造を維持したまま、子ノード（段落）を日本語翻訳済みのノードに差し替える。
    再帰的に処理を行い、H3 などの階層を維持する。
    """
    japanese_tree: List[TreeNode] =[]

    for en_node in english_tree:
        section_name = en_node.text
        
        # 翻訳済みノードを ID で逆引きするためのマップを作成 (h2 セクション単位)
        translated_pool = translated_sections.get(section_name,[])
        id_to_ja_node = {node.id: node for node in translated_pool}

        def _recursive_rebuild(en_subnode: TreeNode) -> TreeNode:
            # 新しいノード（日本語版）を作成
            ja_subnode = TreeNode(
                id=en_subnode.id,
                text=en_subnode.text,
                role=en_subnode.role,
                seq_index=en_subnode.seq_index,
                children=[]
            )

            if en_subnode.role == "p":
                # p ノードは翻訳済みのテキストに差し替え
                if en_subnode.id in id_to_ja_node:
                    ja_subnode.text = id_to_ja_node[en_subnode.id].text
            
            # 子ノードを再帰的に処理
            if en_subnode.children:
                for child in en_subnode.children:
                    ja_subnode.children.append(_recursive_rebuild(child))
            
            return ja_subnode

        japanese_tree.append(_recursive_rebuild(en_node))

    return japanese_tree


# ============================================================
# メイン実行関数
# ============================================================

async def _run_phase4_async(
    phase2_state_path: str | Path,
    structure_state_path: str | Path,
    sections_state_path: str | Path,
    phase4_state_path: str | Path,
    glossary_path: str | None,
    api_key: str | None,
    save_state: bool,
    expertise: str = "文化人類学",
    model: str | None = None,
    thinking_level: str = "High",
    state: Any = None,
) -> List[TreeNode]:
    """非同期メイン実行処理"""
    phase2_state_path = Path(phase2_state_path)
    structure_state_path = Path(structure_state_path)
    sections_state_path = Path(sections_state_path)

    if not sections_state_path.exists() or not structure_state_path.exists():
        raise FileNotFoundError("Phase 3 の出力が見つかりません。")
    
    with open(sections_state_path, "r", encoding="utf-8") as f:
        sections_dict: Dict[str, List[dict]] = json.load(f)
        
    with open(structure_state_path, "r", encoding="utf-8") as f:
        english_tree_data = json.load(f)
        english_tree =[TreeNode.from_dict(d) for d in english_tree_data]

    prompts = load_coreprompts()
    prompt_template = prompts["TRANSLATION_PROMPT"]
    master_glossary = load_glossary_csv(glossary_path)

    # 全チャンク数
    total_chunks = sum(len(chunks) for chunks in sections_dict.values())
    progress_state =[0, total_chunks]

    print_log(f"  [Phase 4] 翻訳対象セクション: {len(sections_dict)} 件")
    
    # Trial C: 並列処理の再解禁 (Semaphore 4)
    semaphore = asyncio.Semaphore(4)
    tasks = []
    
    # 1. 語彙データの統合読み込み
    master_glossary = load_glossary_csv(glossary_path)
    
    # phase2_meta.json から AI 抽出語彙をマージ 
    if phase2_state_path.exists():
        with open(phase2_state_path, "r", encoding="utf-8") as f:
            phase2_data = json.load(f)
            ai_keywords = phase2_data.get("keywords_data", [])
            for kw in ai_keywords:
                en = kw.get("en", "").strip()
                ja = kw.get("ja", "").strip()
                if en and en not in master_glossary:
                    master_glossary[en] = ja
        print_log(f"  [Phase 4] AI語彙をマージしました。合計語彙数: {len(master_glossary)}")

    # 2. 全体要約の読み込み
    resume_content = ""
    if phase2_state_path.exists():
        with open(phase2_state_path, "r", encoding="utf-8") as f:
            resume_data = json.load(f)
            resume_content = resume_data.get("resume_content", "")


    # 各セクションへのタスク作成
    print_log("  [Phase 4] 翻訳処理実行中 (Parallel Execution)...")
    for section_name, chunks in sections_dict.items():
        if not chunks:
            continue
        tasks.append(process_section(
            section_name=section_name,
            chunks=chunks,
            resume_content=resume_content,
            master_glossary=master_glossary,
            prompt_template=prompt_template,
            progress_state=progress_state,
            semaphore=semaphore,
            api_key=api_key,
            expertise=expertise,
            model=model, thinking_level=thinking_level,
            state=state,
        ))
    
    results = await asyncio.gather(*tasks)
    
    # 結果の集約
    translated_sections: Dict[str, List[TreeNode]] = {}
    total_translated_chunks = 0
    for section_name, translated_nodes in results:
        translated_sections[section_name] = translated_nodes
        total_translated_chunks += len(translated_nodes)
        print_log(f"  [Phase 4] セクション '{section_name}' 完了 ({len(translated_nodes)} チャンク)")

    # 日本語ツリーの再構築
    japanese_tree = rebuild_translated_tree(english_tree, translated_sections)

    if save_state:
        save_tree_to_json(japanese_tree, str(phase4_state_path))
        print_log(f"  [Phase 4] 日本語ツリー保存: {phase4_state_path}")

    return japanese_tree


def run_phase4(
    phase2_state_path: str | Path,
    structure_state_path: str | Path,
    sections_state_path: str | Path,
    phase4_state_path: str | Path,
    glossary_path: str | None = None,
    api_key: str | None = None,
    save_state: bool = True,
    expertise: str = "文化人類学",
    model: str | None = None,
    thinking_level: str = "High",
    state: Any = None,
) -> List[TreeNode]:
    """
    Phase 4 メイン処理（同期ラッパー）
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    return loop.run_until_complete(_run_phase4_async(
        phase2_state_path, structure_state_path, sections_state_path, 
        phase4_state_path, glossary_path, api_key, save_state,
        expertise=expertise, model=model, thinking_level=thinking_level,
        state=state
    ))