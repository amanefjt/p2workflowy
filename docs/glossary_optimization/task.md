# Task: 語彙最適化と TTFT 計測

## 完了済み
- [x] **パフォーマンス計測基盤の構築**: `llm_client.py` に TTFT, TPS, Token Usage を CSV 出力するロジックを実装。
- [x] **語彙統合ロジックの実装**: `phase2_meta.json` (AI抽出) と `glossary.csv` (ユーザー指定) のマージ処理を実装。
- [x] **フィルタリングモードの実装と検証**:
    - **Mode 0 (Baseline)**: 全語彙を注入。
    - **Mode 1 (Dynamic)**: チャンクごとに動的抽出。
    - **Mode 2 (Global)**: 論文全体で事前抽出。
- [x] **実験の実施と比較分析**: `NSTsample.txt` を用いて各モードの TTFT を実測。
- [x] **最適解の選定とリファクタリング**: 実績値に基づき Mode 0 を採用。不要なフィルタリングロジックをコードから排除。
- [x] **ドキュメントの訂正**: 「要約をプロンプトから除外する」という誤った方針を「常に含める」に修正。

## 成果物
- 実測データ: `state/ttft_metrics.csv`
- 解析レポート: `docs/glossary_optimization/walkthrough.md`
