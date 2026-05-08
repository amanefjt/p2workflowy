# Requirements Log: P2Workflowy V3 (Golden Rewrite)

## 会話履歴とユーザー要望の集約

### 2026-04-01 - 04-03: Golden Rewrite 初期フェーズ
- **JSONからの脱却**: Fragile な JSON 出力を廃止し、XMLタグベースのパースへ移行。
- **TierManagerの実装**: 有料/無料版の制限に合わせてバッチサイズを自動調整。
- **Book Modeの確立**: 大規模PDFを章ごとに分割し、並列翻訳した後に統合。

### 2026-04-04: 最終堅牢化と API仕様最適化（TTFTペーシング）
- **Phase 4 直列化の禁止**: Gemini 3 Flash Preview の長大コンテキスト（Thinking: High）発火時に発生する「約4分のサーバー側一斉塩漬け現象」を回避するため、直列化による順次待機（Context Chaining等）は絶対に採用しない（アーキテクチャ制約）。
- **並列相殺 (Global Optimum) の確立**: `max_concurrent_sections = 4` での一斉並列処理（Scatter-Gather）を標準とし、待機時間を並列で一括消化するアプローチを唯一の正解とする。
- **Book Mode パス解決の修正**: 統合時に出力ファイルを見失うバグ（`_export` サブディレクトリの問題）を解消。
- **ティア伝播の修正**: `--lite` フラグが Phase 0 (Global Scan) に適用されない問題を、モデル解決の遅延タイミング化によって解決。
- **コード監査**: `main.py`, `book_manager.py`, `state_integrator.py` の引数フローの整合性を確保。
- **スモークテスト**: `chap3relations.pdf` にて書籍モード完走を確認。
