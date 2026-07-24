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
- **対応済み（2026-07-18, Spec B 実装）**: `ocr_manager.py` の pdf_path 引数版（旧 :214）を削除し、呼び出し元 `pdf_ingester.py:67` と一致する画像引数版（旧 :157）を正とした。

### I-16. pdf_mode=full_vlm 指定でも Docling が優先される（書籍モードの実働経路は Docling＋TOC フォールバック）

- **事象**: `phase1_preprocessor.py:141` の Docling 分岐は `max_pages is None and is_docling_viable(pdf_path)` のみを見て **`pdf_mode` を参照しない**。したがって書籍モードの full_vlm 固定にかかわらず、デジタル PDF は Docling ルートに入る。`data/input/Booksample/` の 3 冊はすべて `is_docling_viable=True` を実測確認（corfra 106p / pse 175p / relations 282p、テキスト層クリーン）。さらに Docling 出力（`docling_ingester.py:70-81`）は `role` 属性のみで本文に `#` Markdown を付与しないため、Phase 3 の Route C（`structure_nodes_by_markdown` の Markdown 正規表現）は空振りし、`phase3_structure.py:70-77` の TOC/ChapterParser フォールバックが実働経路になっている。
- **含意**: 書籍モードの構造化品質は、意図された「VLM Markdown 経路」ではなく **Docling＋TOC フォールバック経路**の産物。requirements_log（2026-07-07）が見込んだ「デジタル書籍で 10〜50 倍のコスト削減余地」は、VLM が動いていない以上、事実上すでに享受されている。I-15 と合わせて、Phase 1（Docling 優先）と Phase 3（full_vlm 前提の Route C）の**前提不一致**が Spec B の技術的核心。
- **対策方針**: Spec B で「デジタル書籍＝ Docling を正式ルート化（role 見出しを書籍 Phase 3 に配線）／スキャン書籍＝ VLM（I-15 修理後）」として公式化し、Phase 3 の分岐条件を「指定された pdf_mode」ではなく「Phase 1 が実際に使ったルート」参照に改める。
- **対応済み（2026-07-18, Spec B 実装）**: `phase1_preprocessor.py` が `pdf_mode` を尊重するよう修正し、実ルートを `phase1_route.json` に記録。`BookManager` に書籍単位ルーティング（①〜④）を実装し `pdf_mode` の pop・破棄を解消。`phase3_structure.py` の Route C 発火条件を実ルート参照に変更し、Docling ルート×書籍モードでは新設の `structure_nodes_by_role` が role 見出しを直接構造化する（従来の ChapterParser/TOC フォールバックは role 見出しが乏しい場合のみ使用）。

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

### I-19. thinking モデルの空レスポンス（finish_reason=MALFORMED_RESPONSE 等）を call_gemini が無言で "" として返し、レジュメが静かに欠落する

- **事象**: Stage 2 マージ後の論文 NST E2E 実行（ハイブリッド既定・PAID）で、`resume_content` が 0 文字になり、出力 `_p2.md` にレジュメが載らず、かつ **翻訳プロンプトにもレジュメが届かない**状態が発生した。ログ上は `[Phase 2] レジュメ生成完了 (0 文字)` とだけ出て、例外・リトライは一切発生していなかった。A/B の armB（同一入力・同一 3.5-flash）では 11,450 字のレジュメが生成できていたため、間欠的な失敗。
- **根本原因**: Gemini API が `gemini-3.5-flash`（thinking=High）のレジュメ生成で **`finish_reason=MALFORMED_RESPONSE`（候補トークン 0）** を返した（ログに `MALFORMED_RESPONSE is not a valid FinishReason` / `Output: 0tk`）。これは多くの場合 transient なモデル側の生成失敗。ところが `core/llm_client.py` の空レスポンス判定が `if chunk is None and not full_response_text:` と狭く、**「chunk は届いているが text だけ空」**（usage_metadata 付きの最終 chunk はあるが `chunk.text` が全て空）のケースを異常と見なさず、`return full_response_text`（= ""）で無言で空を返していた。sync（`call_gemini` 旧 `:274`）・async（`call_gemini_async` 旧 `:371`）の両方に同じ狭い判定があった。
  - I-17（max_output_tokens=8192 過小による MAX_TOKENS 途中切断）とは別物。今回は Output 0 トークンで、token 枯渇ではなく finish_reason 異常。ただし「thinking モデルが本文テキストを 1 文字も返さない」という同じ失敗表面を共有する。
- **影響**: レジュメ生成が間欠的に無言で失敗し、① Phase 4 翻訳がレジュメ文脈なしで実行され訳質が劣化、② 出力にレジュメが載らない、という静かな品質劣化が起きる。間欠バグのため大半の実行では表面化せず、**比較読み等の評価を交絡させる**（レジュメ有無が意図せず混入する）危険があった。実際に Stage 2 の用語レイヤー比較読みがこの状態で行われ、評価をやり直すことになった。
- **対策**: sync/async 両方の空判定を `if not full_response_text:` に広げ、**テキスト出力が空なら常に `RuntimeError` を送出**するようにした（`core/llm_client.py`）。これによりリトライループ（既定 `max_retries=5`）が拾い、transient な MALFORMED_RESPONSE は再試行で回復し、恒久的に空なら無言で "" を返さず例外で顕在化する。全呼び出し（レジュメ・キーワード・翻訳・DNA）で「空テキスト＝異常」は共通して正しい。
- **検証**: `tests/unit/test_llm_client.py` に sync/async の「空テキスト応答 → RuntimeError」2 件と、正常応答がそのまま返る sync 1 件を追加（計 3 件）。単体 237 → 240 全合格。修正後の NST 再実行でレジュメ生成が回復することを確認する（ユーザー実施の再比較読みの前提）。
- **教訓**: ストリーミング応答の「完了はしたが本文が空」は `chunk is None` では捕捉できない。生成系 API 呼び出しでは「空出力は常に失敗」として扱い、finish_reason の種類（MAX_TOKENS / MALFORMED_RESPONSE / SAFETY / RECITATION）に関わらずリトライ・顕在化させるのが安全。

## 2026-07-13: 書籍スモークテスト（relationspdf.pdf）で発見・対処した不具合

### I-20. gemini-3.5-flash の実効入力上限がドキュメント記載値（1,048,576 tok）より遥かに低く、書籍全文スキャンで 400 INVALID_ARGUMENT

