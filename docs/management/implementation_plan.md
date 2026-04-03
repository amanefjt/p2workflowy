# リポジトリ管理ファイル（Rules/Skills）の英語名へのリネーム計画

## 目的
AIエージェントによるファイルアクセスと管理を容易にするため、`.agent/rules/` および `.agent/skills/` 配下のファイル名を日本語から英語へ変更します。ただし、ドキュメントの内容自体はユーザーの利便性を考慮し、引き続き日本語を標準とします。

## ユーザーレビュー必須

> [!IMPORTANT]
> - ファイル名のみを英語に変更し、ファイル内部の記述（日本語）は維持します。
> - `mission.md` などの基本原則ファイルにおいて、「ファイル名は英語」という例外規定を追記します。

## 提案される変更

### 1. エージェント定義の更新

#### [MODIFY] [mission.md](file:///Users/shufujita/Antigravity/p2workflowy/.agent/mission.md)
- 「ドキュメントは日本語標準」という記述に、「ただし、ファイル名はエージェントの操作性を考慮し、英語（snake_case）を推奨・許容する」という旨の例外を追記します。

### 2. ルールファイルのリネーム（.agent/rules/）

以下のようにリネームを実行します（`mv` コマンド）:

| 現ファイル名 | 新ファイル名 |
| :--- | :--- |
| `01_物理証拠主権.md` | `01_physical_evidence_sovereignty.md` |
| `02_アトミック・エンジン・モジュール化.md` | `02_atomic_engine_modularization.md` |
| `03_アンラッパー聖域.md` | `03_unwrapper_sanctuary.md` |
| `エクスポート構造.md` | `export_structure.md` |
| `コーディング規約.md` | `coding_standards.md` |
| `パフォーマンス標準.md` | `performance_standards.md` |
| `思考優先メソッド.md` | `thought_first_method.md` |
| `書籍モード標準.md` | `book_mode_standards.md` |
| `論文モード安定化.md` | `paper_mode_stabilization.md` |
| `論文モード構造.md` | `paper_mode_structure.md` |

### 3. スキルファイルのリネーム（.agent/skills/）

※現在、ほとんどのスキルは英語名ですが、日本語名が見つかった場合は同様にリネームします。

## オープンな質問

> [!TIP]
> リネーム後、他のドキュメント内（`requirements_log.md`など）でこれらのファイルへのリンクがある場合、それらも自動的に修正してよろしいでしょうか？

## 検証プラン

### 自動検証
- `ls .agent/rules/` を実行し、すべてのファイルが期待通り英語名になっているか確認。
- `mission.md` の記述が正しく更新されているか確認。
