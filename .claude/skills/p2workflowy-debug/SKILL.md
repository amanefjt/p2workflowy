---
name: p2workflowy-debug
description: p2workflowy 固有のデバッグ手順。フェーズ出力の不整合・見出し消失・IDドリフト・翻訳スループット低下の調査に使う。
---

# p2workflowy-debug

全体のパイプライン構造は `docs/ARCHITECTURE.md` を参照。

## Debug Workflow

1. 現象を再現し、対象フェーズ（Phase 1〜5）を特定する。
2. `state/<session_id>/phaseN_*.json` を確認し、壊れた時点を切り分ける。
3. `logs/` の入出力を追って、根本原因を特定する。
4. 最小修正を入れ、同一入力で再検証する。

## Known Failure Modes

- **見出し消失**: 正規化や分割ルールで先頭文字が落ちる。
- **ID ドリフト**: フェーズ間で id 生成ルールが不統一。
- **書籍統合崩れ**: 統合時の見出しシフト／重複タイトル除去漏れ。
- **翻訳遅延**: 過度な直列化や同期待機によるスループット低下。

## Evidence First

修正提案の前に、再現手順・観測ログ・壊れた中間成果物を揃える。
