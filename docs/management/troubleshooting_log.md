# Troubleshooting Log - P2Workflowy

## [2026/03/30] Pipeline Phase 0-1 Argument Mismatch
- **事象**: パイプライン実行時、Phase 0 で得られた単一のパス（文字列）が、後続フェーズで誤ってリスト `['path/to/pdf']` としてラップされてしまい、Phase 1 の OCR 処理がアサート失敗または無限ループ（再帰待機）に陥る。
- **原因**: `pipeline.py` における `run_phase1_v3` への戻り値の受け渡しが `[input_path]` のように不適切にリスト化されていた。
- **解決策**: 単一の文字列としてパスを直接渡すように修正。

## [2026/03/30] Gemini API TTFT High Latency
- **事象**: `gemini-3-flash-preview` (Thinking: High) 使用時、特定のバッチで TTFT（最初の一文字が出るまでの時間）が 260 秒に達する。生成自体は数秒（3s）で終わっており、API 側のリソース割り当てや Thinking 処理のオーバーヘッドと推測される。
- **解決策**: 現時点ではリフレッシュまたはリトライで対応。将来的に短いセクションは Flash 1.5 等の軽量モデルへの動的スイッチを検討。

## 2026-03-30: SaaS 移行・クォータ管理設計フェーズ

### 1. LLM 出力トークン制限超過 (max_tokens limit)
- **事象**: `brainstorming` 中に大規模な設計案を出力しようとした際、`generation exceeded max tokens limit (16384)` エラーが発生し、レスポンスが中断された。
- **原因**: 複数の設計セクション（Auth, Billing, DB, Pipeline）を一括で詳細に記述しようとしたため、コンテキストウィンドウではなく、単一の出力トークン上限に達した。
- **解決策**:
  - 設計案の提示を **セクション単位** で分割し、ユーザーの合意を得ながら段階的に進める。
  - `using-superpowers` に基づき、要点を絞った簡潔な記述を心がける。
  - 長大な設計書は Artifacts ではなく、リポジトリ内の `.md` ファイルに直接書き出し、ユーザーにはサマリーを提示する。
- **再発防止策**: 設計の規模が大きい場合は、最初から「要点提示」と「ファイル書き出し」に分離する。

---
## 過去の主要なトラブルシューティング

### 2026-03-30: VLM-First 黄金の再構築 v3 (Sliding Window & Simple Structure)
- [2026/03/30] `chap3relations.pdf` (23 pages) の Full VLM (Route C) による一括処理の成功。
- [2026/03/30] Abstract 後のタイトルなし序論を `[Unlabeled Section]` として自動抽出し、階層を維持することに成功。
- [2026/03/30] Workflowy 用テキストにおける「黄金の非対称（English Nested, Japanese Parallel）」の完全適用を検証。
- **スライディングウィンドウ方式 (Sliding Window)**: 前後のコンテキストを含める方式を採用。
  - VLM プロンプトに「前ページとの連続性」を意識させる明示的な指示を追加。
  - 幾何学的ルール「垂直の堀（3倍以上の空白）」を導入し、判定の決定論的要素を強化。

  - `response_schema` の使用を禁止し、プレーンテキスト JSON + 正規表現パースにフォールバック。
  - `Semaphore: 4` による流量制御を厳格化。

---
## 2026-04-01: 黄金の再構築 最終品質保証 (Final QA)

### 1. Markdown 見出しのハッシュ (#) がそのまま Workflowy の bullet に混入する
- **事象**: 全体要約（Global Resume）を `TextBookIntegrator` で変換した際、`#### # 見出し` のような二重ハッシュが残留。
- **原因**: 独自の正規表現変換ロジックのサニタイズ不足。
- **解決策**: 独自変換を廃止し、`WorkflowyEngine.render_resume` と `clean_heading_text` へ統合。

### 2. 統合ファイルにおけるインデントのズレ（Book Mode）
- **事象**: `combined_test_p2.txt` の要約がルートに置かれ、章との階層が崩れていた。
- **原因**: `shift_workflowy_indent(shift=1)` の適用ミスと `base_depth` の不整合。
- **解決策**: `render_resume(base_depth=1)` を使用し、親ノードとの整合性を厳密に制御。

### 3. 日本語本文のネスト解釈の誤り（Paper Mode）
- **事象**: Workflowy 出力において、「- 日本語本文」の直下にセクション見出しをネスト（1タブ追加）しようとしてしまった。
- **原因**: 仕様（非対称階層：英語はネスト、日本語は並列）の失念。
- **解決策**: `core/phase5_export.py` の `current_depth` を `0` に戻し、`ideal_wfstructure.txt` の「兄弟要素（Sibling）展開」という正解に準拠させた。
