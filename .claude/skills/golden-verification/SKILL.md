---
name: golden-verification
description: p2workflowy の出力品質を理想出力ルールに照らして検証する。見出し抽出・階層・書籍統合・モデル切替ロジックなど構造に関わる変更の完了宣言前に使う。
---

# golden-verification

## Verification Targets

- `phase3_structure.json` の見出し抽出が期待どおりか。
- 最終 `_p2.md` / `_p2.txt` の非対称階層（英語 nested / 日本語 parallel）が維持されているか。
- 書籍モードで章境界・見出しシフト・ID 衝突回避が成立しているか。

## Checklist

1. English/Japanese の階層関係（nested/parallel）を確認する。
2. 除外セクション（`References` 等）と注釈ノードの再配置結果を確認する。
3. 章統合後のタイトル重複・見出しシフト・インデント破綻がないか確認する。
4. 見出し判定ロジックを調整した場合は `phase3_structure.json` を直接開いて抽出結果を検証する。
5. `TierManager` やモデル切替ロジックを変更した場合は、paper/book 両モードで回帰確認を実施する。
6. 期待構造と差分がある場合は、壊れたフェーズまで遡って原因を追跡する（推測で複数箇所を同時に直さない）。
