# Requirements Log

## 2026-02-14: Paper Mode Refactor
**Goal**: Simplify and improve "Paper Mode" performance and stability.
**Trigger**: User request ("Too slow, accuracy not improving").
**Changes**:
- **Paper Mode Pipelineの刷新 (2026-02-14)**:
    - 従来の複雑な「概要からのセクション抽出＋分割処理」を廃止し、Gemini 1.5 のロングコンテキストを活かした「全文一括構造化」を採用。
    - 処理速度の向上と構造化精度の安定化を実現。
- **翻訳時のAIハルシネーション（自問自答）対策 (2026-02-14)**:
    - 翻訳対象とコンテキスト/用語集の矛盾に対するGeminiのメタ発言（「ひとりごと」）を抑制するため、プロンプトを強化。
    - `Utils.sanitize_translated_output` による事後検知・削除処理を実装。
- **Removal of Complexity**: Eliminate the "Split by Summary Headers" logic in Phase 2 for Paper Mode. This logic was fragile and slow.
- **Book Mode Refactor (Phase 5)**:
    - Extracted robust chunking logic (`_structure_text_chunked`) to be shared between Paper and Book modes.
    - Updated `BOOK_STRUCTURING_PROMPT` to strictly forbid meta-commentary, aligning with Paper Mode standards.
    - Restored full pipeline functionality including `translate_chapter`.
- **Improved Chunking Logic (2026-02-14)**:
    - Upgraded `_split_text_by_length` to respect paragraph boundaries (`\n\n`) and sentence boundaries where possible, rather than strict length-based cutting. This improves context preservation for both Paper and Book modes.
- **Documentation Consolidation (Manual v2.0)**:
    - Updated `.agent/skills/p2workflowy_pipeline.md` to reflect V2.0 changes.
    - Created `docs/manual.md` as a comprehensive system guide, aggregating architecture, rules, and troubleshooting tips.

## 2026-02-14: Workspace Cleanup
**Goal**: Remove unnecessary test files, redundant prompts, and archive old intermediate data to improve development environment clarity.
**Changes**:
- **Test File Organization**: Moved all `test_*.py` files from root to `tests/`.
- **Archive Intermediate Data**: Moved all processing artifacts from `intermediate/` (summaries, chapters, TOCs) to `intermediate/archive/2026-02-14_cleanup/`.
- **Prompt Cleanup**: Removed `STRUCTURING_PROMPT` from `shared/prompts.json` as it was deprecated by the more stable `STRUCTURING_WITH_HINT_PROMPT`.
- **General Housekeeping**: Removed temporary test files from `web/` directory and consolidated output files in the root.

## 2026-02-14: GitHub Repository Update
**Goal**: 同日に行われた一連の改善内容（チャンク分割向上、書籍モード調整、UI日本語化、ドキュメント整備等）をリモートリポジトリに同期。
**Changes**:
- **Master Branch Synchronized**: 18ファイルにおよぶ変更（新規作成、修正、削除、移動）をGitHubにプッシュ。
- **Implementation Plan & Manual**: 開発管理用の `github_update_plan.md` とユーザー向けの `manual.md` を追加。
- **Cleanup Confirmed**: テストファイルの `tests/` への集約と不要ファイルの削除が完了。

## 2026-02-15: Paper Mode & Book Mode Refinements
**Goal**: 品質と安定性の向上。
**Changes**:
- **Paper Mode 実行時エラーの修正**: `src/main.py` から `skills.py` の不適切なメソッド名を呼んでいたバグを修正。
- **Book Mode の決定論的処理とメモリ節約**:
    - アンカー分割後の章テキストを `.txt` ファイルとして `intermediate/chapters/` に保存するように変更。
    - メモリに全章のテキストを保持せず、必要に応じてディスクから読み込むように改善。
    - 構造化プロンプトから不要な要約指示を削除し、原文の保持を徹底。
