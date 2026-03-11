# 実装計画：徹底的なデバッグと品質改善

現在のコードベースに含まれる論理的なバグ（特に出力のネスト構造）および、静的解析で指摘される可能性のあるインポート不足や型ヒントの不備を修正します。

## 修正内容

### 1. 出力形式のネストレベル修正
- **対象ファイル**: `core/phase5_export.py`
- **問題**: 
    - 以前の修正で「日本語本文」の子要素をネストさせてしまったが、P2の正しい仕様では「日本語本文」と各セクション見出し（一つ目の見出し等）は同レベル（Sibling）であるべき。
    - Markdown期待値: `## 日本語本文` に対して各見出しも `##` (Level 2)。
    - Workflowy期待値: `- 日本語本文` に対して各見出しも `base_depth=0`。
- **修正**: `generate_markdown_output` および `generate_workflowy_output` 内の引数を修正。

### 2. インポート不足の修正 (typing)
- **対象ファイル**: `core/phase1_preprocess.py`, `core/phase2_meta.py`
- **問題**: シグネチャで `"Any"` (文字列) を使用しているが、`typing` から `Any` がインポートされていない。
- **修正**: `from typing import List, Any` 等に変更。

### 3. ID 比較の安定性向上
- **対象ファイル**: `core/phase4_translate.py`
- **内容**: `node.id` の比較時に `str()` 変換を挟むことで、`int` と `str` が混在した場合の `KeyError` や不一致を防止する。

### 4. ドキュメントの更新
- `docs/management/requirements_log.md` および `troubleshooting_log.md` に今回の修正内容を記録。

## 実施手順

1. `core/phase1_preprocess.py` のインポート修正。
2. `core/phase2_meta.py` のインポート修正。
3. `core/phase4_translate.py` の ID 比較ロジックの補強。
4. `core/phase5_export.py` のネストレベル修正。
5. 修正後のコードで `NSTsample.txt` または `Arbitrarysample.txt` を用いて、出力ファイルの構造（見出しレベルとインデント）を目視確認。

## 完了の定義 (DoD)
- [ ] `core/` 配下の主なモジュールでインポートエラーや未定義変数エラーが発生しない。
- [ ] 生成される `.md` ファイルの「日本語本文」に続くセクションが `##` (Level 2) で始まっている。
- [ ] 生成される `.txt` ファイルの「日本語本文」に続くセクションがインデントなし（Level 0）で出力されている。
- [ ] ログファイルが更新されている。
