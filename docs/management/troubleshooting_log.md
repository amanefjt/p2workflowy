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

### 5. 特定セクションでの異常な実行遅延 (API Queuing 240s TTFT)
- **事象**: Phase 4 の翻訳において、特定のセクションのみ TTFT（最初の1文字が出るまでの時間）が 230〜255秒（約4分）に跳ね上がり、プロセスがフリーズしたように見える。
- **原因**: 8万文字の大規模コンテキストを持つリクエストを、`ParallelTranslator` が同時に 3つ（`semaphore_size=15`）API に送信した結果、Google API 側でシリアライズ（処理の順番待ち）が発生。先に送信したリクエストが処理されるまで、後続のリクエストがサーバーのキューに長期間留まる「Silent Stall」状態に陥っていた。
- **解決策**: API 側のキューイングによるタイムアウトや理不尽な遅延を防ぐため、`parallel_translator.py` の `max_concurrent_sections` を 3 から 1 に削減。さらに `llm_client.py` の有償版のAPI並列セマフォを 15 から 2 に減らし、「少しずつ投げる」健康的なペーシング（Pacing）を確立した。総処理時間は並列時と変わらない。
