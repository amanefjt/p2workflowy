# Troubleshooting Log: P2Workflowy V3

## 発生したエラーと解決策

### 1. `FileNotFoundError` (StateIntegrator integration)
- **事象**: Book Mode で章ごとの翻訳が完了しても、最終的な統合ファイルが生成されない。
- **原因**: Phase 5 が `is_book=True` の場合に作成する `[Title]_export/` サブディレクトリを `StateIntegrator` が考慮しておらず、直下のディレクトリを探していたため。
- **解決策**: `StateIntegrator.integrate_to_book` にて `_export` サブディレクトリを優先的に探索するように修正。

### 2. `NameError` (BookManager kwargs pop)
- **事象**: `BookManager.run` の `pop()` 部分で変数が解決されずクラッシュ。
- **原因**: `explicit_keys` に定義された変数を `pipeline_kwargs.pop()` した際、再注入の処理が漏れていたため、あるいは `kwargs` 内での競合が発生。
- **解決策**: `pipeline_kwargs` からの抽出ロジックを整理し、必要な引数のみが `run_pipeline` に渡るよう監査・修正。

### 3. モデル選択の不一致 (Tier Resolution Timing)
- **事象**: `--lite` フラグを指定しても Phase 0 (Global Scan) で高コストな `gemini-3-flash-preview` が使用される。
- **原因**: `BookManager.__init__` でモデルのデフォルト値（Paid向け）を固定していたため、`run()` でティアが確定した後に更新されなかった。
- **解決策**: `BookManager.__init__` でのモデル決定を遅延させ、実際に API 呼び出しを行う直前に `get_default_model()` を呼ぶように修正。

