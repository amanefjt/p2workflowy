---
name: p2workflowy-context
description: Provide project-specific architecture context for p2workflowy. Use when editing pipeline boundaries, TreeNode/RawChunk flows, book integration, or performance-sensitive translation paths.
---

# p2workflowy-context

## Quick Context

- 入口: `main.py`
- 全体制御: `core/pipeline.py`
- 構造化中核: `core/phase3_structure.py`
- 書籍制御: `core/book_manager.py`
- 書籍統合: `core/engine/p3_structure/state_integrator.py`

## Working Rules

1. フェーズ責務（Phase1-5）を跨いでロジックを混在させない。
2. `TreeNode` の構造安定性（id/role/children）を壊す変更は回避する。
3. 書籍統合での `chN_` プレフィックスと見出しシフトは必須。
4. `_p2` 出力契約を破る変更は受け入れない。

## Performance Notes

- 長文翻訳では直列待ちを避け、並列実行前提の設計を維持する。
- 変更時は paper/book の両モードで劣化がないかを確認する。
