# 管理ファイル（Rules）の英語化対応完了

## 実施内容
エージェントによるファイル操作の確実性と速度を向上させるため、以下の対応を完了しました。

### 1. 例外規定の追記
- [mission.md](file:///Users/shufujita/Antigravity/p2workflowy/.agent/mission.md) を更新し、**「ファイル名は英語を標準とする」**（内容は日本語）という例外規定を追加しました。

### 2. ルールファイルのリネーム（.agent/rules/）
すべての日本語ファイル名を snake_case の英語名に変更しました。

| 旧ファイル名 | 新ファイル名 |
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

### 3. 内部リンク・タイトルの整合
- 各ファイル内の 1 行目（タイトル）に含まれていたファイル名参照を、新しい英語名に更新しました。

## 次のステップ
- 本プロジェクトの基本的ルール（Rules）が英語名になったことで、今後の開発においてエージェントがより正確にこれらの定義を参照できるようになりました。
- 引き続き、`.worktrees/golden-rewrite` での作業を継続します。