### 4. XMLパース失敗 (RTT protocol)
- **事象**: Gemini の出力に Markdown コードブロック（```json）が含まれるとパースが壊れる。
- **原因**: `_parse_response` がコードブロックのメタデータをテキストの一部として拾ってしまう。
- **解決策**: 入力にコードブロックを含めないようプロンプトを徹底するとともに、正規表現ベースでタグの中身のみを抽出。

### 5. 特定セクションでの異常な実行遅延 (API Queuing 240s TTFT と並列相殺の発見)
- **事象**: Phase 4 の翻訳において、特定のセクションの TTFT（最初の1文字が出るまでの時間）が 220〜240秒（約4分）に跳ね上がり、プロセスがフリーズしたように見える。
- **原因判定と推移**: 
  1. 初期推測では `Structured Output (JSON Schema)` のペナルティとされていた。
  2. V3でSchemaを廃止後も再発したため、「クライアント側の高並列によるキューイング」と推測して**「完全直列化（Context Chaining）」**を一度実装した。
  3. しかし完全直列であっても「単発で230秒待たされる（合計1時間の絶望的遅延）」ことが判明し、**「長大コンテキスト＋Thinking: Highに対するGoogle側クラスタのハードウェア的スケジューリング（塩漬け）」**が根本原因であると特定された。
- **最終解決策 (Global Optimum)**: どのみちサーバー側で4分待たされるのであれば、直列で順に待つのではなく**「並列で一気に投げて待機時間を相殺する」**ことが唯一の最適解となる。直列化（Context Chaining）を破棄・ロールバックし、`max_concurrent_sections = 4` という最適な並列数（トークン制限を回避しつつキューを消化する黄金比）による一斉並列処理を確立。これにより Phase 4 は合計10分内で完走するようになった。

> **[2026-05-11 訂正]** 「約4分の塩漬け現象」は 2026-04-04 の一時的な外れ値（サーバー障害 × Phase 1 分割バグの複合）であり、定常現象ではなかった。2026-05-11 の実測（AL論文 17バッチ × 8回）での avg TTFT は 14〜31s。concurrent=1（直列）が約50%遅いという事実は実証済みのため「直列化を避ける」という結論は変わらないが、「4分のキューを並列で相殺する」という説明は不正確。詳細は `docs/model_optimization.md` Section 3 参照。

## 2026-04-04: Book Mode Chapter Titles Overwritten by DNA

**Problem:** 
各章が正常に処理されているように見えても、出力されるファイル名および統合処理において、多数の章が `[Unlabeled Section]_p2.txt` として出力されてしまう問題が発生。これにより `StateIntegrator` が各章のファイルを見つけられず、最終的な統合処理がスキップされていました。

**Cause:** 
Phase 2 (Meta Generation) において、文献の第一ページからタイトルを推定する `MetaAnalyzer.analyze_dna()` が、明確なタイトルのない章の先頭を `[Unlabeled Section]` として抽出。その後、`core/pipeline.py` にて「Phase 2で抽出したDNAタイトルがあれば、パイプライン側の `title` 変数を上書きする」というロジックが走り、`BookManager` から直接与えられた正しい目次タイトル（例: "6. Kinship Unbound"）が強制的に `[Unlabeled Section]` に置き換えられてしまっていました。その結果としてPhase 5も `[Unlabeled Section]` という名前でエクスポートを行っていました。

**Resolution:** 
`core/pipeline.py` において、ユーザー（または `BookManager`）が明示的に `title` を指定して `run_pipeline` を実行したかどうかを判定するフラグ (`explicit_title = title is not None`) を追加しました。
以降の Phase 2 でタイトルを上書きする処理 (`title = meta["dna"]["title"]`) の実行条件に `not explicit_title` を紐付け、明示的なタイトルが既にある場合はDNAから推測したタイトルでの上書きを禁止しました。（※ 単発のPaper Modeなど、ファイル名から仮で生成したタイトルの場合は引き続き上書きを許可します。）

---

## 2026-05-15: コードレビュー全コードベース監査（バグ一括修正）

全コードベースを対象にしたサブエージェントによるセキュリティ・品質監査を実施。以下の問題を検出・修正した。

### C-1. ダウンロード 403 エラー（ronbunnihongo モード）
- **事象**: `app_ronbun.js` でダウンロードボタンを押すと毎回 403 が返される。
- **原因**: `pollStatus()` にダウンロードトークンを渡していなかったため、ダウンロード URL に `?token=` が付かず、`server.py` の `secrets.compare_digest()` が常に不一致を返していた。
- **解決策**: `app_ronbun.js` の `pollStatus` / `showDownloads` にトークンを伝播し、URL に `?token=` を追加。

### C-2. ronbunnihongo モードでレジュメが出力されない
- **事象**: ronbunnihongo 出力ファイルにレジュメ（要約）が含まれない。
- **原因**: `phase5_export.py` の `ronbunnihongo` 分岐が `resume_content` を使わず日本語ツリーのみ書き出していた。
- **解決策**: `p2workflowy` モードと同じ条件でレジュメを先頭に追加するよう修正。

### C-3. `--resume-only` / `--structure-only` が書籍モードで無視される
- **事象**: CLI の `--resume-only` / `--structure-only` フラグが書籍モード時に効かず、常にフルパイプラインが走る。
- **原因**: `book_manager.py:166-167` で `pipeline_kwargs` から再取得しようとしていたが、これらは `run()` の名前付き引数として既に受け取られており `pipeline_kwargs` には含まれない。常に `False` に上書きされていた。
- **解決策**: `pipeline_kwargs` からの再取得行と `explicit_keys` からの当該エントリを削除し、名前付き引数をそのまま利用。

### I-1. `resume_only=True` でも Phase 4 が再実行される
- **事象**: `--resume-only` 指定時に翻訳済みキャッシュが無視され API コストが無駄に発生。
- **原因**: `pipeline.py:169` の条件が `state.phase4_translate.exists() and not resume_only` であり、`resume_only=True` の場合にキャッシュを読まずに再翻訳していた。
- **解決策**: 条件を `state.phase4_translate.exists()` に修正。キャッシュがあれば常に再利用。

### I-2. 書籍モードで `dna` / `intro_pre_heading` が未初期化
- **事象**: 将来のリファクタ時に `UnboundLocalError` が発生しうる潜在バグ。
- **原因**: `phase3_structure.py` の書籍モード分岐で `dna` と `intro_pre_heading` が初期化されないまま `build_tree` の呼び出しで条件式の左辺に現れていた。
- **解決策**: 書籍モード分岐の末尾に `dna = {}` / `intro_pre_heading = None` を追加。

### I-3. `global_resume = None` による型不整合（book_manager）
- **原因**: PDF 破損時に `self.global_resume = None` を設定していたが、下流は `str` 型を前提。
- **解決策**: `""` に変更。

### I-4. `existing_resume` デッドコード（phase4_translate）
- **原因**: Phase 3 が `existing_resume` キーを sections_dict に注入するパスが存在しないにもかかわらず、Phase 4 にその抽出コードが残存していた。
- **解決策**: 該当ブロックを削除。

### I-5. 同期 LLM クライアントの空レスポンス検出が非同期版と不整合
- **原因**: `call_gemini`（同期）は `chunk is None` で即エラーだったが、ストリーム末尾の metadata-only チャンクが `None` になるケースで誤ってエラーを投げていた。
- **解決策**: `if chunk is None and not full_response_text:` に統一（非同期版と同じ条件）。

### M-1. `phase2_meta.py` の例外型誤り
- **原因**: チャンクが空の場合に `FileNotFoundError` を投げていたが、ファイルは存在しており内容が空なので意味が違う。
- **解決策**: `ValueError` に変更。

## 2026-06-07: coreprompts.json 構造改善時に発見した実装バグ2件

`core/coreprompts.json` の要約・翻訳・抽出系プロンプトを再構成する作業（詳細は requirements_log.md の同日エントリを参照）の過程で、プロンプトと呼び出し側コードの整合性を精査した際に見つかったバグ。いずれも副産物として発見・修正した。

### I-6. `meta_analyzer.py` で VLM ヒントが最終指示の後ろに回り込む
- **事象**: `DNA_EXTRACTION_PROMPT` の末尾の最終指示（「解説や挨拶は一切不要です。純粋な JSON のみを出力してください。」）が、実際にモデルへ渡るプロンプトの末尾になっていなかった。
- **原因**: `prompt = prompt_template.format(chunks_json=chunks_json) + prompt_hint` という文字列連結により、テンプレート末尾の最終指示の**後ろに** VLM ヒントが付与されていた。「最終指示は生成直前（プロンプト末尾）に置く」という構造上の前提が、コード結合後には崩れていた。
- **解決策**: `DNA_EXTRACTION_PROMPT` に `{vlm_hint}` プレースホルダーを追加し、`prompt_template.format(chunks_json=chunks_json, vlm_hint=vlm_hint)` で注入するよう変更（`core/engine/meta_analyzer.py`）。最終指示が文字列結合後も本当に末尾に来るようにした。

### I-7. `TOC_EXTRACTION_PROMPT` の出力スキーマ例が二重波括弧のまま渡っていた
- **事象**: `TOC_EXTRACTION_PROMPT` 内の JSON 出力スキーマ例が `{{"title": "...", ...}}` という二重波括弧で記述されており、モデルに渡る実際のプロンプトにも壊れた見た目の JSON 例がそのまま出力されていた。
- **原因**: `core/pdf_splitter.py::_extract_toc()` はテンプレートを `.replace("{text}", text_samples)` で処理する（`.format()` ではない）ため、`{{ }}` エスケープは不要かつ有害だが、記述時にこの区別が確認されていなかった。
- **解決策**: スキーマ例の `{{` → `{`、`}}` → `}` に修正し、有効な JSON として表示されるようにした。

**教訓**: プロンプトテンプレートを改修する際は、呼び出し側コードが `.format()` と `.replace()` のどちらで処理しているかを必ず確認すること（`.replace()` 系では `{{ }}` エスケープ不要）。また、コード側でプロンプト文字列に `+ 文字列` のような後置連結が無いかを確認し、あれば結合後の全体を見て「最終指示が本当に末尾に来るか」を検証すること。

## 2026-07-04: 全体リファクタリング（フェーズD/E/F）の E2E ゴールデン検証で発見した Phase 2/3 結合の脆弱性

`docs/superpowers/plans/2026-06-10-codebase-refactoring.md` フェーズ D/E/F 完了後の E2E ゴールデン検証（`python3 main.py data/input/paperplain/NST/NSTsample.txt --lite` / `data/input/paperpdf/AL/ALpdf.pdf --lite`）で発見。**対応済み**（`systematic-debugging` スキルで根本原因を特定し修正。詳細は下記 I-8 参照）。

### I-8. テキストルートで末尾の見出し（Conclusion）が本文扱いに格下げされ、セクションごと出力から欠落する

- **事象**: NST 論文（テキスト入力）で `--lite` 実行時、最終出力 `_p2.md` から `Conclusion` セクションが丸ごと欠落した。同一入力・同一コードベースでの過去の実行（本セッション以前のバックアップ出力）および理想出力 `NSTsample_idealp2.txt` には `Conclusion` セクションが存在する。一方、PDF ルート（AL 論文）では同条件の実行で理想出力と完全に一致し、`Conclusion` を含む全セクションが正しく保持された。
- **一次切り分け**: `state/<session_id>/phase1_preprocessor.json` を確認すると、`Conclusion` は Phase 1 の時点で正しく `role: "h2"` として検出されていた（chunk id=86）。しかし `state/<session_id>/phase3_structure.json` では同じチャンクが `role: "p"`（本文扱い）に格下げされていた。
- **根本原因（暫定）**: `core/phase3_structure.py:145` 付近のコメント「アンカー検知によるスキップを廃止し、レジュメの見出しリストを唯一の基準にする」の通り、Phase 3 の見出し認識（`extract_headings_from_resume` → `match_heading`）は **Phase 2 が生成するレジュメ（要約）の「各セクションの展開」箇条書きに見出し名が言及されているか**にのみ依存する設計になっている。今回の実行では Phase 2 の要約生成（`--lite` モデル使用）が箇条書きに `Conclusion` を含めなかったため、Phase 3 がこの見出しをレジュメの既知見出しリストの中に見つけられず、対応するチャンクを非見出し（本文）として扱った。Phase 1 側の h1/h2 判定結果は Phase 3 の最終判断には使われていない。
- **リファクタリングとの関係**: この結合（Phase 3 の見出し認識が Phase 2 のレジュメ網羅性に完全依存する設計）は今回のフェーズ D/E/F（ファイル配置・命名・ドキュメント同期のみ）が触れたコードパスではなく、既存の設計。PDF ルートでは理想出力と完全一致したことから、フェーズ D/E/F 自体が挙動を変えたわけではなく、レジュメ生成 LLM 呼び出しの出力ゆらぎ（`--lite` モデル使用時に顕在化しやすい）によって既存の脆弱性が露呈したものと判断する。
- **次のステップ（未着手）**: 別タスクとして `systematic-debugging` スキルで着手する。候補となる対策方向（未検証）: (a) Phase 3 の見出し照合に Phase 1 の `role: h1/h2` 判定をフォールバックとして併用する、(b) Phase 2 の要約プロンプトに「本文中の全見出しを漏れなく箇条書きに含めること」という網羅性の明示的な指示を追加する。いずれも挙動を変える変更のため、リファクタリング計画のスコープ外（同計画の「スコープ外」節に「プロンプト内容・モデルルーティングの変更」と明記）。

### I-8 対応済み（2026-07-04）

`systematic-debugging` スキルで着手し、対策 **(a) のみ**を実装した（(b) のプロンプト変更は今回見送り）。

- **確定した根本原因**: Phase 3 の Paper Mode 経路（`build_tree` の else 分岐、`core/engine/p3_structure/tree_builder.py:274-286`）は、渡された全チャンクを問答無用で `role="p"` に作り直しており、Phase 1（`TextStructureExtractor`）が既に決定論的に検出済みの `role="h1"/"h2"` を完全に握りつぶす設計だった。この「role を見ない」設計は 2026-05-07 のコミット `e345ffe`（テキスト入力対応の追加）で導入されたもので、それ以前のバージョン（`07d7784` 時点の `tree_constructor.py`）は `node.role in ["h1","h2",...]` を最優先の判定基準にしていた。`CLAUDE.md` の設計原則「VLM の論理役割判断 > 物理証拠 > 幾何的ヒント」と整合しない状態が `e345ffe` 以降続いていたことになる。
- **実装した修正 (a)**: Phase 3 の「見出しリストが唯一の基準」というアーキテクチャ自体（`tree_builder.py` / `heading_matcher.py` のマッチングロジック）は変更せず、その基準となる `headings` リストの生成元を拡張した。
  - `core/engine/p3_structure/heading_matcher.py` に純関数 `merge_role_headings(role_headings, resume_headings)` を追加。正規化比較（既存の `normalize_heading`）で重複を除きつつ、レジュメ由来のリストに Phase 1 の role 由来見出しを合成する。
  - `core/phase3_structure.py` の Paper Mode 分岐（146行目付近）で、`extract_headings_from_resume` の直後に `chunks` から `role in ("h1","h2")` のチャンクテキストを抽出し `merge_role_headings` で合成するよう変更。
  - `tree_builder.py` の `role="p"` 強制変換や `match_heading` のロジックには一切手を入れていない（変更範囲を最小化）。
- **テスト**: `tests/unit/test_heading_matcher.py` に `merge_role_headings` の単体テスト4件、`tests/unit/test_phase3_structure.py` に I-8 の実シナリオ（Conclusion がレジュメから漏れるケース）を再現する回帰テスト2件（修正前の欠落挙動の確認＋修正後の復元確認）を追加。`python3 -m pytest tests/unit/ -q` で 197 件全合格（既存191件 + 新規6件、回帰なし）。
- **実地検証**: `python3 main.py data/input/paperplain/NST/NSTsample.txt --lite` を実行し、`phase3_structure.json` で `Conclusion` が独立した `role: "h2"` トップレベルノードとして復元されていることを確認。最終出力 `_p2.md` / `_p2.txt` にも英語（nested）・日本語（parallel）双方に `Conclusion` セクションが出力され、`NSTsample_idealp2.txt` の階層構造と一致した。
- **(b) を見送った理由**: レジュメの悉皆性向上（プロンプト改善）は要約 LLM の品質・モデル Tier に依存し保証にならないため、決定論的に解決する (a) のみで根本原因は解消済みと判断。(b) は将来 lite モデルでの他の見落としパターンが観測された場合に改めて検討する。

## 2026-07-07: 書籍モードのレジュメ／プロンプト受け渡しの実データフロー監査（未対応・Spec A 起案）

「`coreprompts.json` の Summary 系プロンプトの用途が不明」という問いを起点に、書籍モードの情報受け渡し（Phase 0→2→3→4→統合）をコードで端から端まで追跡した結果、**意図と実装の乖離が複数**見つかった。いずれも本日時点では**未修正**。対策方針は `docs/superpowers/specs/2026-07-07-book-mode-resume-prompts-design.md`（Spec A）に集約し、段階的に実装する。実装着手前の調査ベースラインとしてここに記録する。

### I-9. Phase 0 の書籍全体レジュメ（global_resume）が章処理に渡っていない

- **事象**: `book_manager._generate_global_context()`（Phase 0）が全書籍テキストから `GLOBAL_SUMMARY_PROMPT` で生成する `global_resume` は、各章の `run_pipeline()` に渡されていない。`core/book_manager.py:211` で `resume_content=None` が明示され、「渡すと章要約が全体要約で上書きされるため None にする」というコメント付き。
- **影響**: 高コストな全書籍スキャンの成果（book_resume）が最終エクスポートの巻頭表示（`integrate_to_book` の `global_resume`）にしか使われず、**章レジュメ生成・翻訳の文脈に一切反映されていない**。ユーザーの意図（「書籍全体レジュメを踏まえて各章を論文のようにレジュメ化し、両者で翻訳する」）と食い違う。
- **根本**: 旧「上書き」バグはプロンプト側で防ぐべき問題（後述 I-10）を、呼び出し側で `None` にして回避したもの。Spec A では専用プロンプトで book_resume を `<book_context>`（背景）として注入し、断絶を復活させる。

### I-10. 書籍モード Phase 2 が「書籍全体用」プロンプトを 1 章に流用している

- **事象**: `core/phase2_meta.py:64-69`、書籍モード分岐で `GLOBAL_SUMMARY_PROMPT` を使用。このプロンプト本文は「これから【書籍全体】の本文を提示します」「8000文字程度」「各章の論理展開」と**全書籍前提**で書かれているのに、実際には 1 章分のテキストしか渡らない（`_sample_text` の head+tail サンプル）。
- **影響**: LLM に「これは書籍全体だ」という誤った前提で 1 章だけを見せているため、「各章の構成」「章間のつながり」といった項目が実態と噛み合わない出力を誘発しうる。
- **対策方針**: 章専用の `CHAPTER_SUMMARY_PROMPT`（新設）に差し替え、`GLOBAL_SUMMARY_PROMPT` は本来の全書籍用途（Phase 0）だけに戻す（Spec A）。

### I-11. 書籍モードでは章レジュメは構造化に使われない（Paper Mode 専用機能だった）

- **事象**: 「Phase 2 の章レジュメは Phase 3 の見出し抽出に必要」という理解は**書籍モードでは誤り**。書籍章処理は常に `pdf_mode="full_vlm"`（`book_manager.py:214` でハードコード）であり、`run_phase3` は `pdf_mode=="full_vlm"` 分岐（`phase3_structure.py:50`）で `structure_nodes_by_markdown()`（VLM の Markdown 見出しから構造化）を通る。Markdown 見出しが無い場合も `is_book and input_path` 分岐（同 :77）で ChapterParser/TOC を使う。`extract_headings_from_resume`（同 :148）を含む resume ベースの見出し抽出は `else`（Paper Mode）分岐でしか到達しない。
- **含意**: 書籍モードの章レジュメ（`resume_content`）は構造化に一切使われず、消費先は **Phase 4 の翻訳コンテキスト参照**と **Phase 5 の章「## レジュメ」描画**のみ。したがって書籍モードでは章レジュメと Phase 4 節レジュメが**冗長な二重生成**になっている。I-8 で修正した「レジュメ ∪ Phase1 role」の見出し判定は Paper Mode の話であり、書籍モードとは別系統である点に注意。

### I-12. SECTION_SUMMARY_PROMPT の粒度・スロット名が実装と乖離

- **事象**: `generate_section_resume`（`llm_client.py:519`）は Phase 4 で `sections_dict.items()` を回すループ内から**節ごとに**呼ばれる（`phase4_translate.py:106-111`）。一方 `SECTION_SUMMARY_PROMPT` 本文は「【セクション原文（章）】」「この章/セクションにおいて」と**章まるごと**を想定した書き方。さらにテンプレートの `<book_meta_reference>` スロットには書籍全体レジュメではなく **Phase 2 の章レジュメ**（`p2_data["resume_content"]`）が渡っている（`phase4_translate.py:85-89`）。
- **対策方針**: 翻訳用レジュメを**章まるごと 1 回**の生成に変更（節間接続を正確に書けるようにする）。Spec A では `generate_section_resume` を廃止し、章レジュメ（Phase 2, book_resume 背景つき）を翻訳コンテキストに直接使う方向で整理。

### I-13. state_integrator に死コード（呼べば即エラー）

- **事象**: `core/engine/p3_structure/state_integrator.py` の `add_chapter` / `_generate_consolidated_resume` / `integrate` / `run_integration_test` は本番でもテストでも未使用。`integrate()` が参照する `BookExporter` はどこにも定義・import されておらず、呼べば `NameError`。本番の統合は `integrate_to_book`（各章の出力ファイルを積み上げ＋巻頭に Phase 0 の global_resume を付与）のみ。
- **含意**: 「Phase 3 統合が `GLOBAL_SUMMARY_PROMPT` で章レジュメを集約する」という理解は誤り。`_generate_consolidated_resume`（GLOBAL_SUMMARY_PROMPT の 3 つ目の呼び出し）は死コード。Spec A で削除対象。

### I-14. Phase 5 が章レジュメを出力に描画している（設計変更時の影響先）

- **事象**: `core/phase5_export.py:113-141` は `resume_content`（Phase 2 の章レジュメ）を各章の「## レジュメ」セクションとして最終出力に描画する。
- **含意**: 「書籍モードで Phase 2 章レジュメを廃止する」等の設計変更を行う場合、Phase 5 の章レジュメ表示が消えないよう、レジュメの供給元を付け替える配線が必要。Spec A の設計判断に織り込む。

### I-9〜I-14 対応済み（2026-07-11, 翻訳コンテキスト Stage 1 実装）

`docs/superpowers/plans/2026-07-10-translation-context-stage1.md` を `subagent-driven-development` で実装し、I-9〜I-14 を一括で解消した（正本設計は `docs/superpowers/specs/2026-07-10-translation-context-architecture-design.md` の Stage 1）。単体テストは着手前 197 件 → 211 件全合格（回帰なし）。各項目の対応:

- **I-9 対応済み**（Task 2, commit `b1016e2`）: `book_manager.py` の章ループを `resume_content=self.global_resume or None` に戻し、旧「上書き回避で None」コメントを削除。Phase 0 の書籍全体レジュメが各章 `run_pipeline()`→Phase 2→Phase 4 に流れるようになった。
- **I-10 対応済み**（Task 1+3, commits `090b1ce`/`05ba97e`）: 章専用 `CHAPTER_SUMMARY_PROMPT` を新設し、書籍 Phase 2 分岐をこれに差し替え。`book_resume` は `<book_context>`（背景、無ければ「なし」）として注入。旧 `GLOBAL_SUMMARY_PROMPT` は本来の全書籍用途向けに `BOOK_SUMMARY_PROMPT` へリネーム（Phase 0 専用）。
- **I-11 整理済み**（Task 4, commit `0e3204a`）: 「書籍モードでは章レジュメは構造化に使われない」という事実自体は不変（構造化は VLM Markdown/ChapterParser が担う）。冗長だった Phase 4 節レジュメ（`generate_section_resume`）との二重生成を解消し、Phase 2 章レジュメを直接 Phase 4 翻訳コンテキストへ供給する一本化に変更。
- **I-12 対応済み**（Task 4+5, commits `0e3204a`/`835af29`）: 粒度・文言・スロットが乖離していた `generate_section_resume` と `SECTION_SUMMARY_PROMPT` を廃止。翻訳用レジュメは章まるごと 1 回生成（Phase 2）に集約。
- **I-13 対応済み**（Task 5, commit `835af29`）: `state_integrator.py` の死コード（`add_chapter` / `_generate_consolidated_resume` / `integrate` / `run_integration_test` / `_apply_prefix_to_ids` と専用フィールド）を削除。本番経路 `integrate_to_book` は不変。
- **I-14 反映済み**（Task 4 で配線維持）: Phase 5 の章「## レジュメ」描画（`phase5_export.py`）は `resume_content`（Phase 2 章レジュメ）供給を維持したまま存続。供給元の付け替えは不要だった（章レジュメは Phase 2 で今も生成され、Phase 4 と Phase 5 の両方へ流れる）。※書籍モードの Phase 5 描画の実 E2E 確認は本セッションでは未実施（書籍スモークはユーザー実施予定）。

**あわせて実施**: 翻訳の直前訳ウィンドウを断片 3 件×200 字トリムから連続 ~2,000 字（段落丸ごと・末尾から遡り WINDOW_MAX_CHARS 上限）へ変更（Task 6, commit `2a175bd`）。論文モードの `{resume_content}` 配線漏れも `build_translation_context` の新設で解消（両モード統一）。

**E2E 検証**: 論文モードのゴールデン検証を実施（`data/input/paperplain/NST/NSTsample.txt --lite`）。`phase3_structure.json` で期待 14 見出し（`Conclusion` 独立 h2 含む、I-8 修正維持）を確認、Phase 4 デバッグプロンプトの `<resume_content>` に 4,227 字の論文レジュメが実注入されていることを確認（Stage 1 配線が実走行で機能）。書籍スモークとモデル A/B・比較読みはユーザー実施予定（Stage 1 のスコープ外）。

### （関連・スコープ外）書籍章処理が pdf_mode=full_vlm 固定

- `--book` に渡した `pdf_mode`（既定 hybrid）は `book_manager.py:170,173-174` で `pipeline_kwargs` から pop され、`:214` で `pdf_mode="full_vlm"` にハードコードされるため、章処理では常に full_vlm になる。これは `CLAUDE.md` の設計原則「複雑なレイアウトでは Route C（全ページ VLM）を優先し中途半端な混在モードは避ける」および `requirements_log.md` の「Book Mode・Route C の Markdown 構造化」記述と整合する**意図的な設計**。ただしデジタル書籍にはコスト過剰であり、適応ルーティング（`is_docling_viable()` 併用）の余地がある。これは上流（VLM ルート分岐）の別課題として `requirements_log.md` に候補改善登録し、コスト実測＋構造品質 A/B を伴う独立スペック（Spec B, 未起案）で扱う。Spec A（プロンプト整理）とは疎結合であり、どちらを先にやっても手戻りは発生しない。
- **追記（2026-07-10）**: Spec B 前提調査で、この「full_vlm 固定」は実際には機能していないことが判明した（下記 I-15/I-16）。

## 2026-07-10: 翻訳コンテキスト再設計の前提調査（Spec B）で発見した Phase 1 ルーティングの重大バグ 2 件（未対応・Spec B で対処予定）

書籍モードの VLM 適応ルーティング（Spec B）の設計前調査として Phase 1 の実働経路を静的解析＋実行時検証した結果、「全ページ VLM（Route C）」という前提そのものが崩れていることが判明した。対策は `docs/superpowers/specs/` の Spec B（2026-07-10 起案）で扱う。

### I-15. VLM スライディング OCR が二重定義バグで全ページ必ず失敗し、ネイティブテキストへ静かにフォールバックしている

- **事象**: `core/engine/p1_ingest/ocr_manager.py` の `OCRManager` に `process_page_vlm` が**同一クラス内で二重定義**されている（`:157` 画像引数版 / `:214` pdf_path 引数版）。Python の規則で後者が生存する（`inspect.signature` により実行時確認済み: `(self, pdf_path: str, page_num: int)`）。ところが唯一の呼び出し元 `pdf_ingester.py:67` は前者のシグネチャ（`curr_img, prev_img=..., page_idx=..., session_dir=...`）で呼ぶため、**毎ページ必ず TypeError** となり、`pdf_ingester.py:70-80` の except で握りつぶされてネイティブ PDF テキスト抽出にフォールバックする。テキスト層のないスキャン PDF では「[VLM抽出失敗]」が並ぶ。
- **影響**: full_vlm モードは、二重定義がファイル初出コミット a4c7fa4（2026-04-03, Stable V3.2.1）から存在するため、**エンジン層の現行構成では一度も VLM OCR を実行していない可能性が高い**。Route C（Markdown 構造化）の前提となる VLM Markdown 見出しは生成されない。CLAUDE.md の設計原則「VLM の論理役割判断が最優先」は現状コードでは実態を持たない。
- **対策方針**: Spec B で修理する。スキャン書籍（見開き含む）は今後も処理予定のため必須項目。修理後にスキャン PDF での実動作確認（VLM が実際に呼ばれ Markdown が返ること）を行う。

### I-16. pdf_mode=full_vlm 指定でも Docling が優先される（書籍モードの実働経路は Docling＋TOC フォールバック）

- **事象**: `phase1_preprocessor.py:141` の Docling 分岐は `max_pages is None and is_docling_viable(pdf_path)` のみを見て **`pdf_mode` を参照しない**。したがって書籍モードの full_vlm 固定にかかわらず、デジタル PDF は Docling ルートに入る。`data/input/Booksample/` の 3 冊はすべて `is_docling_viable=True` を実測確認（corfra 106p / pse 175p / relations 282p、テキスト層クリーン）。さらに Docling 出力（`docling_ingester.py:70-81`）は `role` 属性のみで本文に `#` Markdown を付与しないため、Phase 3 の Route C（`structure_nodes_by_markdown` の Markdown 正規表現）は空振りし、`phase3_structure.py:70-77` の TOC/ChapterParser フォールバックが実働経路になっている。
- **含意**: 書籍モードの構造化品質は、意図された「VLM Markdown 経路」ではなく **Docling＋TOC フォールバック経路**の産物。requirements_log（2026-07-07）が見込んだ「デジタル書籍で 10〜50 倍のコスト削減余地」は、VLM が動いていない以上、事実上すでに享受されている。I-15 と合わせて、Phase 1（Docling 優先）と Phase 3（full_vlm 前提の Route C）の**前提不一致**が Spec B の技術的核心。
- **対策方針**: Spec B で「デジタル書籍＝ Docling を正式ルート化（role 見出しを書籍 Phase 3 に配線）／スキャン書籍＝ VLM（I-15 修理後）」として公式化し、Phase 3 の分岐条件を「指定された pdf_mode」ではなく「Phase 1 が実際に使ったルート」参照に改める。


