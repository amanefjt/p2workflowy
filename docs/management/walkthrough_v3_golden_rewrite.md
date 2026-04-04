# Walkthrough: P2Workflowy V3 (Golden Rewrite) Pipeline

## 1. 概要
P2Workflowy V3 は、従来の脆弱な JSON パースから脱却し、XMLタグベースの RTT (Robust Translation Transcribe) v3.4 プロトコルを採用した次世代の翻訳パイプラインです。
本バージョンでは、API制限への適応能力（TierManager）と、大規模な書籍を構造的に処理する能力（BookManager）が大幅に強化されています。

## 2. 主要コンポーネント

### A. RTT v3.4 (XML Tag Protocol)
- `llm_client.py`: 翻訳チャンクを `<p2w_chunk_ID>...</p2w_chunk_ID>` でカプセル化し、正規表現で確実に抽出します。
- これにより、Gemini が出力を途中で中断した場合でも、正常に出力された箇所までを確実にパースして再開することが可能になりました。

### B. TierManager (Adaptive Batching)
- ティア（Paid/Free）に合わせて、バッチサイズ（チャンク数、文字数）を動的に調整します。
- `429 (Resource Exhausted)` エラーが発生した場合、バッチサイズを自動的に 50% 縮小する適応ロジックを搭載しています。

### C. Book Mode Orchestration
- `BookManager`: PDF 全体をスキャンし、目次ベースで章ごとに分割。
- 各章に対して独立したパイプライン（Phase 1-5）を実行し、最後に `StateIntegrator` が全成果物を一つの Workflowy 用ファイルに統合します。

## 3. 実行手順

### 論文モード（Paper Mode）
```bash
python main.py [PDF_PATH] --lite
```
- `--lite`: 無料版 Gemini の制限内で安全に実行します。
- `--free`: 同等。

### 書籍モード（Book Mode）
```bash
python main.py [PDF_PATH] --book --lite
```
- `--book`: 章ごとの分割と統合処理を有効にします。

### スモークテスト済み
- `chap3relations.pdf` を用いた書籍モード（--lite）の完走を確認済み。
- モデル選択、パス解決、各フェーズの連結が正常に機能しています。

## 4. トラブルシューティング
- **パス解決エラー**: `StateIntegrator` が章ごとの `_export/` ディレクトリを自動探索するように修正されています。
- **モデル選択**: `--lite` 指定時は `Phase 0` から `3.1-flash-lite` が選択されるよう遅延解決ロジックが実装されています。
