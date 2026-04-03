# p2workflowy 論理構造マトリックス

## モードと変数の関係

| パラメータ | 指定 | 影響範囲 | 期待される動作 |
| :--- | :--- | :--- | :--- |
| `is_book` | **True** (Book) | Phase 3, 4, 5 | 目次ベースの構造化。**セクション別の日本語要約を生成する**。Markdown は `ideal_bookmdstructure.md` 形式。 |
| | **False** (Paper) | Phase 3, 4, 5 | レジュメの見出しベースの構造化。**セクション別要約は生成しない**。Markdown は `ideal_mdstructure.md` 形式。 |
| `resume_only` | **True** | Phase 4, 5 | 翻訳をスキップ。出力は「全体要約 + セクション別要約(Bookのみ) + 原文」。 |
| | **False** | Phase 4, 5 | 翻訳を実行。出力は「全体要約 + 英語本文 + 日本語本文」。 |
| `export_mode` | `p2workflowy` | Phase 5 | バイリンガル出力（英語 + 日本語）。日本語セクションを `##` (Level 2) に配置。 |
| | `ronbunnihongo`| Phase 5 | 日本語のみ出力。 |

## データフローと ID 管理

### Phase 3: `sections_dict` のキー形式
重複するタイトル（例: Introduction, Conclusion, Preface）を正確に翻訳・紐付けるため、キーを以下のように統一する。

- **Paper Mode**: `ID|Title` (実装済み)
- **Book Mode**: `ID|Title` (**未実装: 修正が必要**)

### Phase 4: 翻訳結果の紐付け (`rebuild_translated_tree`)
`translated_sections` および `section_resumes` の引き当てロジック。

1. `en_node.id` から `ID|` で始まるキーを `sections_dict` から探す。
2. マッチしたキーを使って `translated_sections` (本文) と `section_resumes` (要約) を取得する。

## 修正が必要な箇所

### 1. `core/phase3_structure.py`
- `build_tree` 関数内の `is_book` ブランチで、`sections_dict` のキーを `Title (c)` 形式から `ID|Title` 形式に変更する。

### 2. `core/phase4_translate.py`
- `rebuild_translated_tree` 関数内で、`section_resumes` からデータを取得する際、タイトルベースではなく、本文と同じ ID ベースのキー（`translated_key`）を使用するように変更する。
- `process_section` から返される `section_name` が `ID|Title` 形式であることを前提とした処理に統一する。
