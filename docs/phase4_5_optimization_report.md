# Phase 4 & 5 Optimization Report (V2.0)

## 概要
本ドキュメントは、Phase 4 (Translation) の高速化実験と、Phase 5 (Export) の出力構造改善の結果をまとめたものです。

## Phase 4: 翻訳パイプラインの最適化 (The Golden Rule)
Gemini 3 Flash の特性を最大限に引き出すため、3つの実験（Trial A, B, C）を経て、速度と安定性の「黄金律（Golden Rule）」を確立しました。

### 採用された設定 (Golden Rule)
1.  **Schema-free Processing**: `response_schema` を無効化。
    - API側の制約（ペナルティ）による TTFT（待機時間）の急増（最大280秒超）を回避。
    - 代わりに正規表現による JSON 抽出フォールバックを実装し、堅牢性を確保。
2.  **Concurrency (Semaphore 4)**: 4並列での非同期リクエスト。
    - スループットを最大化し、全体処理時間を大幅に短縮。
3.  **Large Batching (6000 chars)**: 1リクエストあたりのバッチサイズを拡大。
    - リクエスト回数を減らし、API制限の回避とコンテキスト効率を向上。

### 実績数値 (NSTsample.txt 約2.4万文字)
- **合計処理時間**: 約 3分 (3:08)
- **スループット**: 120 tokens/sec 以上
- **TTFT (待機時間)**: 平均 15秒以下（スパイクなし）

## Phase 5: 出力構造の改善
Workflowy および Markdown 出力において、ユーザーの思考プロセスに最適な階層構造を実現しました。

### 改善ポイント
- **Top-Level Sections**: 
  - `レジュメ`、`English text`、`日本語テキスト` および
  - 論文内の主要セクション（`Abstract`、`introduction` 等）を、
  - タイトルの直下（Level 1）に並べる構成に変更。
- **階層の整合性**: Workflowy のインデント深度を調整し、Markdown の見出しレベル（H2/H3）と同期。
- **目視確認**: 全ての主要見出しがタイトルの直下（Workflowy ではインデントなし、Markdown では `##`）であることを確認済み。

## 結論
p2workflowy V2 は、V1 並みの「爆速」と、V2 固有の「ID管理・構造一貫性」を両立した安定版（Release Candidate）に到達しました。
