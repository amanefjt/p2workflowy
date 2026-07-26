---
name: golden-verification
description: p2workflowy の出力品質を理想出力ルールに照らして検証する。見出し抽出・階層・書籍統合・モデル切替ロジックなど構造に関わる変更の完了宣言前に使う。
---

# golden-verification

構造に関わる変更を「完了」と宣言する前に、理想出力（golden）と突き合わせて回帰がないことを確認する。

## 参照する golden 資産

| 入力 | 資産 |
|---|---|
| 論文テキスト | `data/input/paperplain/AL/`, `data/input/paperplain/NST/` |
| 論文 PDF | `data/input/paperpdf/AL/`, `data/input/paperpdf/NST/` |
| 書籍 PDF | `data/input/Booksample/`（理想出力なし。不変条件のみ確認） |

`paperpdf/NST/` が最高品質の参照基準。**まずここで回帰がないことを確認する。**

## 手順

1. 変更前後で同一入力を通し、`state/<session_id>/phase3_structure.json` の見出し抽出結果を直接開いて差分を取る（最終出力だけを見て判断しない）。
2. 最終 `_p2.md` / `_p2.txt` を同ディレクトリの理想出力と比較する。
3. 差分が出た場合は、壊れたフェーズまで遡って原因を 1 箇所に特定する。**推測で複数箇所を同時に直さない。**

## 不変条件チェック

- 英語ブロックが親子ネスト、日本語ブロックが並列展開になっているか（非対称階層）。
- `References` 系セクションが除外され、`Appendix` が保持されているか。
- 注釈ノードが言語ブロック末尾へ再配置されているか。
- 書籍モード: 章統合後のタイトル重複・見出しシフト・`chN_` プレフィックスによる ID 衝突回避・インデントが崩れていないか。

## 判定を誤りやすい点

- `[Unlabeled Section]` の出現は**バグではない**（Abstract 直後の見出しなし Introduction の正しい表現）。問題なのは、本来ある節タイトルが検出されず前セクションに吸収されている場合のみ。
- `TierManager` やモデル切替ロジックを触った場合は、paper / book 両モードで確認する（片方だけでは通らない経路がある）。
