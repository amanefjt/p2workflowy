# requirements_log.md

## 2026-02-12: リファクタリング指示（Agentic Skillsパターンへの移行と辞書機能の追加）

### 🎯 目的
- `p2workflowy` プロジェクトを、従来のルールベースからLLMの「認知スキル（Cognitive Skills）」を活用したText-to-Textパイプラインへ移行。
- 外部CSVファイルによる「ユーザー定義辞書（Glossary）」機能を実装。

### 🛠 アーキテクチャ概要
1. **Skill: `StructureRestorer`**: PDF由来のノイズ除去とMarkdown構造の復元。
2. **Skill: `ContentSummarizer`**: Workflowy特化型の要約生成。
3. **Skill: `AcademicTranslator`**: 辞書適用型のアカデミック翻訳。

### 📋 主な要件
- XMLタグを用いたプロンプト構造化。
- `constants.py` の再定義。
- `skills.py` の新設。
- [自動後処理] LLMが出力する不要なマークアップ（`<thought>`, `<Ethnography>` 等）や不要な強調（`**`）を自動的に除去する。
- [セクション抑制] 参考文献、謝辞、著者情報などは構造復元段階で破棄し、翻訳および要約の対象外とする。
- [モデル最適化] Gemini 2.0/3.0 Flash 等のモデル特性に合わせ、出力欠落を防ぐための最適なチャンク分割（3.5万文字）と重複見出し削除ロジックを実装する。
- `utils.py` への名称変更と機能追加（CSV読み込み、IO）。
- `main.py` のパイプライン再構築。
- `input.txt` を入力とし、`intermediate/structured.md` を経由して `output/Final_Output.txt` を生成。
