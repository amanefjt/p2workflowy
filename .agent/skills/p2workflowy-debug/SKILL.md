---
name: p2workflowy-debug
description: Practical implementation recipes and debugging protocols for the p2workflowy pipeline. Fact-based, logic-driven troubleshooting.
---

# P2Workflowy 実装レシピ & 技術デバッグ・プロトコル (p2workflowy-debug)

## 1. 救出パターンの実装 (Standard Recipes)

### **Recipe A: 埋没見出しの正規表現剥離 (Heading Regex Separation)**
本文ブロックに含まれてしまった見出しを分離するための `lookbehind` パターン。
- **判定式**: `re.split(r"""(?:(?:^|\n|(?<=[.!?;:\"'])\s*)(?P<head>[A-Z][^a-z]{5,}))\b""", text)`
- **主要な境界**: 句読点（. ! ?）および引用ブラケット（" '）の後、かつ「大文字から始まる 5 文字以上の単語」が出現した地点。

### **Recipe B: 文字列正規化比較 (Squash Match Protocol)**
目次情報（TOC）と本文アンカーを正確に照合するためのアルゴリズム。
- **正規化**: `re.sub(r'[^a-zA-Z0-9]', '', text.lower())`
- **判定基準**: `target_squash.startswith(toc_squash)` かつ `len(target_squash) < len(toc_squash) * 1.5`（一致部の長さを極端に超えないこと）。

## 2. ログとディレクトリ構造によるデバッグ

### **State ファイルの確認**
1. `state/` 配下の `phaseN_output.json` を開き、問題のノードの論理的な `role` と `id` を確認する。
2. `metadata` 内の物理フラグ（`is_header`, `font_size`）が VLM の判断を裏書きしているかを監査する。

### **LLM の思考プロセスの追跡**
1. `logs/` 配下にある Gemini の最新プロンプト入出力を開き、プロンプト内での「文脈の欠損」や LLM 側の「迷い」を特定する。
2. 特に翻訳（Phase 4）の成功・失敗は、`context_buffer` のサイズ調整（バッチ・サイズ）に起因することが多い。

## 3. 典型的な失敗モードと対策 (Failure Modes)

| 失敗モード | 判定基準 (Audit Criterion) | 技術的解決 (Recipe) |
| :--- | :--- | :--- |
| **Slicing Failure** | 相対ページ番号 `0` のチャンクが見つからない | `calibrate_page_offset` による物理ページと論理ページの再同期。 |
| **Heading Vanish** | `normalize_heading` 後の文字数が 3 未満 | 元の `RawText` を保持し、LLM による再分類（ヘッダー再昇格）を行う。 |
| **ID Drift** | フェーズを跨いで ID が変わる | `Node.id` の生成を、インジェスト時の `raw_text` ハッシュに強制固定。 |

## 4. デバッグ・ステップ
1. 現象の特定（例: 翻訳が出ない）。
2. `state/` JSON での中間構造の健全性チェック。
3. 問題のある `id` の特定と、`logs/` でのプロンプトの逆引き。
4. [Rule 03: コーディング・スタンダード](file:///Users/shufujita/Antigravity/p2workflowy/.agent/rules/03_coding_standards.md)に基づき、サニタイズや型定義を確認・修正。