- **事象**: レジュメ生成プロンプト接地性強化（commit `266091a`）後の書籍モード e2e スモークテスト（`data/input/Booksample/relations/relationspdf.pdf`, 282p/786,303字, Docling ルート）で、Phase 0（`BookManager._generate_global_context`、書籍全文スキャンからのグローバルレジュメ生成）が起動直後に `400 INVALID_ARGUMENT` で 5 回リトライ後に例外終了し、書籍処理全体がクラッシュした。
- **調査**: 最小再現スクリプトで `gemini-3.5-flash` に同一入力（786,303字＝ `count_tokens` 実測 212,500 tok）を渡すと thinking の有無・`max_output_tokens` の大小に関わらず一貫して 400 で失敗する一方、`gemini-3.1-flash-lite` は同一入力で成功することを確認。文字数で二分探索した結果、失敗の閾値は **734,997字（185,337 tok）= OK / 738,015字（187,031 tok）= FAIL** の間にあった。`docs/gemini_models.md` が公式ドキュメントから引いている入力上限「1,048,576 tok」とは大きく乖離しており、**GA 版 `gemini-3.5-flash` はこのアカウント/API バージョンにおいて実質 ~186,000〜187,000 tok 前後の単発リクエスト上限を持つ**（少なくとも本検証時点、2026-07-13）。この上限を超えるリクエストはリトライしても回復しない決定論的な失敗であり、`call_gemini` の既存リトライループ（5 回、指数バックオフ）は毎回同じ 400 を受けて無駄にリトライ・待機していた。
- **背景**: 2026-07-12 の Stage 2 既定化（`DEFAULT_MODEL_RESUME=gemini-3.5-flash`）＋書籍レジュメ routing 修正（I-18 内の副次修正）により、`book_manager.py::_generate_global_context` の全体レジュメ生成が `get_default_model("resume")`（＝3.5-flash）を明示的に使うようになっていた。この変更時の検証は NST/AL 論文（数万字規模）に閉じており、**書籍の「全文スキャン」という桁違いの入力規模ではテストされていなかった**ため、今回まで顕在化しなかった。
- **対策**: `core/book_manager.py` に `RESUME_MODEL_SAFE_CHAR_LIMIT = 600_000`（実測しきい値 734,997字に対し安全マージンを確保）を追加。`_generate_global_context` の全体レジュメ生成で、全文がこの字数を超え、かつユーザーが `--model` を明示指定していない場合は resume モデル（3.5-flash）を使わず既定モデル（`gemini-3.1-flash-lite`）にフォールバックする。`--model` 明示指定時はユーザーの選択を尊重しガードを適用しない。章単位のレジュメ生成（`run_pipeline` 経由、`core/phase2_meta.py`）は章テキスト規模（実測 5〜9万字程度）が閾値を大きく下回るため対象外＝ Task 8 の resume routing はそのまま機能する。
- **検証**: 単体テスト 239 件全合格（既存挙動に影響なし、新規テストは追加せず＝定数分岐のみで `call_gemini` モック前提の既存テストと独立）。修正後に同一書籍で `--max-chapters 4`（前付け2つ＋実質章1・2）を再実行し、Phase 0 が `gemini-3.1-flash-lite` へのフォールバックで正常完了、以降 4 ユニット（Preface/Introduction/Chapter1/Chapter2）が完走・統合されることを確認。`golden-verification` で階層（英語nested/日本語parallel）・章統合・除外セクション（References/Notes/Index が分割時点で正しくスキップ）を確認、構造回帰なし。章単位レジュメでは `gemini-3.5-flash` 使用が維持されていることをログで確認済み（Task 8 routing 健全）。
- **副次観測（対応不要・記録のみ）**: PDF 章分割時に `MuPDF error: format error: cannot find object in xref (1 0 R)` という警告がページ抽出のたびに出力されたが、処理は正常続行・最終出力に実害なし（この PDF 固有の軽微な xref 構造上の特性と推測）。また Docling 抽出テキストに `twentieth- century` のような行送りハイフネーションの不完全な結合（本来 `twentieth-century` となるべき箇所にスペースが残る）が散見されたが、今回の変更とは無関係の既存の抽出品質特性であり、今回のスコープ外。
- **教訓**: (1) Gemini モデルの「公称の入力コンテキスト上限」と「単発リクエストの実効上限」は乖離しうる。`gemini_models.md` の数値は Google 公式ドキュメントの転記であり、実運用での検証が別途必要。(2) resume routing のような「用途別モデル選択」の既定値を変更する際は、その用途で発生しうる**最大の入力規模**（論文レジュメ数万字 vs 書籍全文スキャン数十万字）を横断的に洗い出してから適用範囲を決めるべきだった。(3) 400 系のような決定論的エラーを無差別にリトライする現行の `call_gemini` 設計は、今回のような「入力サイズに起因する恒久的失敗」では時間とコストの無駄になる。エラー種別（decoded body の `status`）に応じてリトライ要否を判断する改善は今回は見送ったが、次に同種の問題が出た場合の候補として記録しておく。

## 2026-07-18: Spec B 実PDF検証（corfrapdf.pdf）で発見した不具合（未対応・記録のみで棚上げ）

### I-21. VLM スライディング OCR が、本文の乏しい図版ページを直前ページの内容で誤って埋めてしまう（ページ内容の重複）

- **事象**: Spec B（書籍モード Phase 1 入力ルーティング修理）の実PDF検証として `data/input/Booksample/corfra/corfrapdf.pdf`（見開きスキャン×Doclingビュー可能、規則②により VLM ルート選択）の「1 Arbitrary Location」章を処理したところ、出力 `_p2.md` の英語原文・日本語訳の両方で同一段落（"scorn on their keenness to hang rusty sheep shears..." 以下、約1,375字）が2回連続して出現した。
- **調査**: `phase1_preprocessor.json` の時点で既に重複していることを確認（`chunk_9`/`page_idx=2` と `chunk_12`/`page_idx=3` が byte-for-byte 同一の1,375字テキスト）。章単位の分割PDF（`03_1_Arbitrary_Location.pdf`）を `fitz` で直接検証したところ、page_idx=2 は本文3,328字を含む通常ページ、page_idx=3 は **ネイティブテキストわずか23字（"figure i.i. A crucetta"というキャプションのみ）・埋め込み画像3枚の図版ページ**であり、元PDFの物理ページ自体は重複していない。VLM スライディング OCR（`ocr_manager.py` の `_merge_images_horizontal` による前後ページの2-up結合、`VLM_CONTINUITY_PROMPT`）が、本文のほぼ空な図版ページ（page_idx=3）を処理する際に、結合された直前ページ（page_idx=2、本文3,328字）の内容をそのまま再転写して返してしまったと推測される。
- **再発確認（同一章内、2026-07-18 同日追加調査）**: ユーザーからの指摘で同じ章内の別箇所（見出し「A Corsican Whole」）でも同型の重複を確認した。`chunk_68`（h1, page_idx=17, "A Corsican Whole"）とその本文（`chunk_69`〜`71`）が、`chunk_73`（p, page_idx=18, "A Corsican Whole"）以降（`chunk_74`,`75`...）で丸ごと再出現している。page_idx=18 の物理ページはネイティブテキスト109字（"figure 1.2. Corsica. Courtesy of..."という図版キャプションのみ）で、page_idx=17 の見出し・本文とは無関係の内容である。**1つの章（30ページ）中で2箇所発生**しており、偶発的な端症例ではなく、**図版ページを含む章では構造的に再現しうる**パターンである可能性が高い。role 判定も一貫しない（1回目は元の役割を保持したまま重複、2回目は本来 h1 のはずの見出しが role="p" として重複）。
- **Spec Bとの関係**: このコードパス（VLM プロンプト・スライディング結合ロジック）は Spec B では一切変更していない。ただし I-15（`process_page_vlm` 二重定義バグ）の修理により VLM が実際に初めて動作するようになったことで、**これまで一度も実行されたことがなかったこの失敗モードが初めて顕在化した**。VLM が動かない間は全てネイティブテキストへ静かにフォールバックしていたため（I-15参照）、この重複は起こり得なかった。
- **既知の類似事象との関係**: `requirements_log.md`（2026-07-13, relationspdf.pdf フルラン）に記録済みの「見出し重複端症例」（Conclusions章で本文冒頭が誤って見出しノードとして重複検出された事例）とは異なる層・異なる原因。あちらは Docling ルート×Phase 3 見出し検出ロジックの端症例、本件は VLM ルート×Phase 1 OCR スライディング結合の端症例であり、偶然テーマ（重複）が同じだが根本原因は別。
- **対応方針（ユーザー判断・2026-07-18、再発確認を経て確定）**: 記録のみで棚上げ。同一章内で2回発生することを確認し頻度の見立てを更新した上で、改めてユーザーに確認した結果、Spec B のスコープ外として記録のみに留める判断が確定した。Spec B のスコープ（書籍単位ルーティング判定・実ルート記録・Docling role構造化）は本件と無関係に正しく機能していることを実PDFで確認済みのため、Spec B 自体は完了とする。VLM スライディング OCR の品質改善（本文の乏しいページの検出・スキップ、または前ページとの類似度チェックによる重複検出）は別スペックとして今後扱う。
- **再現手順**: `python3 main.py data/input/Booksample/corfra/corfrapdf.pdf --book --max-chapters 3`（`--pdf-mode` 未指定、規則②により自動的に VLM ルート選択）を実行し、`corfrapdf_p2.md` の「1 Arbitrary Location」章、"scorn on their keenness" を含む段落の出現回数を確認する。
- **教訓**: 長らく機能していなかった処理経路（今回は VLM OCR）を修理する際は、「動くようになったこと」自体の確認だけでなく、実際に流したコンテンツを人間が読んで品質を確認する工程が不可欠である。ルーティング・呼び出し成功のログだけでは、この種のコンテンツレベルの静かな劣化は検出できない。

> **[2026-07-20 解決]** ブランチ `feature/chapter-boundary-adjudication` で解決。真因は
> **実装が元設計から逸脱していたこと**だった。元設計の意図は「画像は現ページのみ渡し、
> 前ページは OCR テキストをヒントとして渡す」だったが、実装は 2-up 画像結合
> (`_merge_images_horizontal`) になっており、図版ページ（抽出対象=右がほぼ空白・隣=左が
> 本文だらけ）で VLM が「右だけ」の指示に反して左（前ページ）を書き起こしていた。
> 対策は挙動（なぜ VLM が左を書くか）ではなく**構造**を潰す方針を採り、VLM に渡す画像を
> 常に現ページ1枚だけにした。前ページ文脈は second image ではなくネイティブテキストで
> 渡す（`build_prev_contexts` → `VLM_SINGLE_PAGE_PROMPT` の {prev_context}）。1画像に
> 対象ページしか入らないため、隣ページの書き起こしが構造的に起きない。図版ページ検出の
> ヒューリスティックは採らなかった（真の引き金は「図版であること」ではなく「対象が隣より
> 極端に文字が少ないこと」で、章の短い頁も巻き込む雑な signal になるため。構造を潰せば
> 検出は不要）。文脈にネイティブテキストを使うのは並列処理を直列化させないため。実PDF検証で
> corfra「1 Arbitrary Location」章の重複段落・重複見出しが解消したことを確認。詳細は
> `docs/superpowers/specs/2026-07-20-vlm-single-page-ocr-design.md`。

