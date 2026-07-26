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
4. 最小修正を入れ、`--session <id> --resume <N>` で**壊れたフェーズだけ**を回し直して再検証する（毎回フル実行しない。Phase 1 の OCR は重く高コスト）。

## まず見るファイル

| 症状 | 見るファイル |
|---|---|
| 構造化の挙動が想定と違う | `state/<session_id>/phase1_route.json`（実際に通ったルート = `docling`/`vlm`/`native_fallback`。Phase 3 の分岐はここで決まる） |
| 書籍のルーティングが想定と違う | book session の `routing_decision.json`（書籍単位で 1 回だけ判定される） |
| 見出しが出ない/多すぎる | `phase3_structure.json`, `phase3_sections.json` |
| 序論が前セクションに吸われる | `phase2_meta.json` の `intro_pre_heading` |
| プロンプト修正が効かない | `core/coreprompts.json` は `@lru_cache` される。**プロセス再起動が必要** |

## Known Failure Modes

- **見出し消失**: 正規化や分割ルールで先頭文字が落ちる。
- **ID ドリフト**: フェーズ間で id 生成ルールが不統一。
- **書籍統合崩れ**: 統合時の見出しシフト／重複タイトル除去漏れ。
- **翻訳遅延**: 過度な直列化や同期待機によるスループット低下（`max_concurrent_sections` は 8 が既定。1 にしない）。
- **偽陽性に注意**: `[Unlabeled Section]` は仕様であってバグではない。

## Evidence First

修正提案の前に、再現手順・観測ログ・壊れた中間成果物を揃える。
