---
name: systematic-debugging
description: Root-cause-first debugging process. Use for any bug, test failure, unexpected behavior, or performance regression before implementing fixes.
---

# systematic-debugging

## Iron Rule

原因が特定できるまで修正を入れない。

## Four Phases

1. Root cause investigation（再現、エラー読解、変更点確認）
2. Pattern analysis（正常系との比較）
3. Hypothesis test（最小変更で仮説検証）
4. Implementation（根本原因のみ修正し再検証）

## Stop Signals

- 推測で複数修正を同時投入しそうになった時点で停止する。
- 3回連続で外したら、個別修正でなく設計レベルを見直す。
