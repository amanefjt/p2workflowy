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
- 最終成果物（Workflowy用、構造化英語）は入力ファイルと同ディレクトリ、全体要約は `intermediate/` フォルダという保存場所を明確化。

## 2026-02-15: レジュメ（Resume）形式の刷新と用語統一
**Goal**: Paper Mode の要約を「レジュメ（Resume）」と改称し、ユーザー指定の特定の階層構造と論理展開（CoT）の形式に刷新する。
**Changes**:
- **用語の変更**: 「要約（Summary）」を「レジュメ（Resume）」に変更。出力ファイル名やコード内のラベルもこれに準拠。
- **階層構造の刷新**: 
    - リサーチ・クエスチョン、核心的主張（Thesis）をトップレベルに配置。
    - セクションごとに「中心的な主張」とその子要素として主張の内容を配置。
    - 議論の展開（Chain of Thought）はセクション名を含めた形式（例: `Introductionの論理展開...`）で記述。
- **プロンプトの更新**: `SUMMARY_PROMPT` を上記の形式を確実に出力するように強化。

## 2026-02-15: Book Mode導入前の状態へのリバートとPaper Modeの安定化
**Goal**: Book Mode導入による複雑化を解消し、Paper Modeのデバッグと機能改善に集中できる状態に戻す。
**Changes**:
- **コードベースのリバート (commit 5b51491)**:
    - `src/main.py`, `src/skills.py`, `shared/prompts.json` を Book Mode 本格導入前の状態に復元。
    - Paper Mode のパイプラインを「要約先行型順次パイプライン（Summary-First Sequential Pipeline）」に固定。
- **Paper Mode の観測性向上 (Observability)**:
    - フェーズ3（並列翻訳）において、各チャンクの開始・終了をログ出力する機能を追加。
    - これにより、長時間かかる翻訳処理の進捗が可視化され、ハングアップとの区別が可能になった。
- **動作確認と品質担保**:
    - サンプル論文（Arbitrary locations...）を用いた全工程の実行テストを行い、正常終了することを確認。
    - 出力された Workflowy 形式テキストの階層構造および翻訳品質が良好であることを検証。
## 2026-02-15: 現状分析とクリーンアップ
**Goal**: 重複したロジックや未使用の定数・ファイルを削除し、コードベースのメンテナンス性を向上させる。
**Changes**:
- **未使用コードの削除**: 
    - `src/utils.py` から `split_into_sections`, `split_text_into_chunks`, `process_uploaded_file` を削除。
    - `src/constants.py` から Book モード関連の定数（`BOOK_*`）を削除。
- **設定ファイルの整理**: 
    - `shared/prompts.json` から `STRUCTURING_PROMPT` を削除。
- **ファイルシステムのクリーンアップ**:
    - `tests/` 内の古い実験用データ（`Arbitrary locations...`）や、最新版ではない古いテストスクリプトを削除。
    - プロジェクト内の各ディレクトリから `.DS_Store` を削除。
- **動作検証**: 
    - サニタイズやノイズ除去の主要機能について、残存したテストスクリプトが正常に動作することを確認。
- **検証用標準データの整備 (2026-02-15)**:
    - 論文モードの標準検証用として `Arbitrary locations- in defenc.txt` を `tests/sample_data/` に配置。
    - 検証手順と基準を `.agent/rules/testing_standards.md` に明文化。

## 2026-02-15: Phase 3 (並列翻訳) の安定化とチャンク戦略の改善
**Goal**: 並列翻訳フェーズでのフリーズを解消し、翻訳品質維持のためのセクション完結型分割を導入する。
**Changes**:
- **並列翻訳のフリーズ修正 (2026-02-15)**:
    - `asyncio.Semaphore(3)` を導入し、同時APIリクエスト数を制限。
    - リトライ時のエラー内容表示を追加し、観測性を向上。
- **セクション完結型チャンク分割 (2026-02-15)**:
    - ユーザー要望に基づき、可能な限りセクション（見出し）単位でチャンクを維持するロジックへ改善。
    - 文字数ベースの機械的な分割を抑制し、文脈の維持を優先する。

