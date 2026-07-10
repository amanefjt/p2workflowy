# 翻訳コンテキスト Stage 1 実装 Plan（レジュメ配線・ウィンドウ連続化・死コード整理）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 「レジュメを踏まえて翻訳する」を論文・書籍両モードで実体化する（Phase 2 レジュメ→Phase 4 翻訳文脈の配線、書籍は book_resume＋章レジュメ）。あわせてウィンドウを連続 ~2,000 字化し、死コード・死スロットを削除する。

**Architecture:** 正本スペックは `docs/superpowers/specs/2026-07-10-translation-context-architecture-design.md`（4 層モデルの Stage 1）。Phase 0（book_manager）→ Phase 2（章レジュメ）→ Phase 4（翻訳文脈）の配線を通し、Phase 4 の節レジュメ生成（`generate_section_resume`）を廃止する。

**Tech Stack:** Python 3.12 / venv（`./venv/bin/python`）/ pytest / Gemini API（`core/llm_client.py` 経由）

## Global Constraints

- テスト実行は常に `./venv/bin/python -m pytest tests/unit/ -q`（全合格を維持したままタスクを進める）
- `core/coreprompts.json` は `@lru_cache` されるため、変更後の手動実行確認はプロセス再起動が必要（pytest は毎回新プロセスなので影響なし）
- **削除の前には必ず再 grep で参照ゼロを確認**し、**削除だけのコミットと挙動変更のコミットを分ける**
- コミットメッセージは日本語
- `core/` 変更を含む一連の作業なので、最終タスクで `docs/management/troubleshooting_log.md`（I-9〜I-14 の対応済み化）と `requirements_log.md` に追記する
- **判断保留ポイント**（下記マーク ⚠️）で迷ったら、Agent ツールを `model: "fable"` で単発起動し、スペックのパスと質問を渡して相談する（このリファクタリング・シリーズ限定の運用）
- 実装完了後の比較読み（`docs/translation_review_checklist.md`）とモデル A/B はユーザーが行う。この Plan のスコープ外

---

### Task 1: coreprompts.json — BOOK_SUMMARY_PROMPT リネームと CHAPTER_SUMMARY_PROMPT 新設

**Files:**
- Modify: `core/coreprompts.json`
- Modify: `core/book_manager.py:68`（`GLOBAL_SUMMARY_PROMPT` 参照）
- Modify: `core/phase2_meta.py:66`（同上。この Task では挙動不変のリネーム追従のみ）
- Test: `tests/unit/test_json_pipeline.py`（プロンプトロードのテストがあればそこ、なければ新規関数を追記）

**Interfaces:**
- Produces: coreprompts.json キー `BOOK_SUMMARY_PROMPT`（旧 GLOBAL と同文）、`CHAPTER_SUMMARY_PROMPT`（新設、スロット `{expertise}` `{book_context}` `{context_guide}` `{text}`）。`SUMMARY_PROMPT` / `SECTION_SUMMARY_PROMPT` はこの時点では**残す**（削除は Task 5）

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_json_pipeline.py` に追記:

```python
def test_summary_prompt_keys_renamed_and_added():
    from core.config import load_coreprompts
    prompts = load_coreprompts()
    assert "BOOK_SUMMARY_PROMPT" in prompts
    assert "CHAPTER_SUMMARY_PROMPT" in prompts
    assert "GLOBAL_SUMMARY_PROMPT" not in prompts
    # CHAPTER_SUMMARY_PROMPT は必要なスロットをすべて持つ
    for slot in ("{expertise}", "{book_context}", "{context_guide}", "{text}"):
        assert slot in prompts["CHAPTER_SUMMARY_PROMPT"], f"missing slot: {slot}"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `./venv/bin/python -m pytest tests/unit/test_json_pipeline.py::test_summary_prompt_keys_renamed_and_added -v`
Expected: FAIL（`BOOK_SUMMARY_PROMPT` が存在しない）

- [ ] **Step 3: coreprompts.json を編集**

1. キー `GLOBAL_SUMMARY_PROMPT` を `BOOK_SUMMARY_PROMPT` にリネーム（値は変更しない）。
2. `BOOK_SUMMARY_PROMPT` の直後に `CHAPTER_SUMMARY_PROMPT` を新設。値は以下の全文（JSON 文字列として 1 行にエスケープして格納。`\n` は改行）:

