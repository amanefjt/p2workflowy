import asyncio
import json
import random
from pathlib import Path
from typing import List, Dict, Any, Tuple
from aiolimiter import AsyncLimiter

# 相対インポートに修正
from .models import TreeNode
from .config import load_coreprompts, load_glossary_csv, print_log, STATE_DIR
from .llm_client import translate_batch, generate_section_resume, tier_manager, GeminiTier, apply_tier_settings
from .phase3_structure import structure_nodes_by_headings, extract_headings_from_resume

# デフォルト設定 (Tiers で上書きされる)
DEFAULT_MAX_BATCH_CHUNKS = 5
DEFAULT_MAX_BATCH_CHARS = 3000



def format_glossary(glossary: Dict[str, str]) -> str:
    """用語集をプロンプト用に整形"""
    if not glossary:
        return ""
    lines = ["以下はプロジェクト固有の用語集です。翻訳時に優先的に適用してください。"]
    for en, ja in glossary.items():
        lines.append(f"- {en}: {ja}")
    return "\n".join(lines)

def format_previous_translation(nodes: List[TreeNode], max_nodes: int = 3) -> str:
    """以前の翻訳結果をプロンプト用に整形する"""
    if not nodes:
        return ""
    recent = nodes[-max_nodes:]
    lines = []
    for n in recent:
        if n.role == "p":
            lines.append(f"- {n.text}")
    return "\n".join(lines)