### I-22. PDFSplitter の章境界ページ補正が実際の抽出範囲と一致せず、隣接章の内容が数ページ単位で混入する

- **事象**: `corfrapdf.pdf` フルラン（全10章）完了後、ユーザーが出力を確認したところ、2箇所で章境界の混入を発見した。(1) 第7章「Knowing」の出力末尾に第8章「Anonymous Introduction」のタイトルが混入。(2) 第4章「Things」の出力冒頭に、本来は第3章「Place」末尾にあるはずの見出し「Difference Out of Similarity, Similarity Out of Difference」が混入。
- **調査**: 章単位の分割PDFを `fitz` で直接検証。
  - `06_4_Things.pdf`（ログ上「ページ補正: '4 Things' 論理P85 → 物理P90」と補正されたはず）の実際の先頭ページ群には "Place | 81" 等、**第3章のランニングヘッダーが付いた物理P81前後のページが含まれていた**。ログが報告した補正後ページ番号（物理P90）と、実際に抽出されたページ範囲が一致していない。
  - `09_Knowing.pdf`（物理P156-173想定）の末尾から2ページ目（page 16、ファイル内インデックス）に第8章のタイトルページ（"8 | Anonymous | Introduction"）が挟まっており、その直後の最終ページ（page 17、原書ページ番号164）は本来の第7章内容に戻っている。**ページ順そのものが前後している**可能性がある。
- **Spec Bとの関係**: `PDFSplitter`（LLM TOC抽出＋ページ補正、`core/engine/p1_ingest/pdf_splitter.py` 系）は Spec B が一切変更していないコンポーネントで、書籍単位のルーティング判定（`BookManager._decide_book_pdf_mode`）よりも前段の、章分割そのものの処理。したがって Spec B のスコープ外だが、これも I-21 と同様、**VLM が実際に処理されるようになったことで初めて中身を精査する機会が生まれ顕在化した**可能性がある（I-15/I-16 修理前は書籍全体が Docling+TOCフォールバックか native_fallback で処理されており、章境界の物理ページ精度を人間が読んで確認する場面自体がなかった）。
- **対応方針（ユーザー判断・2026-07-18）**: 記録のみで棚上げ。Spec B のスコープ（VLM修理・書籍単位ルーティング判定・Docling role構造化）はこの章分割境界の問題と独立に正しく機能しており、Spec B は完了とする。`PDFSplitter` のページ補正精度改善は別途調査・対応する。
- **教訓**: I-21 と合わせ、書籍全文を通した「読んで確認する」検証が、ログ上は正常終了に見える処理でも見つかる問題があることを示した。今後、長期間動いていなかった処理経路の修理では、構造検証（見出し数・階層等の機械チェック）に加えて人間による通読サンプリングを検証工程に含めることを検討する。

> **[2026-07-19 原因確定]** 当初の調査記録にあった「②`09_Knowing.pdf` の末尾でページ順そのものが前後している可能性」は**誤読**だった。この本の偶数頁（verso）ランニングヘッダーは章名を含まない汎用形式 `164 | Corsican Fragments` であり、第8章の物理頁164が第7章の続きに見えたのは、単にこのヘッダーが章名を運ばない書式だからにすぎない。`new_doc.insert_pdf(doc, from_page=start_page, to_page=end_page)` は常に連続範囲を抽出しており、ページ順が入れ替わったことは一度もない。実際の原因は、次章（第8章 Anonymous Introduction）の開始位置検出が真の扉頁（idx171）より2頁遅れて確定していたことで、`end_page = 次章の start_page - 1` が第8章の扉頁（idx171）を第7章側に飲み込んでいた（症状①「Things 冒頭への前章ヘッダー混入」も同根：`_matches_heading()` の前方一致誤マッチ＋first-match-wins が誤った開始位置を確定させていた）。根本原因の `_matches_heading()` 誤マッチ・first-match-wins は、`_classify_match()`（隣接行によるランニングヘッダー/章扉判別）＋`_score_candidate()`（窓内全候補の採点）への置き換えで解消した。詳細は `docs/superpowers/specs/2026-07-19-chapter-splitting-accuracy-design.md` §2.1、実装は本ブランチ Task 1〜4（`core/engine/p1_ingest/pdf_splitter.py`）。修正後の実PDF検証で、症状①②とも corfra 実データ上で再発しないことを確認済み（`.superpowers/sdd/task-5-report.md`）。

## 2026-07-18: Spec B `is_book` 配線バグの発見・修正（relations.pdf 実データ検証中）

### I-23. `core/pipeline.py` が `run_phase3()` に `is_book` を渡しておらず、書籍モードの Phase 3 分岐が本番では一度も実行されていなかった

- **事象**: Task 5 で新設した Route D（Docling role 見出し構造化）を relations.pdf（Docling ルート）で実データ検証したところ、`phase1_route.json` が正しく `{"route": "docling"}` を記録し、role=h1/h2 の見出しチャンクも存在するにもかかわらず、Route D が発火しなかった。
- **根本原因**: `core/pipeline.py` の `run_phase3(...)` 呼び出しに `is_book=is_book` が渡っていなかった（`run_phase3` のデフォルト値 `is_book=False` が常に使われる）。`git show <Spec B着手前のコミット>:core/pipeline.py` で確認したところ、この欠落は Spec B 着手前から存在する既存バグであり、本セッションの Task 1-5 のどのコミットも `core/pipeline.py` に触れていない。`run_phase3` の唯一の本番呼び出し元が `core/pipeline.py` であることは `grep -rn "run_phase3(" core/` で確認済み。
- **影響**: 書籍モードの Phase 3 分岐（ChapterParser/TOC フォールバック、Route C、Task 5 の Route D）が、この関数が書かれてから一度も本番で実行されたことがなかった。書籍は常に「ペーパーモード」相当の構造化（`merge_role_headings` によるレジュメ見出し＋role h1/h2 フォールバックの統合）で処理されてきた。これが「たまたま許容できる品質」を出していたため、これまで気づかれずにいた。
- **副次的に発見した設計上の問題（Route C は実データでは発火しない死んだコード）**: Phase 1 の `Formatter.logical_split`（VLM 出力のパース）は、VLM が出力した Markdown 見出し記号 `# ` を `RawChunk.role`（h1/h2）に変換し、`chunk.text` からは `#` を除去する。そのため Route C（`re.match(r'^#\s+', chunk.text)` による Markdown 正規表現判定）は、VLM ルートの実データでは原理的に一致条件を満たさず、一度も発火しない。この状態は is_book 配線バグの有無に関わらず存在していた。
- **対策**:
  1. `core/pipeline.py` の `run_phase3(...)` 呼び出しに `is_book=is_book` を追加。
  2. `core/phase3_structure.py` の Route D 条件を `actual_route == "docling"` から `actual_route in ("docling", "vlm")` に拡張し、VLM ルートの書籍も Docling と同じ `structure_nodes_by_role` で処理するようにした（Route C が実質使えないため、VLM ルート書籍が is_book 修正後にそのまま ChapterParser／TOC フォールバック（ネイティブ PDF 再解析、スキャン本には無力）へ落ちる回帰を防ぐため）。
  3. relations.pdf の実データ検証で、章の最初の見出しが章ローカルな TOC 抽出結果と一致せず降格された場合、`structure_nodes_by_role` が空の「[Unlabeled Section]」ノードをトップレベルに紛れ込ませるバグを追加発見（`current_h2 is None` のみでの判定漏れ。降格後は `current_h3` のみが設定され `current_h2` は None のまま残るため）。`structure_nodes_by_markdown` にも同型のバグがあったため合わせて修正（未発火の死んだコードだが一貫性のため）。
- **検証**: `tests/unit/test_pipeline.py` を新設し、`run_pipeline(is_book=True/False)` → `run_phase3()` の配線を直接検証する回帰テストを追加（これまでのテストは `run_phase3` を直接 `is_book=True` で呼んでいたため、本番唯一の呼び出し元である `run_pipeline` を経由する配線漏れを検出できていなかった）。`tests/unit/test_phase3_structure.py` に VLM ルート×Route D 発火のテストと、降格章の空セクション回帰テストを追加。単体テスト 264 件全合格。relations.pdf の Preface 章（Docling ルート）を実データで再実行し、Route D が正しく発火（ログで確認）、出力に空の「[Unlabeled Section]」が混入しないことを確認した。
- **教訓**: (1) ユニットテストが対象関数を直接呼び出す場合、その関数の唯一の本番呼び出し元を経由する統合テストが別途無いと、「本番では一度も通らないキーワード引数」のような配線漏れを検出できない。(2) 長期間実行されたことのないコードパス（今回は書籍モード Phase 3 分岐全体）を修理する際は、ユニットテストの green だけでなく実データでの動作確認が必須。(3) TOC ベースの章見出しデモーション判定は「書籍全体を一括処理する」設計を前提にしており、`book_manager.py` が章ごとに独立した `run_pipeline()` を呼ぶ現在のアーキテクチャ（各 Phase 3 呼び出しが単一チャプター PDF のみを見る）とは相性が悪い（章ローカルな TOC 抽出が章自身のタイトルを含まないのは自然な帰結）。将来この判定ロジックに手を入れる場合は、この前提のズレを踏まえる必要がある。