```
あなたは {expertise} のシニア・リサーチャーです。

これから分析対象の【書籍の一章】の本文を提示します。読み込んだうえで、本文の後に示す指示に従い、この章のみを一つの論文とみなして論理構造を精緻に再現したレジュメをMarkdown形式で作成してください。

<book_context>
{book_context}
</book_context>

<context_guide>
{context_guide}
</context_guide>

<source_document>
{text}
</source_document>

---

上記の <source_document>（書籍の一章の本文）を分析し、以下のルールに厳密に従ってレジュメを作成してください。

【<book_context> の扱い (CRITICAL)】
- <book_context> は書籍全体の要約であり、**背景知識としてのみ**使用してください。
- レジュメ化の対象はあくまで <source_document>（この章）のみです。<book_context> の内容を要約したり、この章に存在しない議論を書き込んだりしてはいけません。
- 冒頭に「# 0. 書籍内での位置づけ」として、書籍全体の議論の中でこの章が占める位置を3〜5文で記述してください（<book_context> が「なし」の場合はこの項目自体を省略）。

【記述ルール (Strict Grounding Rules)】
1. **構成順序**:
   - # 0. 書籍内での位置づけ（上記の条件を満たす場合のみ）
   - # 1. この章の問い: 著者がこの章で立てている『問い』を明確に記述する。
   - # 2. この章の核心的主張: この章の議論の到達点と独自の貢献を記述する。
   - # 3. 各セクションの展開: 各節の主張と、それを支える論理的ステップ（Chain of Thought）を段階的に明示し、どのように議論されているかを詳述する。節と節の間の論理的接続（前節からの展開・次節への接続）も明示する。
2. **各セクションの見出し**: 原文の見出しを一切改変せず、一字一句そのまま `# [Original Heading]` として記述してください。注釈や日本語訳は含めないこと。
3. **メタ出力の禁止**: 「〜を要約しました」等の挨拶や解説は一切不要です。
4. **分量**: 日本語4000〜5000文字程度の詳細な記述を目標としてください。
5. **文体**: 常体（「だ・である」）を維持してください。

【出力前の確認】
`# [Original Heading]` として記述する各見出しは、<source_document> 内の表記と一字一句照合し、改変や表記揺れがないことを確認してから出力してください。