async def process_section(
    section_name: str,
    chunks: List[dict],
    resume_content: str,
    master_glossary: dict,
    prompt_template: str,
    progress_state: List[int],
    semaphore: asyncio.Semaphore,
    rate_limiter: AsyncLimiter,
    settings: dict,
    api_key: str | None = None,
    expertise: str = "文化人類学",
    model: str | None = None,
    thinking_level: str = "High",
    state: Any = None,
    resume_only: bool = False,
    is_book: bool = False,
    pdf_mode: str = "default",
) -> Tuple[str, Any, List[TreeNode], str] | Tuple[str, List[TreeNode], str]:
    """
    セクション内のチャンクを動的にバッチ化して翻訳を進める。
    """
    translated_nodes: List[TreeNode] = []
    
    # 用語集は常に全件プロンプトに注入する
    glossary_content = format_glossary(master_glossary)

    # セクション間並列のためのセマフォ
    async with semaphore:
        print_log(f"  >>> [Start Section] {section_name}")
        # --- 要約生成 (書籍モードのみ) ---
        resume_text = ""
        if is_book:
            # --- 修正: センチネル辞書の抽出と、chunksからの確実な除去 ---
            # _run_phase4_async で先頭に挿入された {"existing_resume": ...} を取り出し、リストを純格化する
            pre_existing_resume = None
            if chunks and isinstance(chunks[0], dict) and "existing_resume" in chunks[0]:
                pre_existing_resume = chunks[0]["existing_resume"]
                chunks = chunks[1:]  # 異物を除去し、純粋なデータチャンクのみに戻す

            # 既存の下流ロジック（resume_only の判定等）をそのまま活かすための変数代入
            existing_resume = pre_existing_resume
            # ------------------------------------------------------------
            
            # [修正箇所]: キャッシュがあればそれを使用、なければ生成する
            if existing_resume:
                resume_text = existing_resume
            else:
                resume_text = await generate_section_resume(
                    section_name=section_name,
                    chunks=chunks,
                    resume_content=resume_content,
                    api_key=api_key,
                    model=model,
                    expertise=expertise,
                    rate_limiter=rate_limiter,
                    log_dir=state.logs_dir if state else None
                )

        # --- Resume Only モード ---
        if resume_only:
            # Book Mode の場合、resume_only でも構造化だけは行って返す（ユーザー要望）
            if is_book:
                chunk_nodes = [
                    TreeNode(id=c["id"], text=c["text"], role=c.get("role", "p"), seq_index=c.get("seq_index", 0.0))
                    for c in chunks
                ]
                if pdf_mode == "full_vlm":
                    # Route C: すでに Phase 3 で構造化されているため、そのままの構造を維持
                    ch_tree: List[TreeNode] = []
                    current_h3 = None
                    for node in chunk_nodes:
                        if node.role.startswith("h"):
                            current_h3 = node
                            ch_tree.append(node)
                        else:
                            if current_h3:
                                current_h3.children.append(node)
                            else:
                                ch_tree.append(node)
                    result_tree = ch_tree
                else:
                    ch_headings = extract_headings_from_resume(resume_text)
                    prompts_data = load_coreprompts()
                    exclude_keywords = prompts_data.get("EXCLUDE_SECTION_KEYWORDS", [])
                    result_tree, _ = structure_nodes_by_headings(chunk_nodes, ch_headings, exclude_keywords)
                
                if state:
                    progress_state[0] += len(chunks)
                    curr, total = progress_state
                    state.update_status("構造化完了...", 70 + int((curr / max(total, 1)) * 20))
                return section_name, result_tree, result_tree, resume_text
            else:
                result_nodes: List[TreeNode] = []
                for chunk in chunks:
                    result_nodes.append(TreeNode(
                        id=chunk.get("id"),
                        text=chunk.get("text", ""),
                        role="p",
                        seq_index=chunk.get("seq_index", 0.0),
                    ))
                progress_state[0] += len(chunks)
                if state:
                    curr, total = progress_state
                    state.update_status("要約生成中...", 70 + int((curr / max(total, 1)) * 20))
                return section_name, result_nodes, resume_text

        # --- 本文翻訳ループ ---
        if is_book:
            # 1. チャンクをTreeNodeに変換し、レジュメ見出しで構造化
            chunk_nodes = [
                TreeNode(id=c["id"], text=c["text"], role=c.get("role", "p"), seq_index=c.get("seq_index", 0.0))
                for c in chunks
            ]
            
            if pdf_mode == "full_vlm":
                # Route C: Phase 3 (sections_dict) に格納された role をそのまま信頼する
                print_log(f"  [Phase 4] Route C (full_vlm) のため、レジュメ抽出による再構造化をスキップします。")
                ch_tree: List[TreeNode] = []
                current_h3 = None
                for node in chunk_nodes:
                    if node.role.startswith("h"):
                        current_h3 = node
                        ch_tree.append(node)
                    else:
                        if current_h3:
                            current_h3.children.append(node)
                        else:
                            ch_tree.append(node)
            else:
                # Route A/B: 古いロジック（レジュメから見出しを抽出して無理やり構造化）
                ch_headings = extract_headings_from_resume(resume_text)
                prompts_data = load_coreprompts()
                exclude_keywords = prompts_data.get("EXCLUDE_SECTION_KEYWORDS", [])
                # 章内のツリー構造（見出しh3と段落pが混在した状態）を作成
                ch_tree, _ = structure_nodes_by_headings(chunk_nodes, ch_headings, exclude_keywords)

            # BUG-001 修正3: [Unlabeled Section] のクリーンアップ
            # LLM が見出しを意訳した場合、match_heading が全て失敗し、全段落が
            # 単一の [Unlabeled Section] に格納されてしまう。
            # また、一部だけ Unlabeled になった場合も、直下に p があるなら不自然な階層を作らない。
            new_ch_tree = []
            for node in ch_tree:
                if node.text == "[Unlabeled Section]" and node.children:
                    # 中身が空でなければ、ラッパーを外して中身を昇格させる
                    new_ch_tree.extend(node.children)
                else:
                    new_ch_tree.append(node)
            ch_tree = new_ch_tree

            # 2. 翻訳対象の抽出（Flatten）
            # 見出しノード(h3)自体は翻訳せず英語のまま残す。
            # その子ノード(p)と、見出しに属さない独立したpノードだけを抽出する。
            flat_chunks: List[dict] = []
            for node in ch_tree:
                if node.role.startswith("h"):
                    for child in node.children:
                        flat_chunks.append({
                            "id": child.id,
                            "text": child.text,
                            "seq_index": child.seq_index,
                        })
                else:
                    flat_chunks.append({
                        "id": node.id,
                        "text": node.text,
                        "seq_index": node.seq_index,
                    })

            # 3. Paper Modeと完全に同じロジックでの一括シーケンシャル翻訳（Translate）
            all_translated: List[TreeNode] = []
            i = 0
            max_chunks = settings.get("max_batch_chunks", DEFAULT_MAX_BATCH_CHUNKS)
            max_chars = settings.get("max_batch_chars", DEFAULT_MAX_BATCH_CHARS)
            
            # [強化] バッチ総数の計算
            total_batches = (len(flat_chunks) + max_chunks - 1) // max_chunks
            batch_count = 0

            while i < len(flat_chunks):
                batch: List[dict] = []
                batch_chars = 0
                while i < len(flat_chunks) and len(batch) < max_chunks:
                    c = flat_chunks[i]
                    c_len = len(c.get("text", ""))
                    if len(batch) > 0 and (batch_chars + c_len) > max_chars:
                        break
                    batch.append(c)
                    batch_chars += c_len
                    i += 1

                batch_count += 1
                if tier_manager.was_downgraded and tier_manager.current_tier == GeminiTier.FREE:
                    rate_limiter, _, settings = apply_tier_settings(GeminiTier.FREE)

                previous = format_previous_translation(all_translated)
                # [強化] セクション内バッチ進捗の出力
                print_log(f"  [Section: {section_name}] Processing Batch {batch_count}/{total_batches} ({len(batch)} chunks)")
                await asyncio.sleep(random.uniform(0.5, 1.5))
                
                try:
                    batch_nodes = await translate_batch(
                        chunks=batch,
                        glossary_content=glossary_content,
                        previous_translation=previous,
                        prompt_template=prompt_template,
                        resume_content=resume_text,
                        section_name=section_name,
                        api_key=api_key,
                        expertise=expertise,
                        model=model,
                        thinking_level=thinking_level,
                        state=state,
                        rate_limiter=rate_limiter,
                        log_dir=state.logs_dir if state else None
                    )
                except Exception as e:
                    print_log(f"  [ERROR] translate_batch 失敗 ({i-len(batch)} to {i}): {e}")
                    # 失敗した場合は原文を維持したフォールバックノードを作成
                    batch_nodes = []
                    for c in batch:
                        batch_nodes.append(TreeNode(
                            id=c["id"],
                            text=f"[翻訳失敗] {c['text']}",
                            role="p",
                            seq_index=c.get("seq_index", 0.0)
                        ))
                all_translated.extend(batch_nodes)
                
                # 進捗更新
                if state:
                    progress_state[0] += len(batch)
                    curr, total = progress_state
                    state.update_status("本文を翻訳中...", 70 + int((curr / max(total, 1)) * 20))

            print_log(f"  <<< [End Section] {section_name}")
            # 4. 翻訳結果の差し戻し（Unflatten）
            # 翻訳されたノードをIDをキーにして辞書化
            id_to_ja: dict[str, TreeNode] = {str(n.id): n for n in all_translated}

            result_nodes: List[TreeNode] = []
            for node in ch_tree:
                if node.role.startswith("h"):
                    # 見出しノードは新たに作り直して英語のまま保持
                    new_h = TreeNode(
                        id=node.id, text=node.text, role="h3",
                        seq_index=node.seq_index, children=[]
                    )
                    # 子ノード（翻訳済み）を紐付け
                    for child in node.children:
                        ja_child = id_to_ja.get(str(child.id))
                        if ja_child:
                            new_h.children.append(ja_child)
                        else:
                            # 翻訳欠落時のフォールバック
                            new_h.children.append(TreeNode(
                                id=child.id,
                                text=f"[翻訳欠落] {child.text}",
                                role="p",
                                seq_index=child.seq_index
                            ))
                    result_nodes.append(new_h)
                else:
                    # 独立したpノード（翻訳済み）
                    ja_node_p = id_to_ja.get(str(node.id))
                    if ja_node_p:
                        result_nodes.append(ja_node_p)
                    else:
                        # 翻訳欠落時のフォールバック
                        result_nodes.append(TreeNode(
                            id=node.id,
                            text=f"[翻訳欠落] {node.text}",
                            role="p",
                            seq_index=node.seq_index
                        ))

            return section_name, ch_tree, result_nodes, resume_text

        else:
            # Paper Mode
            i = 0
            while i < len(chunks):
                batch = []
                batch_chars = 0
                max_chunks = settings.get("max_batch_chunks", DEFAULT_MAX_BATCH_CHUNKS)
                max_chars = settings.get("max_batch_chars", DEFAULT_MAX_BATCH_CHARS)
                
                while i < len(chunks) and len(batch) < max_chunks:
                    c = chunks[i]
                    c_text = c.get("text", "")
                    c_len = len(c_text)
                    if len(batch) > 0 and (batch_chars + c_len) > max_chars:
                        break
                    batch.append(c)
                    batch_chars += c_len
                    i += 1
                
                if tier_manager.was_downgraded and tier_manager.current_tier == GeminiTier.FREE:
                    rate_limiter, _, settings = apply_tier_settings(GeminiTier.FREE)
                
                context_guide = "本文としての完結性を重視し、各段落を正確かつ論理的な日本語に翻訳してください。"
                previous = format_previous_translation(translated_nodes)
                await asyncio.sleep(random.uniform(0.5, 1.5))
                batch_nodes = await translate_batch(
                    chunks=batch,
                    glossary_content=glossary_content,
                    previous_translation=previous,
                    prompt_template=prompt_template,
                    resume_content=resume_content,
                    section_name=section_name,
                    api_key=api_key,
                    expertise=expertise,
                    model=model,
                    thinking_level=thinking_level,
                    state=state,
                    rate_limiter=rate_limiter,
                    context_guide=context_guide,
                    log_dir=state.logs_dir if state else None
                )
                translated_nodes.extend(batch_nodes)
                progress_state[0] += len(batch)
                if state:
                    curr, total = progress_state
                    state.update_status(f"本文を翻訳中...", 70 + int((curr / max(total, 1)) * 20))

            return section_name, translated_nodes, resume_text