## 2026-07-11: モデル A/B（Stage 1 後）の下ごしらえ中に発見したレジュメ切断バグ

### I-17. thinking モデル（gemini-3.5-flash）でレジュメが MAX_TOKENS 途中切断される（本番有料モードにも影響）

- **事象**: `phase2_meta.py::generate_resume` は `max_output_tokens=8192` 固定でレジュメを生成していた。`gemini-3.5-flash`（thinking モデル）を `thinking_level="High"` で呼ぶと、**thinking トークンが 8192 枠を食い尽くし、レジュメ本文が MAX_TOKENS で途中切断**される。Stage 1 後のモデル A/B（`DEFAULT_MODEL_RESUME=gemini-3.5-flash` の「レジュメのみ 3.5-flash」ハイブリッド腕）を NST で実走行した際、生成レジュメが **533 字**で `# 2. 核心的主張` の途中（「…補完」）で途切れているのを発見。同条件の lite 腕（`gemini-3.1-flash-lite`）は thinking が軽く **4,347 字**で完結していた。
- **影響範囲（重要）**: `coreprompts.json` の `DEFAULT_MODEL` は `gemini-3.5-flash` であるため、**`--lite`/free ティアを使わない通常の有料モードでは、以前からレジュメが同様に切断されていた**（`--lite` 運用で長く隠れていた潜在バグ）。切断されたレジュメは Phase 4 の翻訳コンテキスト（`{resume_content}`）に注入されるため、翻訳品質にも影響しうる。
- **根本原因**: `_build_gemini_config`（`llm_client.py`）が `max_output_tokens` に thinking トークンを含む枠を渡す仕様。resume 呼び出しの 8192 は、thinking を持たない/軽いモデル時代の値で、thinking モデルには過小。
- **対策（実施済み）**: `generate_resume` の `max_output_tokens` を `8192 → 32768` に引き上げ（thinking＋目標 4000〜5000 字＋余裕を確保）。修正後の再実行で 3.5-flash レジュメは **11,450 字**で完結（section 3「各セクションの展開」含む、末尾正常）。lite 腕は元々 8192 内で完結していたため挙動不変。単体テスト 218 件全合格（テストは call_gemini をモックするため値変更の影響なし）。
- **観測メモ（A/B の設計入力）**: 3.5-flash レジュメは 11,450 字と lite（4,347 字）の約 2.6 倍で、プロンプトの目標「4000〜5000 字」を大きく超える。文脈としては richer だが、毎バッチ注入されるためコスト・文脈長への影響は比較読みで評価対象。
- **フォローアップ候補（未実施）**: `call_gemini`/`call_gemini_async` で finish_reason == MAX_TOKENS を検出した際に警告ログを出す（レジュメ以外でも silent truncation を早期検知するため）。

