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

