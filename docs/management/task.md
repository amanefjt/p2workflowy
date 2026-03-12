# Task: p2workflowy V2 パイプラインの安定化と信頼性向上

## 目的
実装済みの V2 パイプラインにおける残存バグ（ID型不一致、API 429 停止、インテント階層の誤認識等）を解消し、有料/無料ティアを問わず安定して完走できる状態にする。

## DoD (完了の定義)
- [x] **TierManager による動的変速**: API 429 エラー検知時に自動で無料版設定（Semaphore 2, RPM 15）へダウンシフトし、処理が継続されること。
- [x] **翻訳反映バグの解消**: 辞書キーの ID 型を統一し、翻訳結果が 100% 日本語本文に反映されること。
- [x] **非対称出力構造の維持**: `rules/export_structure.md` に従い、日本語本文の見出しが H2 (ネストなし) になっていること。
- [x] **プロンプト指示の完全性**: Phase 2/4 の `context_guide` が注入され、翻訳品質が向上していること。
- [x] **PDF フローの安定性**: `full_vlm` モードでの PDF 解析が正常に Phase 1 へ橋渡しされること。

## 進捗状況
- 2026-03-12: **安定化フェーズ完了**。
    - [x] テキストファイル (Arbitrarysample.txt) による E2E テスト完了。
    - [x] 翻訳品質と階層構造の修正確認。
    - [x] `TierManager` の実装と統合。
    - [x] リビングドキュメント (`requirements_log.md`, `troubleshooting_log.md`) の更新。
- [ ] **現在進行中**: PDF ファイル (`ALpdf.pdf`) による VLM インジェクションを含めた最終確認。

## 完了済みチェックリスト
- [x] Phase 5 階層構造の修正 (H3 -> H2)
- [x] Phase 4 ID型不一致の修正
- [x] TierManager の導入と llm_client への統合
- [x] context_guide プロンプト定数の注入
- [x] export_structure.md ルールファイルの作成
