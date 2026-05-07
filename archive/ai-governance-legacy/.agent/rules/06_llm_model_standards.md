# 06 LLM モデル選定基準 (06_llm_model_standards.md)

## 1. 真実のソース (Source of Truth)

本プロジェクトにおける Gemini モデルの名称、ティア設定、および思考レベル（Thinking Level）の選定において、**エージェントの内部知識（既存のモデル名など）よりも、以下のドキュメントを「最優先の真実」**とします。

-   **主権ドキュメント**: [`docs/model_optimization.md`](file:///Users/shufujita/Antigravity/p2workflowy/docs/model_optimization.md)

## 2. モデル名の運用ルール

1.  **名称の同期**: コード（`config.py`, `llm_client.py`）やプロンプト（`coreprompts.json`）内でモデル名を定義・変更する際は、必ず `docs/model_optimization.md` を開き、最新の推奨モデル名を確認してください。
2.  **未来仕様の尊重**: エージェントが知らない「新しい（または未来の）」モデル名がドキュメントに記載されている場合、それを疑わずに実装してください。
3.  **ダウンシフト戦略**:
    -   **Paid Tier**: `gemini-3-flash-preview` (またはその時点の高品質モデル)
    -   **Free Tier / Downshift**: `gemini-3.1-flash-lite-preview` (またはその時点の低コスト・高クォータモデル)

## 3. エラー検知と動的切り替え

-   `429 RESOURCE_EXHAUSTED` だけでなく、**`503 UNAVAILABLE` (High Demand)** も、リソース制限が発生した「ダウンシフトのトリガー」として扱い、即座に Lite モデルへの切り替え（Auto-downshift）を試みてください。