## 2026-07-12: 翻訳コンテキスト Stage 2（統合用語レイヤー）実装で対処した不具合

### I-18. 用語集パイプラインが dict[str,str] 固定で definition が 2 箇所で欠落し、翻訳プロンプトに一度も届いていなかった

- **事象**: Phase 2 のキーワード抽出（`KEYWORD_EXTRACTION_PROMPT` / `keywords_data`）は用語ごとに `definition`（本文中の定義・特殊な語義の説明）を抽出済みだったにもかかわらず、この情報が Phase 4 の翻訳プロンプト `<glossary>` まで一度も到達していなかった。
- **根本原因（2 箇所の欠落）**:
  1. `core/config.py::load_glossary_csv`（用語集 CSV ローダー）が en/ja の 2 列だけを読み、CSV の 3 列目（definition）を無視して `dict[str,str]`（en→ja）を返す実装だった。
  2. `core/phase4_translate.py`（旧実装、`:96-98` 付近）が `p2_data["keywords_data"]` を用語集に組み込む際、`{kw["en"]: kw["ja"] for kw in keywords_data}` のような形で en→ja のみを取り出してフラット化しており、`keywords_data` に載っていた `definition` フィールドをここで捨てていた。
  - 2 箇所とも「用語集＝訳語の対応表（dict[str,str]）」という当初のデータモデルに固定された結果、後から追加された `definition` フィールドの受け皿がどこにも用意されていなかった。`TranslationPromptBuilder.glossary` の型注釈も `dict[str,str]` のままで、型シグネチャ上も definition を運べない設計になっていた。
