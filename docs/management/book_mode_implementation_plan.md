# 実装計画: 書籍モード (Book Mode) の復活と洗練

## 1. 概要
以前削除された「書籍モード」を、論文モードでの知見を活かしつつ、さらに洗練された形で復活させます。大規模な文献（書籍）を構造的に理解し、章ごとのレジュメ、イントロ、本文翻訳をセットにした Workflowy 形式の出力を生成します。

## 2. 実装詳細

### 2.1 プロンプトの追加・更新 (`shared/prompts.json`)
以下のプロンプトを新規追加します。
- `BOOK_GLOBAL_RESUME_PROMPT`: 全体に対する要約用（文化人類学シニア・リサーチャー視点）。
- `BOOK_TOC_EXTRACTION_PROMPT`: 目次の抽出・生成用。
- `BOOK_CHAPTER_RESUME_PROMPT`: 章ごとの要約用。（論文モードをベースに調整）。
- `BOOK_TRANSLATION_PROMPT`: 書籍向けの翻訳プロンプト（論文モードをベースに調整）。

### 2.2 スキル・クラスの拡張 (`src/skills.py`)
`BookProcessorSkills` を実装（または `PaperProcessorSkills` を共通化）し、以下のメソッドを追加します。
- `generate_book_global_resume()`
- `extract_table_of_contents()`
- `split_book_by_toc()`: 目次情報に基づき、テキストを章ごとに分割。
- `process_book_chapter()`: 一つの章に対して要約・イントロ・翻訳を順次実行。

### 2.3 メイン・パイプラインの更新 (`src/main.py`)
- 起動時に「論文モード (Paper Mode)」と「書籍モード (Book Mode)」を選択可能にします。
- 書籍モード用のシーケンシャルなパイプラインを実装します。
    1. 全体レジュメ生成
    2. 目次抽出
    3. テキスト分割
    4. 章ごとのループ処理（要約・イントロ・翻訳）
    5. 成果物の統合

### 2.4 出力形式の改善
Workflowy 形式で、以下の階層構造を持つようにします。
- 本のタイトル
  - 全体レジュメ
  - 第1章（タイトル）
    - 第1章 レジュメ
    - 第1章 イントロダクション
    - 第1章 本文...

## 3. タスクリスト
- [ ] `shared/prompts.json` に新しいプロンプトを追加
- [ ] `src/skills.py` に書籍モード用のロジックを実装
- [ ] `src/main.py` にモード選択と書籍モード用パイプラインを追加
- [ ] 動作確認（小規模なダミーテキストまたはサンプル書籍）
- [ ] `requirements_log.md` 等のドキュメント更新
