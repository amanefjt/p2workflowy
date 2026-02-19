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
    - Gemini 2.5 Pro 比で 3倍의 処理速度により、大きなチャンクでも低遅延での翻訳を実現。

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
- **Pipeline의 安定化確認**:
    - 論文モードの主要フロー（レジュメ生成、見出しベース分割、並列翻訳、Workflowy変換）に必要なコードのみが残っていることを確認。

## 2026-02-16: 書籍モード (Book Mode) の本格復活と洗練 (v2.2)
**Goal**: 書籍モードの処理フローを論文モードと同等の高品質なプロセスに刷新し、Workflowy形式での統合出力を実現する。
**Changes**:
- **洗練された処理フローの実装**:
    - 各章に対して「章レジュメ生成」→「レジュメをヒントにした構造化」→「並列翻訳」という論文モード準拠のフローを適用。
    - 各章の翻訳時に「章レジュメ」を文脈（Context）として提供し、文脈一貫性を向上。
- **専用プロンプトの導入**:
    - `BOOK_CHAPTER_STRUCTURING_PROMPT` を新規作成。書籍の章（Chapter）に最適化された見出し階層（H1: 章タイトル, H2: 節）を強制。
- **Workflowy形式の統合出力**:
    - 全体タイトル -> 全体レジュメ -> 各章（章名 -> 章レジュメ -> 各節 -> 本文）という階層構造を Workflowy 形式で自動生成。
- **安定性の向上**:
    - 未知の目次形式に対するフォールバックや、本文のない「部（Part）」の扱いを改善。

## v2.3: 書籍モードの並列処理（章内並列）
**Goal**: 章ごとに処理していた書籍モードを、章内の「節（Section）」単位で並列処理するように変更し、トークン制限の回避と高速化を実現する。
**Changes**:
- **章内並列処理の実装**:
    - 各章をさらに「節（Section）」に分割するロジックを追加。
    - `asyncio.gather` を使用して、章内の各節を並列に「構造化＋翻訳」処理。
    - これにより、長大な章でもトークン制限に引っかかりにくくなり、並列処理による高速化が実現。
- **モード選択の刷新**:
    - CLIのモード選択を `p: 論文 / b: 書籍` から `1: 論文モード / 2: 書籍モード` に変更し、UIを更新。
- **処理フローの確立**:
    - Phase 1: 全体レジュメ＆目次抽出
    - Phase 2: 章分割
    - Phase 3: 章ごとの順次処理 → **各章内で節並列処理**
    - Phase 4: 全体統合

## 2026-02-17: 書籍モード (Book Mode) の完全削除とコードベースの簡素化
**Goal**: 複雑化しメンテナンスコストが増大した書籍モードを廃止し、学術論文の処理（Paper Mode）に特化した最高品質の変換ツールとして再定義する。
**Changes**:
- **書籍モードの完全削除**:
    - `src/main.py`, `src/skills.py` から書籍処理パイプラインおよび専用クラスを削除。
    - `shared/prompts.json` から `BOOK_` プレフィックスのプロンプトをすべて削除。
    - Webフロントエンド (`web/src/App.tsx`, `lib/`) から書籍モードのUIとロジックを完全に削除。
- **論文モード (Paper Mode) のシングルパス化**:
    - モード選択を廃止し、起動時に自動的に（デフォルトで）論文処理を開始するシンプルなワークフローに変更。
- **ターミナル出力（CLI）の洗練**:
    - ユーザー要望に基づき、「[論文モード] を開始します」という冗長な表記を削除。
    - 翻訳フェーズ（Phase 3）での不要な改行を削除し、進捗表示が上書きされるように改善。
- **ドキュメントの同期更新**:
    - `README.md`, `manual.md`, `.agent/mission.md` を論文モード専用の内容に書き換え。
- **資産のアーカイブ**: 削除した書籍モード関連コードを `archive/book_mode/` に退避。