- **影響**: 訳語だけでなく定義（特に「日常語が理論的・特殊な意味で使われている」ケースの語義の手がかり）を翻訳 LLM に与えることで訳語の一貫性・精度を上げる、という用語集本来の狙いが、実装上ずっと機能していなかった。書籍モードの `global_glossary.csv`（definition 列あり）も同様に定義が捨てられていた。
- **対策**: `core/config.py` に 3 列（en, ja, definition）対応の `load_glossary_entries` を新設（`load_glossary_csv` は既存呼び出し元互換のため dict 版として残置）。`core/engine/p4_translate/term_layer.py` に `TermEntry(en, ja, definition, source)` と `build_term_layer(keywords_data, glossary_entries)` を新設し、`keywords_data`（本文抽出、definition 優先）と glossary CSV（訳語 `ja` 優先、definition は空欄補完のみ）をフィールド単位でマージする専用ロジックに隔離。`phase4_translate.py` を `build_term_layer` 経由に配線し直し、`TranslationPromptBuilder.glossary` の型を `list[TermEntry]` に変更。`format_term_layer` で定義付きエントリを先頭に `- en → ja：定義` 形式で描画するようにし、definition が初めて `<glossary>` に載るようにした。
- **副次的に発見した回帰（同一 Stage 内、コードレビューで検出）**: Stage 2 でハイブリッド構成（`DEFAULT_MODEL=gemini-3.1-flash-lite` / `DEFAULT_MODEL_RESUME=gemini-3.5-flash`）を既定化した際、`core/book_manager.py` の書籍全体レジュメ生成（旧 `:72` 相当）と各章の `run_pipeline()` 呼び出し（旧 `:212` 相当）が resume 用途のモデルルーティングを経由せず、`DEFAULT_MODEL`（lite）へフォールバックしてしまう設計だったことが判明した。Stage 1 時点では `DEFAULT_MODEL` が実質 resume と同じ lite だったため症状が出ておらず、ハイブリッド既定化で `DEFAULT_MODEL` を lite に固定し `DEFAULT_MODEL_RESUME` を切り離したことで初めて顕在化した「静かな書籍モード限定の劣化」である。**NST 論文モードの比較読みでは検出できない**（論文モードは book_manager を通らないため）。
  - **対策**: `book_manager.py` の該当 2 箇所を `get_default_model("resume")` 経由／`self.model` の明示渡しに変更し、書籍全体・章レジュメが用途別ルーティングに正しく乗るよう修正（commit `1033e83`）。
  - **教訓**: モデルティアのデフォルト値を変更する際は、purpose 別ルーティング（resume 等）を経由せず定数を直接参照しているコードパスが他にないか、変更前に横断確認する必要がある。今回は Stage 2 の自己レビューで発見できたが、次回同種の変更（`DEFAULT_MODEL_*` 系の値変更）では `grep -rn "DEFAULT_MODEL\b" core/` 等での横断確認をチェックリスト化することを検討する。
- **検証**: `tests/unit/test_config.py`（`load_glossary_entries` 4 件）、`tests/unit/test_term_layer.py`（`build_term_layer`/`format_term_layer` 10 件）、`TranslationPromptBuilder` 関連 2 件、`coreprompts.json` の Stage 2 既定値 2 件、書籍 resume routing 回帰 1 件を含む新規テストを追加。単体テスト 211 → 237 件全合格（回帰なし）。ゴールデン構造回帰・書籍スモーク（resume モデル表示の実地確認）はユーザー実施予定（有料 API 実行を伴うため本タスクのスコープ外）。
- **判断保留⑤（許容判断として確定）**: Web 版で管理者パスコード経由のサーバー側キー（無料モード）を使う場合、レジュメ生成が `DEFAULT_MODEL_RESUME`（3.5-flash、無料枠の対象外）を消費する。ハイブリッド構成による訳質向上のメリットを優先し、現時点ではこれを許容する。コスト面で問題が顕在化した場合は、無料モード時のみ resume も lite にフォールバックする分岐を別途検討する。