def rebuild_translated_tree(
    english_tree: List[TreeNode],
    translated_sections: Dict[str, List[TreeNode]],
    section_resumes: Dict[str, str] = {},
    resume_only: bool = False,
    is_book: bool = False,
) -> List[TreeNode]:
    """元の英語ツリー構造を維持したまま、子ノード（段落）を日本語翻訳済みのノードに差し替える。"""
    japanese_tree: List[TreeNode] = []
    for en_node in english_tree:
        section_key = en_node.text
        translated_pool = translated_sections.get(section_key)
        
        # 1段階目のフォールバック: IDを用いた検索
        if translated_pool is None:
            node_id_str = str(en_node.id)
            for k, pool in translated_sections.items():
                if k.startswith(f"{node_id_str}|"):
                    translated_pool = pool
                    section_key = k
                    break
        # [修正箇所]: 翻訳失敗セクションを安全にスキップ
        if translated_pool is None:
            print_log(f"  [rebuild] 警告: 翻訳データなし（スキップ）: {en_node.text[:40]}")
            continue

        ch_resume = section_resumes.get(section_key, "")
        ch_headings = extract_headings_from_resume(ch_resume) if is_book else []
        safe_id_suffix = str(en_node.id)
        
        resume_nodes: List[TreeNode] = []
        if resume_only or (is_book and ch_resume):
            resume_text = ch_resume if is_book else ""
            if not resume_text:
                 resume_nodes = [n for n in translated_pool if str(n.id).startswith("resume_0_")]
            else:
                 resume_nodes = [TreeNode(
                     id=f"resume_0_{safe_id_suffix}",
                     text=resume_text,
                     role="h3",
                     seq_index=-1.0
                 )]

        if is_book:
            ja_node = TreeNode(
                id=en_node.id, text=en_node.text, role="h2", seq_index=en_node.seq_index,
                children=[],
                metadata={"summary": ch_resume}
            )
            # ch_resume は metadata["summary"] に格納されるため、
            # resume_nodes を ja_node.children に追加すると、
            # Phase 5 の generate_resume_only_output 等で二重に出力されてしまう。
            # したがって、ここでは children には追加しない。

            if not resume_only:
                # --- [修正箇所] 構造化済み英語ツリーを型紙にして日本語ツリーを再構築 ---
                source_paragraphs = en_node.children  # すでに Phase 4 内で h3 構造化済み
                
                # 翻訳済みノードのプールから再帰的にpノードのIDマップを作成
                # translated_pool が h3>p 構造の場合、フラットな内包表記では
                # h3 の子である p ノードの ID を見落とす。再帰で全階層を収集する。
                id_to_ja: Dict[str, TreeNode] = {}
                def _collect_p_ids(nodes: List[TreeNode]) -> None:
                    for n in nodes:
                        if str(n.id).startswith("resume_0_"):
                            continue
                        if n.role == "p":
                            id_to_ja[str(n.id)] = n
                        if n.children:
                            _collect_p_ids(n.children)
                _collect_p_ids(translated_pool)

                def _recursive_rebuild(en_sub: TreeNode) -> TreeNode:
                    """英語のツリー構造(en_sub)を型紙にして、テキストを日本語に差し替えた新ノードを返す。"""
                    # 基本情報をコピー（ID, role, seq_index）
                    ja_sub = TreeNode(
                        id=en_sub.id, 
                        text=en_sub.text, 
                        role=en_sub.role, 
                        seq_index=en_sub.seq_index, 
                        children=[],
                        metadata=en_sub.metadata.copy() # メタデータを継承
                    )
                    
                    # 段落(p)の場合は日本語テキストに差し替え（存在すれば）
                    if en_sub.role == "p":
                        str_id = str(en_sub.id)
                        if str_id in id_to_ja:
                            ja_sub.text = id_to_ja[str_id].text
                    
                    # 再帰的に子ノード（h3内部のpなど）を処理
                    if en_sub.children:
                        for child in en_sub.children:
                            ja_sub.children.append(_recursive_rebuild(child))
                    
                    return ja_sub

                # A. 英語側のラッパー構築 (h3 "English text")
                # Phase 5 のエクスポート処理がこのラッパーを期待しているため。
                english_wrapper = TreeNode(
                    id=f"en_wrap_{en_node.id}",
                    text="English text",
                    role="h3",
                    children=source_paragraphs,
                    seq_index=en_node.seq_index - 0.1 # 翻訳より前に配置
                )
                ja_node.children.append(english_wrapper)

                # B. 日本語ツリーの構築 (英語ツリーを型紙にして翻訳を流し込む)
                ja_children_structured = [_recursive_rebuild(c) for c in source_paragraphs]
                ja_node.children.extend(ja_children_structured)
                # ----------------------------------------------------------------------
            else:
                # resume_only = True の場合
                # 英語段落をラップして追加
                english_wrapper = TreeNode(
                    id=f"en_wrap_{en_node.id}",
                    text="English text",
                    role="h3",
                    children=en_node.children,
                    seq_index=en_node.seq_index - 0.1
                )
                ja_node.children.append(english_wrapper)

            japanese_tree.append(ja_node)
        else:
            # Paper Mode
            id_to_ja_node = {str(node.id): node for node in translated_pool if not str(node.id).startswith("resume_0_")}
            def _recursive_rebuild(en_subnode: TreeNode) -> TreeNode:
                ja_subnode = TreeNode(
                    id=en_subnode.id, text=en_subnode.text, role=en_subnode.role,
                    seq_index=en_subnode.seq_index, children=[]
                )
                if en_subnode.role == "p":
                    str_id = str(en_subnode.id)
                    if str_id in id_to_ja_node:
                        ja_subnode.text = id_to_ja_node[str_id].text
                if en_subnode.children:
                    for child in en_subnode.children:
                        ja_subnode.children.append(_recursive_rebuild(child))
                return ja_subnode

            ja_node = _recursive_rebuild(en_node)
            # Paper Mode では本文中に要約を挿入しない（要約はフルレジュメ側にのみ表示）
            # もし resume_only であれば、レジュメノードのみを追加するなどの調整が必要だが、
            # 現在の resume_only は rebuild_translated_tree の外側（process_section）でも制御されているため
            # ここではシンプルに Body のみを生成する。
            if resume_only:
                ja_node.children = resume_nodes + ja_node.children

            if ch_resume:
                ja_node.metadata["summary"] = ch_resume
            japanese_tree.append(ja_node)

    return japanese_tree

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
    tier: str = "paid",
    resume_only: bool = False,
    is_book: bool = False,
    pdf_mode: str = "default",
) -> List[TreeNode]:
    """非同期メイン実行処理"""
    phase2_state_path = Path(phase2_state_path)
    structure_state_path = Path(structure_state_path)
    sections_state_path = Path(sections_state_path)
    
    with open(sections_state_path, "r", encoding="utf-8") as f:
        sections_dict: Dict[str, List[dict]] = json.load(f)
    with open(structure_state_path, "r", encoding="utf-8") as f:
        english_tree_data = json.load(f)
        english_tree = [TreeNode.from_dict(d) for d in english_tree_data]

    prompts = load_coreprompts()
    prompt_template = prompts["TRANSLATION_PROMPT"]
    master_glossary = load_glossary_csv(glossary_path)

    if tier.lower() == "free":
        initial_tier = GeminiTier.FREE
    else:
        initial_tier = GeminiTier.PAID
    
    tier_manager.set_tier(initial_tier)
    rate_limiter, semaphore, settings = apply_tier_settings(tier_manager.current_tier)

    # 既存の翻訳結果（特に書籍モードのセクション要約）があれば読み込む
    existing_resumes: Dict[str, str] = {}
    if phase4_state_path.exists():
        try:
            with open(phase4_state_path, "r", encoding="utf-8") as f:
                old_data = json.load(f)
                # old_data は TreeNode のリスト。章(h2)の metadata["summary"] を探す
                for node_dict in old_data:
                    if node_dict.get("role") == "h2" and "metadata" in node_dict:
                        sum_val = node_dict["metadata"].get("summary")
                        if sum_val:
                            existing_resumes[node_dict.get("text", "")] = sum_val
        except:
            pass

    total_chunks = sum(len(chunks) for chunks in sections_dict.values())
    progress_state = [0, total_chunks]

    resume_content = ""
    if phase2_state_path.exists():
        with open(phase2_state_path, "r", encoding="utf-8") as f:
            phase2_data = json.load(f)
            ai_keywords = phase2_data.get("keywords_data", [])
            for kw in ai_keywords:
                en = kw.get("en", "").strip()
                ja = kw.get("ja", "").strip()
                if en and en not in master_glossary:
                    master_glossary[en] = ja
            resume_content = phase2_data.get("resume_content", "")

    tasks = []
    for section_name, chunks in sections_dict.items():
        if not chunks: continue
        
        # 既存のレジュメがあればチャンクの先頭に忍び込ませる（簡易的な受け渡し）
        # process_section の chunks は List[dict] なので破壊的変更を避けるためコピー
        payload_chunks = chunks
        if section_name in existing_resumes:
            payload_chunks = [{"existing_resume": existing_resumes[section_name]}] + chunks
        else:
            title_part = section_name.split("|", 1)[1] if "|" in section_name else section_name
            if title_part in existing_resumes:
                payload_chunks = [{"existing_resume": existing_resumes[title_part]}] + chunks

        tasks.append(process_section(
            section_name=section_name, chunks=payload_chunks, resume_content=resume_content,
            master_glossary=master_glossary, prompt_template=prompt_template,
            progress_state=progress_state, semaphore=semaphore, rate_limiter=rate_limiter,
            settings=settings, api_key=api_key, expertise=expertise, model=model,
            thinking_level=thinking_level, state=state, resume_only=resume_only, is_book=is_book,
            pdf_mode=pdf_mode
        ))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    translated_sections = {}
    section_resumes = {}

    for res in results:
        if isinstance(res, Exception):
            print_log(f"  [Phase 4] セクション処理致命的失敗（スキップ）: {res}")
            continue

        if len(res) == 4:
            # Book Mode: (section_name, en_structured_tree, ja_structured_tree, resume_text)
            sec_name, en_structured_tree, ja_structured_tree, resume_text = res
            
            # sec_name ("ID|Title") から ID を抽出して安全に比較する
            sec_id = sec_name.split("|")[0] if "|" in sec_name else None
            
            # 大元の english_tree の該当セクションの children を、構造化済みのツリーで上書きする
            for en_sec in english_tree:
                # IDが一致、またはタイトルが完全一致する場合に上書きする
                title_in_sec = sec_name.split("|", 1)[1] if "|" in sec_name else sec_name
                if (sec_id and str(en_sec.id) == sec_id) or (en_sec.text == title_in_sec):
                    en_sec.children = en_structured_tree
                    break
        else:
            # Paper Mode: (section_name, ja_structured_tree, resume_text)
            sec_name, ja_structured_tree, resume_text = res

        translated_sections[sec_name] = ja_structured_tree
        section_resumes[sec_name] = resume_text

    japanese_tree = rebuild_translated_tree(
        english_tree, translated_sections, section_resumes, resume_only, is_book
    )

    if save_state:
        # 1. 翻訳済み日本語ツリーの保存 (Phase 4 成果物)
        with open(phase4_state_path, "w", encoding="utf-8") as f:
            json.dump([n.to_dict() for n in japanese_tree], f, ensure_ascii=False, indent=2)

        # 2. 構造化済み英語ツリーの保存 (Phase 3 キャッシュの上書き)
        # Phase 5 がこのファイルから英語の階層情報を読み取れるようにする
        phase3_path = Path(phase4_state_path).parent / "phase3_structure.json"
        with open(phase3_path, "w", encoding="utf-8") as f:
            json.dump([node.to_dict() for node in english_tree], f, ensure_ascii=False, indent=2)
    
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
    tier: str = "paid",
    resume_only: bool = False,
    is_book: bool = False,
    pdf_mode: str = "default",
) -> List[TreeNode]:
    from .llm_client import run_async
    return run_async(_run_phase4_async(
        phase2_state_path, structure_state_path, sections_state_path, phase4_state_path,
        glossary_path, api_key, save_state, expertise, model, thinking_level, state, tier,
        resume_only, is_book, pdf_mode
    ))