## 2026-07-19: 章分割精度の改善（I-22 原因確定・I-24・I-25）

Spec B 完了により書籍モードで「スキャン書籍を最後まで読める」状態になったことで、章分割そのものの精度を通読で検証する機会が初めて生まれ、`PDFSplitter`（`core/engine/p1_ingest/pdf_splitter.py`）に既存不具合が複数件見つかった。3件（I-22/I-24/I-25）は経路上で連結しており、単一スペック `docs/superpowers/specs/2026-07-19-chapter-splitting-accuracy-design.md` として一括対応した。I-22 の原因確定は上記追記を参照。以下は新規2件。

### I-24. `_get_chapters_from_outline()` が PDF ネイティブ outline を無検査で信頼し、ページラベル由来のゴミを章目次として採用する

- **事象**: `PSEpdf.pdf`（175頁）を書籍モードで処理すると、章ではなく **1頁=1章の175個の「章」**が生成され、その各々が5フェーズのパイプラインを完走しようとする（約17倍のユニット数と全フェーズ分の LLM 呼び出しが発生する）。
- **調査**: `doc.get_toc()` の返り値を確認したところ、PSE の outline は `f1, f2, f3 … f175` という175件（全て level 1）で構成されていた。これはスキャンソフトが付与したページラベルであり、章目次ではない。`_classify_role("f1")` は既定の `"chapter"` を返すため `target_roles` を全件通過してしまう。
- **原因**: `_get_chapters_from_outline()` が `doc.get_toc()` の結果を妥当性検査なしで章リストとして返していた。Route 2（outline）は Route 3（LLM TOC 抽出）より優先されるため、この経路にひとたび入るとページラベルが無検査で章目次として確定してしまう。
- **対策**: `_is_plausible_outline()` を新設し、`_get_chapters_from_outline()` が結果を返す前に妥当性を検査するようにした。棄却条件（いずれか該当で棄却）: (1) 1章あたり平均頁数が `OUTLINE_MIN_PAGES_PER_CHAPTER`（3頁）未満、(2) タイトルの過半数が共通接頭辞＋連番のページラベル形式（`OUTLINE_LABEL_SEQ_RATIO=0.5` 超）。棄却時は `None` を返し Route 3 へフォールバックする。定数はクラス先頭にまとめた。
- **再現手順**: `data/input/Booksample/PSE/PSEpdf.pdf` を `PDFSplitter.split()` に渡す（修正前は175件の章PDFが生成される）。修正後は `print_log` に `outline 棄却: 175件/175頁 = 1章あたり平均1.00頁が下限3頁未満` と出力され、Route 3 へフォールバックすることを確認した（実測ログは `.superpowers/sdd/task-5-report.md`）。
- **教訓**: PDF ネイティブの構造情報（outline・TOC・ラベル等）はスキャンソフトやオーサリングツールが機械的に付与したものである可能性があり、章目次として無検査で信頼してはならない。「章目次らしさ」を検査する簡易ヒューリスティック（頁密度・連番ラベル形式）は低コストで効果が大きい。

### I-25. `_extract_toc()` の TOC サンプリング窓が先頭固定で、目次が窓外にある書籍を分割できない

- **事象**: `Naven.pdf`（380頁）を書籍モードで処理すると、TOC 抽出が失敗し **380頁全体が単独章として処理される**（章分割が一切行われない）。
- **調査**: `_extract_toc()` はテキスト抽出の対象を先頭15頁固定（`range(min(15, len(doc)))`）、VLM フォールバックも先頭10頁固定にしていた。Naven の目次は idx 16〜24 にあり、両方の窓の外である。実測では、この窓で取得したサンプルテキストはわずか4,199字で `Contents`/`CONTENTS` を含まなかった。他3冊（corfra/relations/PSE）の目次は idx 2〜6 にあり窓内に収まっていたため、この不具合はテストコーパスの偏りにより検出されないまま残っていた。
- **原因**: TOC ページの探索窓が「先頭からの固定頁数」というハードコードされた前提で設計されており、目次の実際の位置を考慮していなかった。
- **対策**: `_find_toc_pages()` を新設し、固定窓を廃止して目次見出し（`TABLE OF CONTENTS` / `CONTENTS` / `目次`）を探索する方式に変更した。`TOC_SEARCH_PAGES`（30頁）の範囲で見出しを検出できた場合はその位置から `TOC_SAMPLE_PAGES`（8頁）を読む（discovery-hit）。検出できない場合は、見出し正規表現が実際の目次を捉え損ねた可能性（本文と結合・字間の空いた "C O N T E N T S" 等）を考慮し、従来の固定15頁挙動を `TOC_FALLBACK_PAGES` として維持し LLM 自身に目次を意味的に発見させる。窓・既定頁数はいずれもクラス先頭の定数として定義した。
- **再現手順**: `data/input/Booksample/Naven/Naven.pdf` を `PDFSplitter.split()` に渡す（修正前は `toc_data=None` のまま単独章フォールバックに落ちる）。修正後は `目次ページ検出: idx 16-23` とログに出力され、Route 3 の LLM TOC 抽出が17章を検出することを確認した（実測ログは `.superpowers/sdd/task-5-report.md`）。
- **教訓**: 固定ウィンドウでのサンプリングは、テスト対象のコーパスに偶然合致していただけの脆い前提になりやすい。書籍ごとにレイアウトが大きく異なりうる箇所（目次の位置等）は、固定値ではなく実データを探索するロジックに置き換えるべきである。単純にウィンドウを拡げるだけでも解決するが、LLM への入力量が全書籍で増えコストが上がるため、目次ページを特定してから周辺だけを渡す方式を採った。

### I-26. Naven のリガチャ崩れタイトルがコンテンツスキャンの一致判定を通らず、2章の境界が誤る（未対応・記録のみ）

- **事象**: I-25 修正後、`Naven.pdf` は17章に分割されるようになったが、そのうち **2章（Chapter VII, Chapter XII）の境界が誤っている**。Chapter VII は本来の扉頁より1頁後ろから始まり、扉頁（`CHAPTER / VII / The Sociology of N aven`）が前章（Chapter VI）のファイルに混入する。Chapter XII はより深刻で、扉頁（`CHAPTER / XII / The Pref erred Types`）を含む**7頁分**が前章（Chapter XI）のファイルに丸ごと混入し、`14_Chap_XII_THE_PREFERRED_TYPES.pdf` は扉と序盤の議論を欠いたわずか4頁のファイルになる。
- **調査**: 両章の扉頁のテキスト抽出結果を確認したところ、リガチャ／カーニングの影響で見出し文字列に不自然な空白が入っていた（`"N aven"`、`"Pref erred"`）。LLM が TOC から読み取ったクリーンなタイトル文字列（例: "The Preferred Types"）とは一致せず、`_classify_match()` の照合が失敗する。その結果、コンテンツスキャンは崩れのない章扉頁を飛ばし、たまたま空白崩れのない後続ページ（本文中で綺麗に組版された同一フレーズの再出現箇所）を一致とみなしてそこを章の開始位置とする。I-22 で追加した局所オフセット救済（`_rescue_by_local_offset`）はこの2章に対して発火していない（ログに該当の「局所オフセット補正」行が無い）——救済はランニングヘッダーへの誤着地を前提としており、本件は「一致そのものの失敗」であるため対象外。
- **原因**: 現行の照合（`_normalize_title()` / `_classify_match()`）は空白の有無を区別してタイトル文字列を比較しており、リガチャ由来の余分な空白挿入に耐性がない。
- **対応方針**: 記録のみで対応せず。Task 5（実PDF検証）で新規発見された欠陥であり、本ブランチのスコープ（I-22/I-24/I-25）とは異なる種類の不具合のため、修正は別課題として切り出す。
- **再現手順**: `data/input/Booksample/Naven/Naven.pdf` を `PDFSplitter.split()` に渡し、`09_Chap_VII_THE_SOCIOLOGY_OF_NAVEN.pdf` / `14_Chap_XII_THE_PREFERRED_TYPES.pdf` の先頭頁を確認する（実測ログ・目視確認の詳細は `.superpowers/sdd/task-5-report.md`）。
- **教訓**: OCR/テキスト抽出由来の表記ゆれ（リガチャ空白挿入）はタイトルの完全一致・前方一致では吸収できない。将来の修正候補として、空白を正規化してから比較する（例: 全空白除去後の部分一致）等の緩和策が考えられる。

