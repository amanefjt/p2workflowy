---
description: GitHub (origin) と Hugging Face Spaces (hf) の main ブランチへまとめてpushする
---

このリポジトリの main ブランチを GitHub と Hugging Face Spaces の両方へ push する。
ユーザーが `/deploy` を実行したこと自体が push の許可なので、途中で改めて許可を求めない。

手順:

1. `git status` で作業ツリーを確認する。コミットされていない変更がある場合は、push を止めて
   その旨を伝える（先に `/commit` でコミットするよう促す）。
2. `git branch --show-current` で現在のブランチを確認する。`main` でなければ push を止めて、
   どのブランチにいるかを伝える（意図しないブランチを hf の main に押し込まないため）。
3. `git push origin main` を実行する。
4. `git push hf main` を実行する。
5. 両方の結果を報告する。Hugging Face Spaces はビルドに1〜2分かかるため、その旨を添える。
   どちらかが失敗した場合はエラー内容をそのまま伝え、もう一方は実行済みか確認する。

これは判断の要らない機械的な操作なので、詳細な設計判断は不要。上記の手順通りに進めれば良い。
