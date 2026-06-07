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