> **[2026-07-20 解決]** ブランチ `feature/chapter-boundary-adjudication` で解決。当初「空白正規化」を緩和候補として挙げていたが、採用しなかった——リガチャ崩れは書籍ごとに現れ方が異なり（`N aven` / `Pref erred` / `rn→m` 等）、正規化ルールを1つ足すたびに次の崩れ方で外す「ヒューリスティックの増殖」になるため。代わりに**照合の成否に依存しない位置検証層**を追加した: 層2（`boundary_adjudicator.py::flag_suspects`）が「前後の確定章の双方とオフセットが食い違う章」を要審査として検出し（VII は +31、XII は +37、他章は +30——閾値ではなく前後との整合で判定するため relations の正当な段差=2 と誤り=1 を取り違えない）、層3（同 `BoundaryAdjudicator`）が要審査章の物理区間を LLM に見せて扉頁を裁定する。LLM はリガチャ崩れを意味で解釈できるため、テキスト完全一致では超えられなかった `N aven` / `Pref erred` の扉頁を同定できた。実PDF検証で VII=P116・XII=P190（正解と一致）を確認。詳細は `docs/superpowers/specs/2026-07-19-chapter-boundary-adjudication-design.md`、`scripts/verify_chapter_boundaries.py` の `NAVEN_GROUND_TRUTH`（floor=2 の hard regression）。

### I-27. PSE のランニングヘッダーが書名でありコンテンツスキャンがほぼ機能せず、境界が信頼できない（範囲外フォールバックによる複製は C2 で対応済み・境界不信頼性そのものは未対応）

- **事象**: I-24 の outline 棄却は正しく機能し `PSEpdf.pdf` は Route 3（LLM TOC 抽出 + コンテンツスキャン）にフォールバックする。章数も13章になり175章への破綻は回避されるが、**13章中12章でコンテンツスキャンの本文一致が失敗**し、LLM が返した論理ページ番号をそのまま物理ページとして採用する無補正のフォールバックに落ちている。
- **調査**: 目視確認の結果、この本の実際のランニングヘッダーは章タイトルではなく、ほぼ全頁に印字される**書名 "Property, Substance and Effect"** であることが判明した。コンテンツスキャンは章タイトル文字列との一致を探すため、書名だけが印字されたページでは一致対象が存在せず、必然的に全滅する。加えて、PSE の TOC 論理頁は最大333まであるが PDF 自体は175頁しかなく、フォールバックとして採用される論理ページ値そのものが範囲外になりうる（例: `Index` の論理ページ333）。
- **実際の下流被害（C2 修正前の実測）**: この範囲外フォールバックは単なる「不正確な値」では済まなかった。`_apply_content_scan()` の無補正フォールバックが文書の総頁数（175）を超えた値（Chapter 9=論理179, Chapter 10=論理204, Chapter 11=論理229, Writing societies=論理233 等）をそのまま `start_page` として返し、`split()` がこれを検査なしで `insert_pdf(from_page=…, to_page=…)` に渡していた。PyMuPDF は範囲外の `from_page` を例外にせず黙って末尾頁にクランプするため、`10_Chapter_9_…pdf` / `11_Chapter_10_…pdf` / `13_Writing_societies_…pdf` の3ファイルが**すべて1頁のみ・内容も書籍末尾の索引頁とバイト単位で同一**になり、`09_Chapter_8_…pdf` も本来より短く打ち切られていた（3つの「章」ファイルが実際には同一の索引頁を複製していただけで、章としての中身を一切持たない状態だった）。
- **原因**: コンテンツスキャンの照合ロジックは「ランニングヘッダーに章タイトルが印字される」という前提に立っているが、PSE はこの前提を満たさない書籍である。加えて、本文一致が全滅した際のフォールバック値（論理ページそのまま採用）に文書範囲の上限チェックが無かった。
- **対応方針（2段階）**:
  - **C2（本ブランチで対応済み）**: `_apply_content_scan()` の無補正フォールバックを `total_pages - 1` にクランプし、クランプ後に前章と衝突して配置不能な場合は章を欠落させてでもスキップする（無関係な頁を複製するより安全）。`split()` 側にも `start_page >= len(doc)` の防御的ガードを追加し、他の経路（outline・ローカルTOC）から範囲外の `start_page` が来た場合も `insert_pdf()` を呼ぶ前に確実に弾く。この修正により、上記の**索引頁複製・ファイル欠損は解消**した（修正後は13章→10章になり、Chapter 10/11/Writing societies は「配置可能な物理ページが残っていない」として正しくスキップされる。全章の頁範囲が文書内に収まり、重複する頁範囲も無いことを `scripts/verify_chapter_boundaries.py` で確認済み）。
  - **境界の不信頼性そのもの（未対応・記録のみ）**: C2 はフォールバック値の「破綻」を防いだだけであり、根本原因である「ランニングヘッダーが章タイトルではなく書名である」という PSE 固有の前提崩れは解消していない。コンテンツスキャンによる章扉頁の同定は依然としてほぼ機能せず、フォールバック採用された章の境界（開始ページ）自体の正確性は保証されない（例: 修正後の Chapter 9 は文書末尾の索引頁に着地しており、これは「範囲外を防いだ」結果であって「正しい章扉頁を発見した」わけではない）。この不正確さの是正は本ブランチのスコープ外であり、引き続き I-27 として未対応のまま残す。
- **再現手順（C2 修正前の状態を再現する場合）**: `data/input/Booksample/PSE/PSEpdf.pdf` を C2 修正前の `PDFSplitter.split()` に渡し、`10_Chapter_9_…pdf` / `11_Chapter_10_…pdf` / `13_Writing_societies_…pdf` のファイルサイズ・先頭頁テキストが同一になることを確認する（実測ログは `.superpowers/sdd/task-5-report.md`、C2 検証は `.superpowers/sdd/task-7-report.md`）。
- **教訓**: 章分割精度の前提（ランニングヘッダーに章タイトルが載る）が成り立たない書籍が存在する。コンテンツスキャンが「全滅」した場合のフォールバック値は、文書範囲の妥当性チェックなしに `insert_pdf()` まで到達させてはならない——「例外を投げない範囲外アクセス」（PyMuPDF の `insert_pdf` の黙示的クランプ）は、テストで明示的に境界値を突かない限り気づけない類のバグである。

> **[2026-07-20 原因の全面訂正と解決]** ブランチ `feature/chapter-boundary-adjudication` で、**当初の原因診断が2箇所で誤っていたこと**が判明し、真因を解消した。
> **(1) 検証が実パイプラインと異なる文書に対して行われていた（→ I-28 として独立記録）。** `scripts/verify_chapter_boundaries.py` は元PDF（175頁）を直接 `PDFSplitter.split()` に渡していたが、実パイプライン（`core/book_manager.py:182-185`）は見開きスキャンを `split_spread_pdf()` で単ページ分割してから渡す。PSE は `is_spread=True` のため、本番が一度も見ない175頁の文書で検証されていた（実際は分割後350頁）。上記の主症状「範囲外フォールバックによる索引頁複製・論理頁333>175」は、この175頁文書のアーティファクトであり本番では発生しない（分割後350頁では論理頁は範囲内に収まる）。C2 のクランプ自体は防御として有用なので残すが、その正当化の根拠だった実害はハーネスの副産物だった。
> **(2) 「ランニングヘッダーが書名」は原因ではなかった。** 分割後の PSE を目視すると、奇数頁（recto）のランニングヘッダーは章タイトルを載せている（例 P150: `Divisions of Interest` / `137`）。書名 `Property, Substance and Effect` が出るのは偶数頁（verso）のみで、これは組版の標準的な交互配置にすぎない。**真因は上流の TOC 抽出（Route 3 `_extract_toc`）が、目次頁のテキスト層が列単位で出力される書籍でエントリと頁番号を1つずらして対応付けていたこと**（Ch1 に Ch2 の頁番号が入り全章がずれる。Preface の頁番号 `ix` が `lX` と誤読されタイトル列に紛れ、数値列が1つ足りなくなるため）。当初「13章中12章で照合失敗」と観測したのは、探索窓 `logical-5 … logical+49` の左端が誤った論理頁の近傍で偶然ヘッダーに当たっていた人工物だった。
> **解決**: 層1（`page_number_map.py` + `toc_verifier.py`）が、印刷頁番号から推定したオフセットで TOC を検算し、系統的な1つずれ（shift=-1）を検出・補正する。実測で PSE のみ shift=-1（一致12対2）、他3冊は shift=0（誤検出ゼロ）。残る個別のずれは層2/層3が処理する。実PDF検証で PSE の章本文境界は 12/13 が正解と一致（`scripts/verify_chapter_boundaries.py` の `PSE_GROUND_TRUTH`、floor=12 の hard regression）。**唯一の未達は Preface のみ**（前付けの端症例。正解 P8 に対し P11 のランニングヘッダー継続頁に着地。章本文の境界ではないため I-27-residual として残す）。詳細は `docs/superpowers/specs/2026-07-19-chapter-boundary-adjudication-design.md`。