## 2026-02-16: Gemini 3 Flash への完全移行と最適化
**Goal**: 最新の Gemini 3 Flash モデルの性能を最大限に引き出し、処理の高速化と品質向上を両立させる。
**Changes**:
- **モデル設定の更新**: デフォルトモデルを `gemini-3-flash-preview` に固定し、その最新ベンチマーク（GPQA 90.4%等）と特性を `docs/management/model_optimization.md` にドキュメント化。
- **チャンクサイズの拡大 (2026-02-16)**:
    - Gemini 3 Flash の 65k 出力トークン制限を活かし、`MAX_TRANSLATION_CHUNK_SIZE` を **40,000文字** に拡大。
    - これにより、ほとんどの論文セクションが分割なしで処理可能となり、文脈維持性能が劇的に向上。
    - Gemini 2.5 Pro 比で 3倍の処理速度により、大きなチャンクでも低遅延での翻訳を実現。

## 2026-02-16: Workflowy変換ロジックの改善
**Goal**: `tests/sample_data/midashi_pattern.txt` の分析に基づき、Workflowy形式への変換精度と階層構造の再現性を向上させる。
**Changes**:
- **H1-H6 見出しへの完全対応**: これまで H4 までだった対応範囲を H6 まで拡大し、正規表現による動的なレベル判定を導入。
- **2スペースインデントの採用**: ユーザー指定のサンプルに基づき、インデント幅を 4スペースから 2スペース（`(level-1)*2`）に変更。よりコンパクトで階層の深い文書に対応可能。
- **階層維持ロジックの強化**: `markdown_to_workflowy` において、直前の見出しレベルを追跡し、その下の段落やリストアイテムを適切なインデントレベルで配置するように改善。
- **Pipelineの整合性調整**: `src/main.py` 内のハードコードされたインデント幅も 2スペース刻みに合わせて調整し、最終出力の一貫性を確保。

## 2026-02-16: 深い階層構造とネストされたリストのWorkflowy変換強化
**Goal**: Markdown内の深い見出し階層（H10まで）や、ネストされたリスト構造を正確にWorkflowy形式に変換できるようにする。
**Changes**:
- **見出しレベルの正規化 (Normalization)**: `normalize_markdown_headings` を導入。文書内の最小見出しレベルを H1 とみなし、全体をシフトすることで、階層の飛び（例: いきなりH2から始まる等）を解消。
- **相対インデントの保持**: リストアイテムや段落の行頭スペースを考慮し、現在の見出しレベルをベースラインとした相対的なインデント計算を導入。これにより、ネストされたリストがフラット化される問題を修正。
- **広範なサポート**: H10 までの見出しに対応。
- **フロントエンド同期**: Web版 (`formatter.ts`, `App.tsx`) についても Python版のロジックを完全移植し、2スペース刻みのインデントと階層維持ロジックを統一。
## 2026-02-16: 単体レジュメ出力の廃止と最終成果物への統合維持
**Goal**: 中間ファイルとしての `_resume.txt` の生成を停止し、ワークスペースを整理する。ただし、レジュメ自体は重要な成果物の一部であるため、最終的な Workflowy 形式の出力には引き続き含める。
**Changes**:
- **単体ファイル `_resume.txt` の出力停止**: Phase 1 で生成されるレジュメを独立したファイルとして保存しないように変更。
- **最終成果物 (Workflowy形式) への包含維持**: 最終的な `_output.txt` 内の「レジュメ (Resume)」セクションは維持し、翻訳本文と合わせて一つのファイルで確認できるようにした。
- **UI/メッセージの整理**: 処理完了時のメッセージから個別レジュメファイルへの案内を削除。
## 2026-02-16: 最終クリーンアップ
**Goal**: 不要なファイルおよびコードを削除し、最新の安定したパイプラインに基づいたドキュメントを再整備する。
**Changes**:
- **未使用コードの削除**: 
    - `src/utils.py` から `clean_header_noise`, `sanitize_structured_output`, `sanitize_translated_output`, `sanitize_filename` を削除。
    - 各ファイルから `difflib`, `os`, `time`, `re` 等の未使用インポートを削除。
- **ファイルシステムの清掃**:
    - `verify_fixes.py`, `tests/` 内の古い検証スクリプト, `docs/management/github_update_plan.md` を削除。
    - `intermediate/` ディレクトリ内の古い中間データを一掃。
- **構成ドキュメントの更新**:
    - `.agent/mission.md` を新規作成し、プロジェクトの現在のフェーズを定義。
    - `.agent/skills/p2workflowy_pipeline.md` および `docs/manual.md` を、論文モードに集中した v2.1 仕様に更新。
    - `requirements_log.md` および `troubleshooting_log.md` の記録を維持。
- **Pipelineの安定化確認**:
    - 論文モードの主要フロー（レジュメ生成、見出しベース分割、並列翻訳、Workflowy変換）に必要なコードのみが残っていることを確認。