それでは、1行目から出力を始めてください。
```

⚠️ **判断保留ポイント**: この文言はドラフト。トーン・構成順序・「# 0. 位置づけ」の要否に迷ったら fable advisor に相談してよい（変更する場合もスロット 4 つは必ず維持）。

3. `core/book_manager.py:68` の `prompts.get("GLOBAL_SUMMARY_PROMPT", "")` を `prompts.get("BOOK_SUMMARY_PROMPT", "")` に変更。
4. `core/phase2_meta.py:66` の `prompts.get("GLOBAL_SUMMARY_PROMPT", prompts["SUMMARY_PROMPT"])` を `prompts.get("BOOK_SUMMARY_PROMPT", prompts["SUMMARY_PROMPT"])` に変更（この Task では挙動不変。CHAPTER への切替は Task 3）。

- [ ] **Step 4: テストが通ることを確認**

Run: `./venv/bin/python -m pytest tests/unit/ -q`
Expected: 全合格（既存テストが `GLOBAL_SUMMARY_PROMPT` を参照している場合はそのテストもリネーム追従させる。`grep -rn "GLOBAL_SUMMARY_PROMPT" tests/ core/` で参照ゼロを確認）

- [ ] **Step 5: コミット**

```bash
git add core/coreprompts.json core/book_manager.py core/phase2_meta.py tests/unit/test_json_pipeline.py
git commit -m "feat: BOOK_SUMMARY_PROMPT へリネームし CHAPTER_SUMMARY_PROMPT を新設"
```

---

### Task 2: Phase 0 → 章への book_resume 受け渡し復活（book_manager）

**Files:**
- Modify: `core/book_manager.py:204,211`
- Test: `tests/unit/test_book_manager.py`

**Interfaces:**
- Produces: 章ループの `run_pipeline(..., resume_content=self.global_resume or None, ...)`。`run_pipeline` の `resume_content` は Phase 2 へ流れ（`pipeline.py:112` 既存配線）、Task 4 で Phase 4 へも流れる

- [ ] **Step 1: 既存テストの harness を確認**

`tests/unit/test_book_manager.py` を読み、`BookManager.run()` を mock で通すテストがあるか確認する。あればその harness（`run_pipeline` の patch 方法）を流用して Step 2 のテストを書く。

- [ ] **Step 2: 失敗するテストを書く**

`tests/unit/test_book_manager.py` に追記（既存 harness がない場合の自己完結版。既存 harness があればそちらの流儀に合わせ、アサーションだけ移植する）:

```python
def test_run_passes_global_resume_to_chapter_pipeline(tmp_path):
    import json
    from unittest.mock import patch, MagicMock
    import core.book_manager as bm

    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")

    with patch.object(bm.BookManager, "_get_pdf_fingerprint", return_value="fp"):
        mgr = bm.BookManager(str(pdf), api_key="k", model="m")
    mgr.session_dir = tmp_path / "sess"
    mgr.session_dir.mkdir()
    # Phase 0 キャッシュを用意して _generate_global_context をスキップさせる
    (mgr.session_dir / "global_context.json").write_text(
        json.dumps({"resume": "GLOBAL_RESUME_TEXT", "glossary": [], "book_title": "book"}),
        encoding="utf-8",
    )

    captured = {}
    def fake_run_pipeline(**kwargs):
        captured.update(kwargs)
        return []

    splitter = MagicMock()
    splitter.split.return_value = [{"title": "Ch1", "path": str(pdf), "role": "chapter"}]

    with patch("core.pipeline.run_pipeline", side_effect=fake_run_pipeline), \
         patch.object(bm, "PDFSplitter", return_value=splitter), \
         patch.object(bm, "apply_tier_settings"), \
         patch("core.engine.p1_ingest.pdf_ingester.diagnose_pdf_quality", return_value=True), \
         patch("core.engine.p1_ingest.spread_splitter.is_spread_pdf", return_value=False):
        try:
            mgr.run(max_chapters=1)
        except Exception:
            pass  # 統合フェーズ以降の失敗は本テストの関心外

    assert captured.get("resume_content") == "GLOBAL_RESUME_TEXT"