### I-28. 検証スクリプトが実パイプラインと異なる文書を検証していた（修正済み）

- **事象**: `scripts/verify_chapter_boundaries.py` が元PDFを直接 `PDFSplitter.split()` に渡しており、実パイプライン（`core/book_manager.py:182-185`）が前段で行う見開き分割を通していなかった。`is_spread_pdf()` の実測は corfra=True / PSE=True / Naven=False / relations=False。ただし実害を受けたのは **PSE のみ**である。`BOOKS` 辞書で corfra だけは分割済みファイル（`corfrapdf_split.pdf`）が直接ハードコードされており、分割判定を迂回してはいたが結果として正しい文書を見ていた。**PSE だけが元PDF（175頁）で検証され、本番が見る350頁の文書を一度も検証していなかった。**
- **影響**: (1) I-27 の原因診断が誤った（詳細は I-27 の訂正ブロック）。(2) マージ済み I-22/I-24/I-25 の「実PDF 4冊で退行0件」のうち PSE の分は無意味な検証だった（再測の結果、corfra/Naven/relations は正しい文書上でも退行なしのため結論自体は維持される）。
- **対策**: `resolve_input_pdf()` を追加し、`book_manager` と同じく `is_spread_pdf()`→`split_spread_pdf()` を通してから検証する。`BOOKS["corfra"]` も元PDFを指すよう戻し、分割経路を実際に通す。期待値定数も分割後の文書で取り直した。
- **教訓**: 検証スクリプトは「本番と同じ入力を作る」ところまで含めて本番と一致させなければならない。前処理の1段の欠落が、その上に積んだ全ての診断を無効にする。前ブランチの教訓「検証スクリプトの assert 範囲を疑う」の、より上流での再発である。

### I-29. PSE Chapter 5 の境界が22頁ずれていた（層1で解消）／PSE 指標が gate していなかった（Task 9 で修正）

- **事象1（境界）**: `Chapter 5 New Economic Forms: a Report` の真の扉頁は物理P102 だが、修正前の出力は P124 だった（22頁のずれ）。I-27 の調査中に正解データを目視で確定させる過程で発見した未記録の欠陥。原因は I-27 と同一（TOC のエントリと頁番号の1つずれで Ch5 が Ch6 の頁番号117を受け取っていた）。層1（TOC 検算）で解消。
- **事象2（指標）**: 検証スクリプトの `report_ground_truth` が (a) タイトルの完全一致で照合し、(b) 一致数を `rec.metric()` で記録するだけで regression を発火させなかった。このため、層2/3配線後に TOC が再抽出されてタイトル書式が変わる（`Chapter 1` → `Chapter 1:` → `Chapter 11` → `Concluded` と実行ごとに揺れる。`state/` は gitignore のため新規 checkout では必ず再抽出される）と、真値12/13が表示1/13に化け、しかもこの激減が「regression 0」を通り抜けた。
- **対策**: `report_ground_truth` を**物理頁番号ベースの照合**に直した（「正解の扉頁に章境界が実際に立っているか」を数える。書式非依存で境界精度そのものを測る）。加えて下限（PSE≥12・Naven==2）を `rec.regression()` で hard gate 化。mutation テスト（正解境界を1頁ずらす→一致数が下限を割り regression が立つ）で赤転を確認した。
- **教訓**: (1) LLM 抽出物（TOC タイトル）に完全一致で依存する検証は、抽出の非決定性で容易に化ける。安定した観測量（ここでは物理頁境界の有無）で測るべき。(2) 「指標として記録するだけ」の検証は gate にならない——下限を割ったら exit code を非0にして初めて退行を捕まえられる。mutation テストで「実際に赤に転じるか」を確かめない指標は、緑であること自体が無意味。

## 2026-07-20: VLM 単ページ OCR 実装（I-21 解決）の実PDF検証中に発見・即修正した不具合

### I-30. 印刷テキストが皆無のページで `[VLM抽出失敗]` という文字通りのプレースホルダが本文として出力に混入する

- **事象**: I-21 修正の実PDF検証（corfra 前付け「Acknowledgments」章）で、本来空白のはずのページ（物理P4、ネイティブテキスト0字・画像1枚のみの真の空白頁）が、最終出力 `_p2.md` に `[VLM抽出失敗]` という日本語の文字列そのままで出現した（英語ブロック・日本語ブロックにそれぞれ1回、計2箇所）。バグ報告ではなく、実際に本文として翻訳・出力パイプラインを通過していた。
- **根本原因**: 2つの独立した既存ロジックの衝突。(1) I-21 修正の `VLM_SINGLE_PAGE_PROMPT` は図版ページ等で「本文が無ければ空を返す」よう指示していた。(2) `core/llm_client.py::call_gemini_async` は I-19 対策として「空応答は常に異常」とみなし最大5回リトライ後に `RuntimeError` を送出する仕様になっている。(1)の設計どおり VLM が空文字を返そうとするたびに(2)がそれを異常とみなしてリトライを浪費し、最終的に例外を送出。加えて `ocr_manager.py::_call_gemini_raw` がその例外を握り潰して空文字列 `""` を返していたため、`pdf_ingester.py::_vlm_slice_job` は「正当な空文字列（本文なし）」と「本物の VLM 失敗」を区別できず、両方を `if not vlm_res: raise ValueError(...)` で同じフォールバック経路に流していた。フォールバック先のネイティブテキストも真の空白頁では空のため、最終手段の `"[VLM抽出失敗]"` プレースホルダ文字列が実体のあるチャンクテキストとして採用されていた。
- **なぜ今まで気づかれなかったか**: I-21 修正前（2-up結合時代）はこの種の空白頁が前ページ内容を誤って書き起こしていた（I-21 の重複バグそのもの）ため、「空白と正しく判定される」場面自体が発生していなかった。I-21 を直して初めて顕在化した。
- **対策（構造的解消）**: 「空文字列」と「本物の失敗」を呼び出し元が区別できるようにした。
  1. `OCRManager.NO_TEXT_MARKER`（`"[NO_PRINTED_TEXT]"`）を新設し、プロンプトには空文字列ではなくこのマーカー文字列を返すよう指示（`call_gemini_async` の空応答ガードを構造的に回避）。
  2. `process_page_vlm` はマーカーを受け取ったら呼び出し元に空文字列として返す（キャッシュヒット経路も同じ変換を通す）。
  3. `_call_gemini_raw` は例外を握り潰さず伝播させる（空文字列＝正当、例外＝本物の失敗、という区別を可能にする）。
  4. `pdf_ingester.py::_vlm_slice_job` の `if not vlm_res: raise ValueError(...)` を削除（空文字列はもはや失敗ではない）。
- **検証**: `tests/unit/test_ocr_manager.py::TestNoTextMarker` を新設（マーカー→空文字列変換・キャッシュヒット時も同様・本物の例外は伝播）。単体テスト 391件全合格（既存388＋新規3）。
- **教訓**: 「意図的な空応答」を設計に組み込む際は、それを処理するすべての層（LLM 呼び出しラッパーの空応答ガード、例外の握り潰し、呼び出し元のフォールバック判定）が同じ「空＝異常」前提で書かれていないか確認する必要がある。今回は3層すべてが独立に「空＝失敗」を仮定しており、そのどれか1つでも「空＝正当な結果」を許容していれば連鎖しなかった。

## 2026-07-21: `docs/superpowers/plans/2026-07-10-book-vlm-routing.md` 以降83コミット分のサブエージェント並列コードレビューで発見・修正した不具合（I-31〜I-37）

4系統（Phase1取り込み/VLM/章境界判定、書籍統合/Phase3構造化、Phase4翻訳、LLMクライアント/設定/Phase2）に分けて並列レビューし、発見した指摘のうち実コードを読んで再確認できたものを修正した。

### I-31. 章境界LLM裁定（層3）の探索窓が、同一確定章ペアに挟まれた複数の要審査章に対して常に同一になる（対応済み）

