# Project PRD: p2workflowy V2 (Scrap & Build)

## 1. Project Overview
学術論文の PDF から抽出した生テキスト（ノイズ多数）を読み込み、要約・構造化・セクションごとの翻訳を行い、最終的に Workflowy に直接ペースト可能なタブインデントテキストを生成するツール「p2workflowy」の V2 を新規開発します。

旧バージョン（V1）のアーキテクチャは完全に破棄し、データが一方向に流れるクリーンな「パイプライン・アーキテクチャ」としてゼロベースで再構築します。最終目標は「FastAPI + React」構成の Web アプリケーションですが、今回のフェーズではその核となる 「完全に独立・疎結合化された Python コアバックエンド」 を構築します。

環境要件: Python 3.11 以上。依存ライブラリ（google-genai, thefuzz, wordninja 等）は requirements.txt で管理し、機密情報は .env で管理すること。

## 2. Core Architecture Rules
Pipeline Pattern: 処理は Phase 1 から Phase 5 まで一方向に流れること。各フェーズの関数/クラスは独立させ、core/pipeline.py から呼び出す構成にすること。

Fault Tolerance（レジリエンス）: 見出し抽出に失敗しても [Unlabeled Section] を用いて必ず処理を完遂させること。

API-Ready: 実装ロジックを core/ 以下のモジュールに集約し、将来的に FastAPI から直接 run_pipeline() を呼び出せる構造にすること。

State Caching（分割保存）: 各フェーズの完了時に中間状態を state/ ディレクトリ内にファイル単位（phase1_clean.json 等）で保存し、--resume 引数で途中から再開可能にすること。

## 3. Pipeline Definitions (The 5 Phases)
Phase 1: Ingest & Preprocess（ノイズ除去と整形）
indi_preprocessor.md に従い、PDF 由来の不要な改行除去、Smart Unwrap、および glossary.csv を用いた用語保護付きの単語分割を実行する。

[重要] 文末判定において、引用ブラケット（[1] 等）を考慮した正規表現ロジックを実装すること。

Phase 2: Meta-Generation（要約と用語集生成）
coreprompts.json の SUMMARY_PROMPT および KEYWORD_EXTRACTION_PROMPT を使用し、論文のレジュメと動的キーワードを生成する。

[サンプリング規則] 原則として全文を投入するが、入力が極めて長大な場合（目安：30万トークン超）は、文字数ベース（冒頭 40,000 文字、末尾 20,000 文字）でサンプリングを行うこと。

[重要] 抽出キーワードは glossary.csv とマージし、翻訳用コンテキストとして準備する。

Phase 3: Structuring & Clipping（構造化とクリッピング）
indi_pre_scanner.md に従い、冒頭チャンクのスキャンによって確定済みアンカーを特定する。

indi_section_detector.md に従い、レジュメの見出しをアンカーとしてファジィマッチングを行い、英語ツリー（List[TreeNode]）を構築する。

除外キーワード判定には部分一致またはスコア 90 以上のファジィマッチングを適用し、不要セクション以降を破棄すること。

Phase 4: Sliding-Window Translation（チャンク翻訳）
indi_io_spec.md に従い、英語ツリーの骨格を維持したまま、テキストを日本語に置換する。

[高速化] セクション単位で並列処理（Async）を行い、セクション内部のチャンクのみに直列の Sliding Window（直前 1 チャンクの文脈維持）を適用すること。

[JSON 強制] Gemini API 呼び出し時は response_mime_type="application/json" を指定し、構造化出力を強制すること。

Phase 5: Export（フォーマット出力）
state/ から必要なデータを読み込み、indi_formatter.py のロジックを用いて Markdown と Workflowy 形式のファイルを生成する。

## 4. Execution Plan for Antigravity Agent
Antigravity の「Planning Mode」を利用し、以下のステップで自律的に開発を推進してください。

行動規範:

自律的検証: コード実装後、必ずターミナルでテスト実行し、エラーがあれば自ら修正すること。

成果物ベースの報告: 各 Step 完了時に Artifacts（実行結果や出力ファイル）を提示し、承認を得ること。

Step 1: Planning & Blueprinting
全ての indi_ 仕様ファイルを読み込み、core/pipeline.py を核としたディレクトリ構造と models.py の定義を提示せよ。

Step 2: Core Pipeline 1–3 実装
Phase 1 から Phase 3 までを実装し、構造化された英語ツリーが state/phase3_structure.json に正しく保存されることを検証せよ。

Step 3: LLM Translation Engine 実装
Phase 4 の並列処理と Sliding Window 翻訳を実装せよ。API 呼び出しには response_schema を用い、パースエラーを防止すること。

Step 4: Export & End-to-End Test
Phase 5 を統合し、CLI ツール（main.py）を完成させよ。テスト用ファイルを用いてエンドツーエンドテストを実行し、最終成果物を提示せよ。

補足：プロンプト変数の固定値
SUMMARY_PROMPT 用: "論文全体の構造、各セクションの論理構成を抽出してください。"

TRANSLATION_PROMPT 用: "原文のニュアンスを維持しつつ、自然な日本語に翻訳してください。"