# Renderにおけるメモリ制限超過（OOM）対策計画

## 現状分析
Renderの通知によると、`p2workflowy` インスタンスがメモリ制限（通常512MB）を超え、自動再起動が発生しました。
ログを確認したところ、巨大なPDFの処理や並列実行が重なった際にメモリ消費がスパイクした可能性が高いです。

### 疑わしい箇所
1. **アップロード処理**: `server.py` で `await file.read()` を使用しており、ファイル全体を一度メモリに載せている。
2. **PDF解析 (PyMuPDF)**: `pdf_ingester.py` で `get_text("dict")` が1ページにつき複数回呼ばれており、巨大なデータの重複保持が発生している。
3. **VLM並列実行**: `VLM_SEMAPHORE_LIMIT = 2` により、高解像度のピクセルデータが同時に複数メモリを専有している。

## 対策内容

### 1. サーバー側のメモリ効率化 (server.py)
- **ストリーミングアップロード**: `await file.read()` を廃止し、`shutil.copyfileobj` 等を用いてディスクに直接ストリーミング保存する。
- **インポートの最適化**: 関数内の `import` をトップレベルへ移動。

### 2. PDF解析の最適化 (core/pdf_ingester.py)
- **データの再利用**: `should_use_vlm` で取得した `text_dict` を `extract_text_fast` へ渡し、重複呼び出しを排除する。
- **並列度の調整**: `VLM_SEMAPHORE_LIMIT` を `2` から `1` へ変更し、メモリピークを抑制する（Renderのような小規模インスタンス向け）。
- **明示的なクリーンアップ**: ページ処理ごとに `gc.collect()` を行う。

### 3. ドキュメントへの記録
- `docs/management/troubleshooting_log.md` に今回の事象と対策内容を記録する。

## 実施スケジュール
1. `server.py` のアップロードロジック修正
2. `core/pdf_ingester.py` の解析ロジック最適化
3. 動作確認（ローカルでの物理メモリ使用量の抑制を確認）
4. ドキュメント更新