```

（`PDFSplitter` / `SessionState` などの import 位置は実ファイルを確認して patch 対象を合わせること。ポイントは「`run_pipeline` に渡った `resume_content` が global_resume であること」の 1 点）

- [ ] **Step 3: テストが失敗することを確認**

Run: `./venv/bin/python -m pytest tests/unit/test_book_manager.py::test_run_passes_global_resume_to_chapter_pipeline -v`
Expected: FAIL（`captured["resume_content"]` が `None`）

- [ ] **Step 4: book_manager.py を修正**

`core/book_manager.py` の章ループ内（現 :204,211）:

```python
                # パイプライン実行: 各章を独立した「論文」として完結させ、物理ファイルを出力させる
                # book_resume は Phase 2 の章レジュメ生成の背景（<book_context>）と
                # Phase 4 の翻訳文脈に使われる（詳細: specs/2026-07-10-translation-context-architecture-design.md）
                processed_paths = run_pipeline(
                    input_path=ch["path"],
                    api_key=self.api_key,
                    session_id=ch_session_id,
                    is_book=True,
                    title=ch_title,
                    resume_content=self.global_resume or None,
```

（旧コメント「resume_content に self.global_resume を渡すと章の要約が全体要約で上書きされるため None にする」は削除する）

- [ ] **Step 5: テストが通ることを確認**

Run: `./venv/bin/python -m pytest tests/unit/ -q`
Expected: 全合格

- [ ] **Step 6: コミット**

```bash
git add core/book_manager.py tests/unit/test_book_manager.py
git commit -m "feat: 書籍全体レジュメを各章パイプラインへ受け渡す（I-9 断絶の解消）"
```

---

### Task 3: Phase 2 — 書籍分岐を CHAPTER_SUMMARY_PROMPT ＋ book_context 注入に変更

**Files:**
- Modify: `core/phase2_meta.py:47-98`（`generate_resume`）
- Test: `tests/unit/test_json_pipeline.py`（または新規 `tests/unit/test_phase2_meta.py`）

**Interfaces:**
- Consumes: Task 1 の `CHAPTER_SUMMARY_PROMPT`
- Produces: `generate_resume(text, ..., is_book=True, resume_context=<book_resume>)` が CHAPTER_SUMMARY_PROMPT を使い、`{book_context}` に book_resume（無ければ「なし」）を注入する。論文分岐は `prompts["SUMMARY_PROMPT_ronbun"]` 必須参照になる（`SUMMARY_PROMPT` フォールバック廃止 → Task 5 の削除が可能になる）

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_generate_resume_book_mode_uses_chapter_prompt(monkeypatch):
    import core.phase2_meta as p2

    captured = {}
    def fake_call_gemini(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["metrics"] = kwargs.get("metrics_metadata")
        return "dummy resume"
    monkeypatch.setattr(p2, "call_gemini", fake_call_gemini)

    p2.generate_resume("CHAPTER_TEXT", is_book=True, resume_context="BOOK_RESUME")
    assert "書籍の一章" in captured["prompt"]          # CHAPTER_SUMMARY_PROMPT を使用
    assert "BOOK_RESUME" in captured["prompt"]          # book_context に注入
    assert "CHAPTER_TEXT" in captured["prompt"]
    assert captured["metrics"] == {"section": "chapter_resume"}

def test_generate_resume_book_mode_without_book_context(monkeypatch):
    import core.phase2_meta as p2
    captured = {}
    monkeypatch.setattr(p2, "call_gemini",
                        lambda prompt, **kw: captured.update(prompt=prompt) or "r")
    p2.generate_resume("CHAPTER_TEXT", is_book=True, resume_context=None)
    assert "<book_context>\nなし\n</book_context>" in captured["prompt"]

def test_generate_resume_paper_mode_metrics(monkeypatch):
    import core.phase2_meta as p2
    captured = {}
    def fake_call_gemini(prompt, **kwargs):
        captured["metrics"] = kwargs.get("metrics_metadata")
        return "r"
    monkeypatch.setattr(p2, "call_gemini", fake_call_gemini)
    p2.generate_resume("PAPER_TEXT", is_book=False)
    assert captured["metrics"] == {"section": "paper_resume"}
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `./venv/bin/python -m pytest tests/unit/ -k "generate_resume" -v`
Expected: FAIL

- [ ] **Step 3: `generate_resume` を書き換え**

`core/phase2_meta.py` の :61-95 を以下に置換:

```python
    prompts = load_coreprompts()

    if is_book:
        # 書籍モード: 章専用プロンプト。book_resume は <book_context> として背景注入する
        prompt_tpl = prompts["CHAPTER_SUMMARY_PROMPT"]
        context_guide = "この章の論理構成、節ごとの議論の展開を抽出してください。"
    else:
        # 論文モード: 論文専用プロンプト
        prompt_tpl = prompts["SUMMARY_PROMPT_ronbun"]
        context_guide = "論文全体の構造、各セクションの論理構成を抽出してください。"

    if "[untitled section]" in text:
        context_guide += "\n注意: '# [untitled section]' は、アブストラクト直後の明示的な見出しのない序論を表します。これを「序論」として適切に要約に含めてください。"

    prompt = prompt_tpl.replace(
        "{expertise}", expertise
    ).replace(
        "{book_context}", resume_context or "なし"
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
        metrics_metadata={"section": "chapter_resume" if is_book else "paper_resume"}
    )
```

補足:
- `{book_context}` は論文プロンプトに存在しないので `.replace` は無害に素通りする。
- 旧 :76-78 の「resume_context を context_guide の先頭に注入する」ハックは削除（<book_context> スロットに置き換わったため）。
- docstring の「SUMMARY_PROMPT または GLOBAL_SUMMARY_PROMPT を使って」の記述も実態（CHAPTER / ronbun）に更新する。
- 章テキストのサンプリングについて: `_sample_text` は `len(text) <= limit`（書籍 1.5M 字）なら全文を返す実装のため、**章全文投入はすでに満たされている**（スペックの当該項目は変更不要と確認済み）。

- [ ] **Step 4: テストが通ることを確認**

Run: `./venv/bin/python -m pytest tests/unit/ -q`
Expected: 全合格

- [ ] **Step 5: コミット**

```bash
git add core/phase2_meta.py tests/unit/
git commit -m "feat: 書籍章レジュメを CHAPTER_SUMMARY_PROMPT ＋ book_context 注入に変更（I-10 解消）"
```

---

### Task 4: Phase 4 — 翻訳コンテキスト配線（両モード）と節レジュメ生成の呼び出し除去

**Files:**
- Modify: `core/phase4_translate.py`
- Modify: `core/pipeline.py:176-192`（`run_phase4` 呼び出し）
- Test: `tests/unit/test_parallel_translator.py` または新規 `tests/unit/test_phase4_context.py`

**Interfaces:**
- Consumes: `run_pipeline` の `resume_content`（書籍モードで book_resume が入っている。Task 2）
- Produces: `build_translation_context(book_resume: str, document_resume: str, is_book: bool) -> str`（`core/phase4_translate.py` のモジュールレベル関数）。`_run_phase4_async(..., book_resume: str = "")`。翻訳プロンプトの `{resume_content}` に組み立てたコンテキストが入る

- [ ] **Step 1: 失敗するテストを書く**

新規 `tests/unit/test_phase4_context.py`:

```python
from core.phase4_translate import build_translation_context


def test_paper_mode_uses_document_resume():
    ctx = build_translation_context("", "PAPER_RESUME", is_book=False)
    assert ctx == "PAPER_RESUME"

def test_paper_mode_empty_resume():
    assert build_translation_context("", "", is_book=False) == ""

def test_book_mode_combines_both():
    ctx = build_translation_context("BOOK", "CHAPTER", is_book=True)
    assert "【書籍全体の要約】" in ctx and "BOOK" in ctx
    assert "【この章の要約】" in ctx and "CHAPTER" in ctx
    assert ctx.index("BOOK") < ctx.index("CHAPTER")  # 全体→章の順

def test_book_mode_without_book_resume():
    ctx = build_translation_context("", "CHAPTER", is_book=True)
    assert "BOOK" not in ctx and "CHAPTER" in ctx
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `./venv/bin/python -m pytest tests/unit/test_phase4_context.py -v`
Expected: FAIL（`build_translation_context` が存在しない）

- [ ] **Step 3: phase4_translate.py を書き換え**

(a) モジュールレベル関数を追加:

```python
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
```

(b) `process_section_modular` から節レジュメ生成を除去（:31-38 と戻り値・引数を変更）:

```python
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
```

(c) `_run_phase4_async` に `book_resume: str = ""` 引数を追加し、コンテキスト組み立てと集約を変更:

```python
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
```

（`section_resumes` は消滅。`TreeReconstructor.rebuild` の第 3 引数はデフォルト `{}` なので変更不要。`phase5_export.py` は `metadata["summary"]` を参照していないことを確認済み——念のため `grep -n "summary" core/phase5_export.py core/engine/p5_export/*.py` で再確認すること）

(d) import 行から `generate_section_resume` を除去:

```python
from .llm_client import translate_batch, tier_manager, GeminiTier, apply_tier_settings
```

(e) `core/pipeline.py` の `run_phase4(...)` 呼び出し（:176-192）に 1 行追加:

```python
                is_book=is_book,
                book_resume=(resume_content or "") if is_book else "",
                pdf_mode=pdf_mode,
```

⚠️ **判断保留ポイント**: `【書籍全体の要約】`【この章の要約】` のラベル文言、および論文モードでラベルなし（素のレジュメ）とする判断はドラフト。変更したい場合は fable advisor に相談。

- [ ] **Step 4: テストが通ることを確認**

Run: `./venv/bin/python -m pytest tests/unit/ -q`
Expected: 全合格（`generate_section_resume` を参照する既存テストが落ちる場合、その参照は Task 5 で本体ごと消えるため、このタスクで「呼び出されないこと」のテストに書き換える）

- [ ] **Step 5: コミット**

```bash
git add core/phase4_translate.py core/pipeline.py tests/unit/
git commit -m "feat: Phase 2 レジュメを両モードで翻訳コンテキストに配線（節レジュメ生成を廃止）"
```

---

### Task 5: 削除コミット（参照ゼロを確認してから、削除だけを行う）

**Files:**
- Modify: `core/llm_client.py:519-566`（`generate_section_resume` 削除）
- Modify: `core/llm_client.py`（`translate_batch` の `context_guide` 引数削除）
- Modify: `core/coreprompts.json`（`SECTION_SUMMARY_PROMPT` / `SUMMARY_PROMPT` 削除、Summary 系キーを BOOK → CHAPTER → ronbun の順に整理）
- Modify: `core/engine/p3_structure/state_integrator.py`（死コード削除）
- Test: 既存テストの参照除去のみ

**Interfaces:**
- Consumes: Task 3（`SUMMARY_PROMPT` フォールバック廃止済み）、Task 4（`generate_section_resume` 呼び出し除去済み）

- [ ] **Step 1: 削除対象の参照ゼロを再 grep で確認**

```bash
grep -rn "generate_section_resume" core/ tests/ server.py main.py
grep -rn "SECTION_SUMMARY_PROMPT\|\"SUMMARY_PROMPT\"" core/ tests/ server.py main.py
grep -rn "context_guide" core/llm_client.py core/phase4_translate.py core/engine/p4_translate/
grep -rn "add_chapter\|_generate_consolidated_resume\|run_integration_test\|_apply_prefix_to_ids\|BookExporter" core/ tests/ server.py main.py
```

Expected: 各対象の参照が「定義自身」と「テスト内の削除予定参照」のみであること。**想定外の参照が見つかったらこの Task を中断し、fable advisor に相談する**。
また `TRANSLATION_PROMPT` に `{context_guide}` プレースホルダが**ないこと**を確認（2026-07-10 時点でないことを確認済み。あった場合は G2-1 の巻き戻しが起きているので advisor に相談）:

```bash
./venv/bin/python -c "import json; print('{context_guide}' in json.load(open('core/coreprompts.json'))['TRANSLATION_PROMPT'])"
```

Expected: `False`

- [ ] **Step 2: 削除を実施**

1. `core/llm_client.py`: `generate_section_resume` 関数全体（:519-566、300 字フォールバック含む）を削除。
2. `core/llm_client.py` `translate_batch`: シグネチャから `context_guide: str = "",` を削除し、`base_prompt = prompt_template.format(...)` から `context_guide=context_guide,` を削除。
3. `core/coreprompts.json`: `SECTION_SUMMARY_PROMPT` と `SUMMARY_PROMPT` のキーを削除。Summary 系キーの並びを `BOOK_SUMMARY_PROMPT` → `CHAPTER_SUMMARY_PROMPT` → `SUMMARY_PROMPT_ronbun` の順に整理。
4. `core/engine/p3_structure/state_integrator.py`: `add_chapter` / `_generate_consolidated_resume` / `integrate` / `run_integration_test` / `_apply_prefix_to_ids` の各メソッドと、`__init__` 内でそれらだけが使う `self.chapters` / `chapter_resumes` / `chapter_titles` の初期化を削除。本番経路 `integrate_to_book` は**触らない**。
5. テスト内の削除対象参照（あれば）を除去。

- [ ] **Step 3: テストが通ることを確認**

Run: `./venv/bin/python -m pytest tests/unit/ -q`
Expected: 全合格

- [ ] **Step 4: 削除だけのコミット**

```bash
git add core/llm_client.py core/coreprompts.json core/engine/p3_structure/state_integrator.py tests/unit/
git commit -m "refactor: 節レジュメ生成・未使用プロンプト・state_integrator 死コードを削除（I-12/I-13 解消）"
```

---

### Task 6: スライディングウィンドウの連続化（断片 3 件×200 字 → 連続 ~2,000 字）

**Files:**
- Modify: `core/engine/p4_translate/prompt_builder.py:22-36`
- Test: 新規 `tests/unit/test_prompt_builder.py`

**Interfaces:**
- Produces: `TranslationPromptBuilder.format_previous_translation(nodes: List[TreeNode]) -> str`（`max_nodes` 引数は廃止。呼び出し元 `phase4_translate.py` の lambda は位置引数 1 つなので変更不要）。ファイル先頭に定数 `WINDOW_MAX_CHARS = 2000`

- [ ] **Step 1: 失敗するテストを書く**

新規 `tests/unit/test_prompt_builder.py`:

```python
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
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `./venv/bin/python -m pytest tests/unit/test_prompt_builder.py -v`
Expected: FAIL（`WINDOW_MAX_CHARS` が存在しない）

- [ ] **Step 3: prompt_builder.py を書き換え**

ファイル先頭（import の直後）に定数を追加し、`format_previous_translation` を置換:

```python
# 直前訳ウィンドウの最大文字数。断片ではなく連続した直前訳文（段落丸ごと）を渡す。
# 根拠: docs/superpowers/specs/2026-07-10-translation-context-research-notes.md
WINDOW_MAX_CHARS = 2000
```

```python
    def format_previous_translation(self, nodes: List[TreeNode]) -> str:
        """
        直前の翻訳結果を「連続した文脈」として整形する。
        末尾から遡って段落（role=="p"）を丸ごと集め、合計 WINDOW_MAX_CHARS を上限とする。
        最低 1 段落は必ず含める（巨大段落による空ウィンドウを防ぐ）。
        """
        if not nodes:
            return ""
        selected: List[str] = []
        total = 0
        for n in reversed(nodes):
            if n.role != "p":
                continue
            text = n.text.strip()
            if not text:
                continue
            if selected and total + len(text) > WINDOW_MAX_CHARS:
                break
            selected.append(text)
            total += len(text)
            if total >= WINDOW_MAX_CHARS:
                break
        if not selected:
            return ""
        selected.reverse()
        return "\n\n".join(["# 直前の翻訳文脈 (Context)"] + selected)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `./venv/bin/python -m pytest tests/unit/ -q`
Expected: 全合格

- [ ] **Step 5: コミット**

```bash
git add core/engine/p4_translate/prompt_builder.py tests/unit/test_prompt_builder.py
git commit -m "feat: 翻訳ウィンドウを断片3件×200字から連続~2,000字に変更"
```

---

### Task 7: E2E 検証・管理ログ・完了宣言

**Files:**
- Modify: `docs/management/troubleshooting_log.md`（I-9〜I-14 の対応済み化）
- Modify: `docs/management/requirements_log.md`（実装完了エントリ）

- [ ] **Step 1: 単体テスト全体の最終確認**

Run: `./venv/bin/python -m pytest tests/unit/ -q`
Expected: 全合格（合格数が着手前より減っていないこと）

- [ ] **Step 2: 論文モードのゴールデン検証（要 GEMINI_API_KEY）**

`golden-verification` skill を invoke し、その手順に従う。最低限:

```bash
./venv/bin/python main.py data/input/paperplain/NST/NSTsample.txt --lite
```

Expected: 完走し、出力 `_p2.md` の見出し構成が理想出力（同ディレクトリ）と一致（構造回帰なし）。翻訳文言の差は不合格条件ではない（訳文品質はユーザーの比較読みで判定）。
また `state/<session_id>/` の Phase 4 デバッグプロンプト（あれば）を 1 件開き、`<resume_content>` に論文レジュメが入っていることを目視確認する。

- [ ] **Step 3: 書籍モードのスモーク（要 GEMINI_API_KEY・時間がかかる）**

```bash
./venv/bin/python main.py data/input/Booksample/relations/relationspdf.pdf --book --lite
```

（時間・コストが問題なら `max_chapters` 相当の制限や既存セッションの `--resume` を活用してよい）
Expected: 完走。最終出力の巻頭に書籍全体レジュメ、各章に「## レジュメ」（I-14 の配線維持）。章の翻訳プロンプト（debug）に「【書籍全体の要約】」「【この章の要約】」の両方が入っている。

- [ ] **Step 4: 管理ログ追記**

- `troubleshooting_log.md`: I-9〜I-14 の各項目に「対応済み（2026-XX-XX, Stage 1 実装）」を追記（I-8 対応済みの書式に倣う）。
- `requirements_log.md`: Stage 1 実装完了のエントリを追加（変更点の要約と、比較読み・モデル A/B が次ステップである旨）。

- [ ] **Step 5: コミット**

```bash
git add docs/management/
git commit -m "docs: 翻訳コンテキスト Stage 1 の実装完了を管理ログに記録"
```

- [ ] **Step 6: 完了報告**

superpowers:verification-before-completion に従い、テスト結果・ゴールデン検証結果を明示して完了報告する。**次のステップ（ユーザー実施）**: 比較読み（`docs/translation_review_checklist.md`、NST で Stage 1 前後比較）→ モデル A/B（現行 vs ハイブリッド）→ その結果を持って Stage 2 の Spec/Plan 起案セッションへ。
