---
name: golden-verification
description: Verify p2workflowy output quality against golden hierarchy rules. Use before completion claims for structure-sensitive changes.
---

# golden-verification

## Verification Targets

- `phase3_structure.json` の見出し抽出が期待どおりか。
- 最終 `_p2.md` / `_p2.txt` の非対称階層が維持されているか。
- 書籍モードで章境界・見出しシフト・ID衝突回避が成立しているか。

## Checklist

1. English/Japanese の階層関係（nested/parallel）を確認。
2. 除外セクションと注釈再配置の結果を確認。
3. 章統合後のタイトル重複やインデント破綻がないか確認。
4. 期待構造と差分がある場合は、壊れたフェーズまで戻って原因を追跡。