- **事象**: `BoundaryAdjudicator._interval()` が要審査章自身の index を見ず、前後の**確定**章のみから探索窓 `[lower, upper]` を決めていた。区間が `ADJUDICATION_MAX_PAGES`(32) を超える場合、常に区間の先頭から32頁を返すため、同じ確定章ペアに挟まれた複数の要審査章（fallback クラスタ）が全員まったく同じ窓を割り当てられていた。
- **実害**: クラスタ後方の章は真の扉頁が窓外になり LLM が正しく裁定できず、区間外判定で棄却されて無補正のまま残る。まさにこの機能が解決対象とする「PSE の12連続fallback」型のケースで効果を失う設計上の穴だった。
- **対策**: `core/engine/p1_ingest/boundary_adjudicator.py::_interval()` を、区間が窓幅を超える場合は対象章の論理頁が prev/nxt の論理頁の間でどの比率に位置するかから窓の中心を推定し、章ごとに異なる窓（幅は変わらず32頁のまま）を割り当てるよう変更。区間が窓幅以下ならこれまで通り区間全体をそのまま返す。
- **検証**: `tests/unit/test_boundary_adjudicator.py::TestInterval` を新設（クラスタ内の2章が異なる窓・区間内に収まる窓を得ることを確認、既存の「区間が窓幅以下ならそのまま返す」ケースの回帰なしも確認）。

### I-32. Phase2レジュメ生成のサンプリング閾値拡大が、resumeモデルの実効入力上限超過（I-20相当）を再導入しうる（対応済み）

- **事象**: 論文モードの `MAX_INPUT_CHARS` を500,000字→1,500,000字に拡大した際（`requirements_log.md` 2026-07-21 参照）、根拠にした「入力上限1,048,576 tok」は公称値であり、`docs/model_optimization.md` 自身が記載する実効上限（I-20実測: 約735,000字前後、文書によって文字/トークン比が3.9〜4.5程度ブレる）とは別物だった。`core/book_manager.py` の書籍全体レジュメには `RESUME_MODEL_SAFE_CHAR_LIMIT` による同種のガードが既にあったが、`core/phase2_meta.py::generate_resume()`（論文・章単位のレジュメ）には対応するガードがなかった。
- **対策**: `core/phase2_meta.py` に `RESUME_MODEL_SAFE_CHAR_LIMIT = 600_000`（book_manager.py と同じ値・同じ考え方）を追加。`model` 未指定かつ入力が閾値超なら resume モデルではなく既定モデル（Lite）にフォールバックする。`--model` 明示指定時はガードを適用しない（book_manager.py と同じ設計、ユーザー選択を尊重）。
- **既知のトレードオフ**: この閾値は「文字数」であり実際の制約は「トークン数」（文書によって文字/トークン比が変動する）ため、600,000字〜735,000字程度の文書は実際には安全でもLite側にフォールバックする場合がある（精度よりクラッシュ回避を優先する保守的な設計。2026-07-21 に Naven.pdf, 745,144字/165,673tok で実際に成功した実測があり、このケースは今回のガードでは Lite にフォールバックされる）。
- **検証**: `tests/unit/test_phase2_meta.py` に3件追加（閾値超でLiteへフォールバック／閾値内はresumeモデル維持／`--model`明示時はガード無効）。

### I-33. 複数APIキー運用でキーローテーションが有料キーへ切り替わった後もTierManagerがFREEに張り付く（対応済み）

- **事象**: `core/llm_client.py::_calc_retry_wait()` は429/503検知時に無条件で `tier_manager.downgrade()` を呼ぶ。直後にキーローテーションで有料キーへ切り替わって成功しても、TierManagerの状態を戻す処理がなく、以降のリクエストが不必要にLiteモデル・縮小バッチで処理され続けていた。
- **対策**: `KeyRotator.configure()` にキーごとの種別（`"free"`/`"paid"`）を渡せるようにし（`main.py` から `["free","free","paid"]` を渡す）、ローテーション成功直後に新設の `_maybe_restore_tier_after_rotation()` を呼んで、切替先が有料キーの場合のみ TierManager を PAID に戻す。無料キー同士のローテーション（free1→free2）ではFREEのまま据え置く。
- **検証**: `tests/unit/test_llm_client.py` に2件追加（有料キーへの切替でPAID復元／無料キー間の切替ではFREE維持）。

### I-34. `core/book_manager.py` の resume モデル安全上限フォールバックが `--model` 明示指定時にバイパスされる（レビューの結果、対応不要と判断）

- **確認内容**: レビューで「ユーザーが `--model` を明示指定すると `RESUME_MODEL_SAFE_CHAR_LIMIT` ガードが完全にバイパスされ、I-20 相当のクラッシュが再発しうる」と指摘された。しかし I-20 の対策記録自体に「`--model` 明示指定時はユーザーの選択を尊重しガードを適用しない」と明記されており、これは見落としではなく意図した設計だった。I-32 の phase2_meta.py 側の新規ガードも同じ方針に揃えている。
- **結論**: コード変更なし。今後同種の指摘が出た場合はこのエントリと I-20 を参照。

### I-35. Phase4 `_create_batches()` が未使用の死コードで、対応するテストもその死コードしか検証していない（対応済み）

- **事象**: 実運用のバッチ分割ロジックは `ParallelTranslator.translate_section_chunks()` 内にインライン実装（ティアの動的変更に追従するため）されており、別メソッド `_create_batches()` は本番コードから一切呼ばれていなかった。`tests/unit/test_parallel_translator.py::test_parallel_translator_batching` はこの未使用メソッドのみを検証しており、実運用のバッチ境界ロジックにリグレッションが入っても検知できない状態だった。
- **対策**: `_create_batches()` を削除。該当テストを `translate_section_chunks()` 経由で実際に渡されるバッチを検証する形に書き換え。
- **検証**: 書き換え後のテストが同じ境界条件（600字チャンク×4、max_batch_chars=1500）で従来と同じ2バッチ分割を検証することを確認。

### I-36. Phase4 `TreeReconstructor.rebuild()` の `section_resumes` 引数が到達不能な死コードになっている（対応済み）

- **事象**: 章レジュメ生成の廃止（Phase2レジュメを両モードで翻訳コンテキストに配線する方式への変更）後、`rebuild()` の呼び出し元（`core/phase4_translate.py`）が `section_resumes` を渡さなくなり、常に空辞書がデフォルト値として使われ続けていた。`ja_node.metadata["summary"]` への書き込み分岐が事実上到達不能になっていた。
- **対策**: `section_resumes` 引数と対応する分岐を削除。呼び出し元・テストとも参照箇所がないことを確認済み。

### I-37. 論文（非書籍）PDFの入力ルーティングが見開きスキャン判定（優先順位②）を一度も行っていない（対応済み・部分対応）

- **事象**: `core/book_manager.py` は書籍単位で `is_spread_pdf()` を判定し、見開きスキャンなら単一ページへ分割した上で VLM ルートへ強制していた。一方 `core/phase1_preprocessor.py::_run_phase1_pdf` は `pdf_mode` 明示指定と `is_docling_viable()` しか見ておらず、論文（非書籍）PDF では見開きスキャン判定（CLAUDE.md 記載の優先順位②）が一度も行われていなかった。
- **対策**: `core/pipeline.py` の PDF プリフライトチェックに `is_spread_pdf()` を追加し、非書籍PDFで見開きスキャンを検出したら `pdf_mode="full_vlm"` を強制する（`diagnose_pdf_quality` と同じ場所・同じパターン）。書籍モードの章単位呼び出し（`is_book=True`）は BookManager が既に分割済みの入力を渡すため判定をスキップする。
- **既知の残課題**: 書籍モードと異なり、論文モードには見開き画像を単一ページへ**分割する**処理自体が存在しない。今回の修正はモデルルーティング（VLM強制）のみで、見開きのままの1画像がVLMに渡る点は未解消。実際に論文が見開きスキャンされるケースは稀と見られ、優先度は低いと判断し今回は対応範囲外とした。
- **検証**: `tests/unit/test_pipeline.py` に2件追加（見開きスキャン論文PDFでfull_vlm強制／書籍モード章では判定自体をスキップ）。

### I-38. 論文（非書籍）モードで Docling 不可PDFがVLMへフォールバックせず、生の物理テキスト抽出のまま複雑な図版ページが破綻する（対応済み）

