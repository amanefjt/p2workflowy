# 05 エクスポート基準と黄金律 (05_export_standards.md)

## 1. 黄金律：非対称階層 (Asymmetric Golden Rule)

最終的な出力（特に Workflowy テキスト）は、[docs/export_spec/](file:///Users/shufujita/Antigravity/p2workflowy/.worktrees/golden-rewrite/docs/export_spec/) で定義された構造を絶対的な正解とします。

### Workflowy 出力の基本構造:
1.  **タイトル**: ファイルの先頭に配置（最上位）。
2.  **レジュメ/抽象 (Resume/Abstract)**: タイトルの直下に配置。
3.  **English text (親子型)**: 
    - 「- English text」を親ノードとする。
    - 各セクション（Section）は、この親ノードの**子要素としてネスト**される。
4.  **日本語本文 (並列型)**:
    - 「- 日本語本文」をマーカー（Title Node）として配置。
    - 各日本語セクション（Section）は、このマーカーの**「兄弟要素（Sibling）」として、同一レベルに並列展開**される。

### Markdown 出力の階層比率:
-   **書籍タイトル**: `#` (H1) で開始。
-   **書籍全体の要約 / 章タイトル**: `##` (H2) を使用。
-   **セクション（各章レジュメ等）**: `###` (H3) を使用。
-   **英語原文 / 日本語本文の中身**: 子要素としてさらにネスト（H4/H5）。

## 2. セクション除外ロジック (Section Exclusion)

学術論文の翻訳において、以下のボイラープレートセクションは原則として**除外（Skip）**します。

-   **除外対象**: `References`, `Bibliography`, `Acknowledgements`, `Conflict of Interest`, `Funding`, `Data Availability`.
-   **保持対象**: `Appendix` (付録) は情報の価値が高いため、必ず保持します。
-   **実装の注意**: `TreeConstructor.construct()` において、見出しの内容（Role: Heading）をスキャンし、上記のキーワードが含まれる場合はその子要素を含めてツリーから切り捨てます。

## 3. 注釈の再配置 (Endnotes Positioning)

論文の途中に散在する脚注（Footnotes）や注釈（Notes）は、Workflowy 上の可読性を高めるため、各言語ブロックの末尾に**移動（Reposition）**します。

-   **英語注釈**: `English text` ブロックの最後のセクションの後にまとめて配置。
-   **日本語注釈**: `日本語本文` ブロックのすべての見出しが終了した最後（フッター）に配置。
-   **形式**: ノードロールが `note` であるものを収集し、`phase5_export._reposition_notes()` によって末尾に一括配置します。

---

## 4. 参照リファレンス

実装や検証の際は、以下のファイルを常に確認してください。
- [ideal_wfstructure.txt](file:///Users/shufujita/Antigravity/p2workflowy/.worktrees/golden-rewrite/docs/export_spec/ideal_wfstructure.txt) (Workflowy 理想構造)
- [README.md](file:///Users/shufujita/Antigravity/p2workflowy/.worktrees/golden-rewrite/docs/export_spec/README.md) (Markdown 理想構造)

---

## 5. 書籍モード統合仕様 (Book Mode Integration)

書籍モードでは、各章の出力を `TextBookIntegrator` により「単純積み上げ」で結合し、書籍全体としての構造を再構築します。

### 階層シフト (Indentation & Heading Shift):
- **Workflowy (.txt)**:
    - `Book Title`: Level 0 (インデントなし)
    - `[Summary] / Chapter Title`: Level 1 (インデントなし, `- ` プレフィックス)
    - `Resume / Section Text`: Level 2 (1タブ, `\t- `)
- **Markdown (.md)**:
    - `Book Title`: `#` (H1)
    - `[Summary] / Chapter Title`: `##` (H2)
    - `Resume Content / Section Titles`: `###` (H3)
    - **実装**: `shift_markdown_headings(shift=1)` および `shift_workflowy_indent(shift=1)` を適用して階層を適正化します。

### 全体レジュメの高品質変換:
- 書籍全体の要約（Global Resume）は Markdown 形式で生成されますが、Workflowy 出力時には `WorkflowyEngine.render_resume` を使用します。
- これにより、章別レジュメと同一のクリーンな箇条書き（空行なし、`clean_heading_text` による `#` 除去済み）としての出力を保証します。
