# 実装詳細と検証手順: API ティア動的最適化

## 実装のポイント

### 1. ゼロコンフィグな判別 (Zero-config Identification)
ユーザーに「自分のキーは有料か？」を尋ねるのではなく、プログラムが最初のリクエスト（または 429 発生時）に API からのバックプレッシャーを感知して自律的に判断します。

### 2. 処理継続の保証 (Continuous Processing)
429 エラーが出た瞬間に例外で止めるのではなく、`TierManager` を通じてシステム全体の「ギア」を落とし、即座に次のバッチから安全なレートで再試行します。

## 検証手順

### CLI でのパフォーマンス検証
1. 有料版キーが設定されている環境で `python3 main.py data/sample/PDF/ALpdf.pdf` を実行。
2. ログに `[TIER] Initializing as PAID` と表示され、Semaphore 12 で高速に処理されることを確認。

### 無料版（ダウンシフト）の検証
1. `--free` フラグを付けて実行: `python3 main.py data/sample/PDF/ALpdf.pdf --free`
2. ログに `[TIER] Force set to FREE` と表示され、Semaphore 2 で慎重に処理されることを確認。
3. 有料版キーで実行中に意図的に 429 を発生させた場合、`[TIER] 429 detected. Downgrading to FREE...` という警告の後、処理が止まらずに継続することを確認。

## ブラウザでの動作確認
- Web UI から PDF をアップロードして実行。
- ブラウザのコンソールやサーバーログで、常に `tier='free'` として処理が開始されていることを確認。
- 処理が完了し、成果物（Markdown, Workflowy形式）が正常にダウンロードできることを確認。