## 2026-02-17: 書籍モード "Map-Split-Reuse" アーキテクチャ刷新
**Goal**: 不安定だった書籍モードのアーキテクチャを根本から刷新し、論文モードと同等の安定性を確立する。
**Pattern**: 書籍を「正確にAI解析→プログラムで分割→各章に論文モードを適用」する3段階パターン。
**Changes**:
- **Phase 1: Full-Text Mapping**: Gemini 3 Flash のロングコンテキストを活かし、書籍全文から ToC + Anchor Text を JSON で抽出するプロンプトを追加 (`BOOK_TOC_MAPPING_PROMPT`)。
- **Phase 2: Anchor-Based Splitting**: Levenshtein距離を用いたファジーマッチで、OCR誤字を吸収しながら chapter の開始位置を特定し、プログラムで物理分割する `book_processor.py` を新規作成。
- **Phase 3: Reuse Paper Mode**: 書籍モード専用の翻訳ロジックを廃止。各章を独立した論文とみなし、既存の `PaperProcessorSkills`（レジュメ→構造化→翻訳）をそのまま適用. `asyncio.Semaphore(3)` で並列処理。
- **Phase 4: Mechanical Merging**: AI不使用。テンプレートリテラルで各章の結果を Workflowy 形式に統合。
- **CLI**: `python -m src.main input.txt book` (または `2`) で書籍モードを起動可能に。


## 2026-02-17: 書籍モードの翻訳欠落（Truncation）対策の実装
**Goal**: 長文の章（Introduction等）において、出力トークン制限により翻訳や構造化が途切れる問題を解消し、Gemini 3 Flash での安定した全文処理を実現する。
**Changes**:
- **構造化フェーズの安定化**: 長大な章（10万文字超）において、出力上限 8k トークンに抵触するのを防ぐため、`skills.structure_text_with_hint` でのチャンク分割処理（約6,000文字単位）をデフォルトで有効化。
- `MAX_STRUCTURING_CHUNK_SIZE`: `15000`
- `MAX_TRANSLATION_CHUNK_SIZE`: `15000`
- **検証**: 特大章（9.6万文字）が 17 パートに分割されて正常に処理されることを確認。

## 2026-02-18: 書籍モードの翻訳精度と章分割の抜粋改善 (v2.4)
**Goal**: 長大な章での翻訳途切れ（Truncation）と、アンカー検出失敗による章の欠落を解決する。
**Changes**:
- **二段構えのチャンク戦略 (v2.5) -> (v2.6 Revert)**:
    - **MAX_STRUCTURING_CHUNK_SIZE**: 出力途中に AI が要約を始めてしまうのを防ぐため、翻訳サイズと同じ **15,000文字** に統一。これにより高精度な全文保持を実現。
    - **MAX_TRANSLATION_CHUNK_SIZE**: 15,000文字を維持。
- **Abstract の明示化 (v2.6)**:
    - 構造化プロンプト（`STRUCTURING_WITH_HINT_PROMPT`）に、Abstract（概要）に対して `## Abstract` という見出しを強制するルールを追加。これにより、翻訳後の区別を明確化。
- **堅牢な章分割ロジック (`split_by_anchors`)**:
    - アンカーが見つからない場合に章をスキップする仕様を廃止。直前・直後の章位置から推測して分割を維持。
    - **タイトルフォールバック**: アンカー（本文冒頭）で見つからない場合、章タイトルで再検索するロジックを追加。
- **`fuzzy_find` の最適化**: シード検索に基づいた局所的な Levenshtein 距離計算により、全文スキャン時間を劇的に短縮。
- **目次抽出プロンプトの強化**: `anchor_text` としてより長いパラグラフ（100-200文字）を抽出するように指示し、マッチングの確実性を向上。
## 2026-02-19: 論文モードへの再集約と書籍モードのアーカイブ (v2.7)
**Goal**: 書籍モード開発で肥大化したロジックとプロンプトを整理し、単一論文の処理において最高精度と安定性を発揮する「論文モード専用ツール」へと原点回帰する。
**Changes**:
- **論文モードへの単一化 (Paper-Only Revert)**:
    - `src/main.py` から書籍モードの分岐（`run_book_pipeline`）および章分割ロジックを削除。
    - CLI引数のモード選択を廃止し、起動後即座に論文処理を開始するシンプルな構成へ戻した。
- **スキルの単純化と安定化**:
    - `src/skills.py` の構造化フェーズ（Phase 2）について、書籍モード用のチャンク分割処理をデフォルトで無効化。一括処理に戻すことで、チャンク境界での文脈途切れや「ワープ」問題を解消。
    - 翻訳フェーズ（Phase 3）の並列数（Semaphore）を 2 から 3 へ戻し、安定している範囲での高速化を再確保。
- **プロンプトのシェイプアップ**:
    - `shared/prompts.json` から書籍専用のプロンプトを削除.
    - 「日本語レジュメを絶対遵守せよ」という制約が招いた「ヒント汚染（日本語の混入や節の強制結合）」を解消するため、指示をシンプルに整理。原文（英語）の見出しと本文の維持を最優先する旧来のバランスに調整。