- **事象**: 2026-07-21、`Naven.pdf`（Bateson の民族誌書籍。741頁）を `--book` を付けずに論文モードで処理したところ、出力（`Naven_p2.md`/`.txt`）の Figure 4「Diagram of Initiatory Groups」ページ（物理P274）が `- 2` `- 45` `- Ax` `- 1--` `- By` のような、図中のラベルが1つずつ独立した箇条書きに寸断された状態で出力された。ログは「VLM ルートで処理します」と表示していたが `phase1_route.json` の実ルートは `"native_fallback"`（VLM 不使用の生の物理テキスト抽出）で、処理時間も741頁で3秒とVLM呼び出しなしの数値だった。
- **根本原因**: `main.py`（論文モードループ, 旧 :232）は `pdf_mode` 未指定時に常に `pdf_mode="hybrid"` を固定していた（`server.py` の非書籍分岐も同様に `"hybrid"` 固定）。`core/phase1_preprocessor.py::_run_phase1_pdf` は `is_docling_viable()` が False（Naven.pdf は該当）の場合に VLM ルートへ「フォールバックする」体裁のログを出すが、実際に呼ぶ `run_pdf_ingestion_async`（`core/engine/p1_ingest/pdf_ingester.py:63`）は **`pdf_mode == "full_vlm"` のときしか VLM スライディングOCRを実行しない**。`"hybrid"` のまま渡されるとその分岐に入らず、無条件で `PhysicalIngester.extract_spans()`（生の物理テキスト抽出）に落ちる。書籍モードは `core/book_manager.py::_decide_book_pdf_mode`（I-16, 規則④「Docling不可→VLM」）で `is_docling_viable()==False` のとき明示的に `pdf_mode="full_vlm"` を渡していたためこの穴を踏まないが、論文モードには対応するルーティング決定が存在しなかった（I-37 は規則②のみ対応、規則③④は未対応のまま残っていた）。
- **なぜ図版ページで顕在化したか**: 通常の本文段落ページでは物理テキスト抽出でも読み順が概ね保たれるため気づかれにくいが、Figure 4 のような2次元配置の図版（ラベルがPDF内部の座標順で並び、視覚的な読み順と一致しない）では、各ラベルが独立した極小スパンとしてバラバラに抽出され、そのまま1チャンク＝1段落として翻訳・出力パイプラインを通過した。VLM（画像として1頁を渡す方式）であればこの種の破綻は起きない設計。
- **対策**: `core/book_manager.py::_decide_book_pdf_mode` の実体（①明示指定②見開き=VLM③Docling可能=hybrid④それ以外=VLM）を `core/engine/p1_ingest/routing.py::decide_pdf_mode` として切り出し、`book_manager.py` は後方互換のためこれをエイリアスとして re-export する形に変更。`main.py`（論文モードループ）と `server.py`（非書籍分岐）はいずれも、PDF入力かつ `pdf_mode` 未指定時に `is_spread_pdf()` / `is_docling_viable()` を実行し `decide_pdf_mode()` で解決した具体値を `run_pipeline()` に渡すよう修正（book_manager.py が書籍単位で行っているのと同じパターンを論文単位で適用）。`core/pipeline.py` の I-37 プリフライトチェック（見開き検知・`diagnose_pdf_quality`）はそのまま維持（役割が異なる独立した安全網のため）。
- **既知の限界**: `main.py`/`server.py` と `core/pipeline.py` の両方で `is_spread_pdf()`/PDFの品質判定が二重に走る（後者は I-37 由来の独立した安全網であり非同期処理を壊さないため統合していない）。パフォーマンス上は軽微（各判定はサンプル数頁のみを読む）。
- **検証**: `tests/unit/test_routing.py` を新設（規則①〜④の4ケース、`test_book_manager.py::TestDecideBookPdfMode` と同内容だが book_manager 経由でなく共有モジュール単体で検証）。単体テスト405件全合格。実PDF再検証（Naven.pdf の `--pdf-mode full_vlm` 再実行によるFigure 4ページの実際の改善確認）は別途ユーザー側で実施予定。

`core/engine/p3_structure/state_integrator.py` の型注釈 `List[Tuple[str, Path]]` で `Tuple` が未importだった件（実行時エラーにはならないが静的解析では検出される）も同時に修正（`from typing import List, Optional, Dict, Tuple`）。

### I-39. genai.Client キャッシュが同一パイプライン内の Phase 1/Phase 4 で別イベントループを跨いで再利用され、章ごとに毎回 "Event loop is closed" が発生する（対応済み）

- **事象**: 2026-07-21、`Naven.pdf` を `--book`（full_vlm）で処理中、17章それぞれで Phase 4 翻訳の最初のリクエストが必ず `RuntimeError: Event loop is closed` で1回失敗し、即座にリトライして成功する、という同一パターンが章ごとに再現した（ユーザー報告）。あわせて章間で `Task exception was never retrieved`（`AsyncClient.aclose()` の `RuntimeError: Event loop is closed`）というバックグラウンドタスクの例外ログも毎回出力されていた。
- **根本原因**: `reset_pipeline_state()`（`core/llm_client.py`）は「genai.Client の非同期トランスポートは生成時のイベントループに紐付く」ことへの既存対策として、`run_pipeline()` 開始時にクライアントキャッシュ（`_clients_local.clients`）を丸ごとクリアしていた（過去の対応、`test_reset_pipeline_state_clears_client_cache` でカバー済み）。しかしこの対策は**パイプライン呼び出し単位**（＝章単位）でしかクリアしておらず、1回の `run_pipeline()` 呼び出し内でも Phase 1（VLM ingestion, `run_pdf_ingestion` 経由）と Phase 4（翻訳）はそれぞれ独立して `run_async()`→`asyncio.run()` を呼ぶため、**別々のイベントループ**を持つ。Phase 1 で生成・キャッシュされた `genai.Client` が Phase 4 の新しいループでもそのまま再利用され、その非同期トランスポートは Phase 1 のループ（`asyncio.run()` 終了時に閉じている）に紐付いたままのため、Phase 4 の最初の呼び出しが必ず失敗していた。既存のリトライ機構（`call_gemini_async` の `except` 節）がたまたま2回目の試行で成功していたため実害はなかったが、章ごとに確実に再現する無駄なリトライと紛らわしいトレースバックが出続けていた。
- **対策**: `_get_clients_dict()` の値を `genai.Client` 単体から `(client, 生成時のイベントループ)` のペアに変更。`_get_client()` は呼び出し時点の `asyncio.get_running_loop()`（ループなしのコンテキストなら `None`）とキャッシュ済みループを比較し、一致しない場合のみクライアントを再生成する。同期呼び出し（ループなし）は従来通りループ不問でキャッシュを再利用するため、`call_gemini()`（同期版）の挙動は変えていない。`reset_pipeline_state()` 側の明示クリアは引き続き残す（パイプライン開始時に即座にキャッシュを空にする防御自体は無害かつ有効なため）。
- **検証**: `tests/unit/test_llm_client.py` に2件追加（同一ループ内では同一インスタンスを再利用／別ループでは再生成される）。単体テスト407件全合格。実運用での再現確認（章をまたいで "Event loop is closed" が出なくなること）は現在進行中の `Naven.pdf` 書籍処理の後続章で確認予定。

### I-40. `BookManager.session_dir` が相対パスのため、カレントディレクトリ次第でプロジェクト外に書籍セッションが作られる（対応済み）

- **事象**: 2026-07-24、ホームディレクトリ（`~`）から `p2wbv` を実行したところ、書籍セッションが `/Users/shufujita/state/book_sessions/` に作成された。`core/config.py` の `STATE_DIR`（`PROJECT_ROOT / "state"`、`PROJECT_ROOT` はプロジェクトルート固定）とは別の場所であり、ユーザーが「仕様通りか」と疑問視した。
- **根本原因**: `core/book_manager.py:42`（`BookManager.__init__`）が `Path("state/book_sessions") / ...` という相対パスを直接組み立てていた。同一ファイル内の `_cleanup_old_book_sessions()` は正しく `STATE_DIR / "book_sessions"`（`config.py` からimport、プロジェクトルート基準の絶対パス）を使っており、同じファイル内で基準が二重化していた。相対パスは `p2wbv` 実行時のカレントディレクトリに依存するため、プロジェクトディレクトリ以外から実行するとプロジェクト外（このケースではホームディレクトリ）にセッションが作られる。
- **実害**: 置き場所が分かりにくくなるだけでなく、`_cleanup_old_book_sessions()`（`MAX_BOOK_SESSIONS` を超えた古い書籍セッションの自動削除）は `STATE_DIR` 配下しか見ないため、カレントディレクトリ違いで作られたセッションは自動クリーンアップの対象から漏れ、無期限に残り続ける。また、同じ本を異なるカレントディレクトリから複数回起動すると、書籍セッションが別々の場所に分裂し、「キャッシュが効かず毎回最初からやり直しになる」といった別の不可解な症状にもつながりうる。
- **対策**: `core/book_manager.py` で `STATE_DIR` を `config` から import し、`self.session_dir = STATE_DIR / "book_sessions" / f"{self.book_title}_{self.fingerprint}"` に変更（`_cleanup_old_book_sessions()` と同じ基準に統一）。
- **既知の残課題**: `scripts/benchmark_concurrent.py` に同種の相対パス `Path("state/...")` が3箇所残っている。ベンチマーク用の開発スクリプトで実害の報告はないため今回は対象外とした。
- **検証**: `python3 -c "from core.book_manager import BookManager"` でimportエラーがないことを確認。既存のセッション作成ロジック自体は変更していないため回帰リスクは低いが、単体テストでの明示的な検証は未実施(要フォローアップ)。