- **資産の保存とアーカイブ**:
    - 書籍モードの独自ロジック（`book_processor.py`）を `archive/book_mode/` に移動。
    - 開発記録を `docs/management/book_mode_retrospective.md` に集約・永続化。
- **動作確認**:
    - 長文論文（Arbitrary locations...）において、見出しの翻訳混入がなく、節の統合（1 & 2）も発生せず、最後まで原文どおりに構造化・翻訳されることを確認。

## 2026-02-19: レジュメ重複出力の抑制とプロンプト強化
**Goal**: 翻訳結果にコンテキストとしてのレジュメが混入する問題を解消し、出力の純粋性を確保する。
**Changes**:
- **翻訳プロンプトの厳格化**: `TRANSLATION_PROMPT` に、[Context] 情報を出力に含めないよう明示的な禁止指示を追加。
- **ドキュメント更新**: 事象と対策を `troubleshooting_log.md` に記録。

## 2026-02-19: ターミナル出力の洗練と進捗表示の改善
**Goal**: ツールを論文モード専用としてより使いやすくし、長時間の処理における視認性を向上させる。
**Changes**:
- **冗長な表記の削除**: 処理開始時の「(📄 論文モード)」という表記を削除。現在は論文モードのみのため不要。
- **翻訳フェーズの進捗詳細化**: Phase 3（並列翻訳）において、「チャンク n/total 翻訳中...」および「チャンク n/total 完了」という詳細な進捗を表示するように改善。
- **表示クリーンアップ**: `print_progress` において、行頭復帰（`\r`）と空白パディング（`:<80`）を適切に組み合わせ、前のメッセージが残存しないように修正。
- **進捗の同一行更新**: 並列処理中もパーセンテージ表示（[ 50%]）を維持しながら、最新のチャンクステータスで上書きし続ける滑らかな表示を実現。
## 2026-02-19: ウェブ版の完全同期 (v2.8)
**Goal**: ウェブ版のロジック、プロンプト、出力を最新の Python版（論文モード専用 v2.7）と完全に一致させる。
**Changes**:
- **パイプラインの同期**:
    - 「レジュメ生成」→「レジュメをヒントにした構造化」→「並列翻訳」の3段階フローをウェブ版にも適用。
    - `extractStructureFromResume` および `removeUnwantedSections` ロジックを TypeScript へ移植。
- **プロンプトの共通化**:
    - `shared/prompts.json` を最新化し、ウェブ版 (`web/src/lib/prompts.json`) と同期。
    - 「見落とされた見出し（Introduction等）の自動挿入」ルールを構造化プロンプトに追加。
- **UI/進捗表示の改善**:
    - 並列翻訳フェーズ（Phase 3）において、「チャンク n/total 翻訳中/完了」の詳細な進捗を表示するように改善。
    - 不要な「(📄 論文モード)」表記を削除し、Python版のクリーンな出力に合わせた。
    - 並列実行数（Concurrency）を Python版の Semaphore 数に合わせ 3 に調整。
- **出力形式の統一**:
    - Workflowy形式のインデント幅を 2スペースに再確認・統一。
    - 「要約 (Summary)」という表記を「レジュメ (Resume)」に統一。
- **Mermaid図の修正 (2026-02-19)**: README.md の Mermaid 図の構文エラーを修正し、最新の「レジュメ生成」フローを反映。
- **テストデータの秘匿化 (2026-02-19)**: `tests/sample_data/` を `.gitignore` に追加。

## 2026-02-19: 並列処理の最適化とウェブ版の完全同期 (v2.9)
**Goal**: Python版とウェブ版の並列処理設定を揃え、最新のプロンプト（翻訳時の要約混入対策済み）をウェブ版にも適用する。
**Changes**:
- **並列リクエスト数の増加**:
    - Python版 (`src/skills.py`) および ウェブ版 (`web/src/App.tsx`) の並列実行数（Semaphore/Concurrency）を **3 から 4** に引き上げ、安定性を維持しつつ更なる高速化を図った。
- **プロンプトの完全同期**:
    - `shared/prompts.json` を `web/src/lib/prompts.json` に反映。
    - 翻訳プロンプトにおける「コンテキスト（レジュメ）を出力に含めない」という厳格な制約がウェブ版にも適用された。
- **ウェブ版の表記修正**:
    - インターフェース上の細かな用語や注釈を最新の状態に更新。
