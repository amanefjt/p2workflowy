# User Requirements Log

## Project Goal
Port existing Python desktop tool `p2workflowy` to a Web application.
- **Source Logic**: `https://github.com/amanefjt/p2workflowy` (Text processing logic)
- **Target Platform**: Cloudflare Pages
- **Tech Stack**: React + Vite + TypeScript + Tailwind CSS
- **AI Model**: Google Gemini API

## Core Features
1. **Authentication**:
   - No login required.
   - User inputs Gemini API Key.
   - Save API Key in `localStorage` (encrypted or plain).

2. **Dictionary (Custom Vocabulary)**:
   - File upload (CSV/JSON/TXT).
   - Sample download button.
   - **Persistence**: Save in `IndexedDB`. Persist across reloads.

3. **Workflowy Transformation**:
   - Port Python logic (Markdown to Workflowy indentation).
   - Sync Prompt/Settings with Python version (Shared JSON/YAML).

4. **UI/UX Flow**:
   - API Key Input.
   - Image Drag & Drop.
   - Dictionary Check.
   - Process with Gemini.
   - Display Result.
   - Copy to Clipboard.

## Implementation Details
- **OCR Logic**: Since Python source inputs text files, the Web version must implement pure Gemini Vision OCR (Image -> Text -> Structured Text).
- **Shared Config**: Use `shared/prompts.json` for prompts to ensure consistency between Python and Web versions.

## v1.0 Alpha (Web Port) - Text Mode
- **Input**: Changed from Image to `.txt` files only (as per user request).
- **Localization**: UI fully localized to Japanese.
- **Processing**: Removed Vision-based OCR. Now focuses on structuring and translating raw text.
- **Service-like UI**: Polished interface for general use.

## v1.2 (Model & UI Polish)
- **Default AI Model**: Changed default model to `gemini-3-flash-preview` ([公式ドキュメント](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/gemini/3-flash?hl=ja)) for both Python and Web versions.
- **Model Selection (Web)**: Added UI buttons to switch between models (Gemini 3 Flash, 1.5 Flash, 1.5 Pro, 2.0 Flash Exp).
- **Environment Support (Python)**: Added `GEMINI_MODEL` environment variable support to customize model in CLI.
- **Output Management (Python & Web)**:
  - The root node of the Workflowy output is now the **English Paper Title** extracted from the original structured text (# Title). (2026-02-13)
  - Sub-sections are properly nested under this English title.
- **Processing Modes (Paper vs Book)**: (2026-02-13)
  - **論文モード (Paper Mode)**: 数十ページ程度の文書向け。全体要約 + 並列翻訳。
  - **書籍モード (Book Mode)**: 100ページ超の書籍向け。全体構造分析 -> 章ごとの要約 -> 章ごとの翻訳 という階層的フローを実現。
- **UI Bug Fixes**:
  - Fixed typo "Genesis" -> "Gemini" in API settings.
  - Corrected hardcoded progress messages to reflect the actual model being used.
- **書籍モードの更なる改善 (2026-02-13)**:
  - **構成の変更**: 本のタイトルの直下に「全体の要約（Book Summary）」を配置し、各章ごとに「その章の要約（Chapter Summary）」と「翻訳本文」が並ぶ形式に再構築。
  - **不要な挨拶の削除**: AIによる「要約を作成しました」等のメタコメントをプロンプトレベルで禁止。
  - **部の階層調整**: 「部（Part）」の見出しのみのセクションは、要約や翻訳をスキップして見出しのみを出力し、Workflowy上で章と同じ階層に配置。
  - **参考文献の除外**: `References` や `Bibliography` などのセクションを自動的にスキップ。
  - **常体での翻訳**: 論文および書籍の翻訳プロンプトに「常体（だ・である調）で翻訳する」制約を追加。
- **処理速度の高速化 (2026-02-13)**:
  - **章ごとの並列化**: 書籍モードにて、各章の処理（要約→構造化→翻訳）を `asyncio.gather` で並列実行するように改善。章の内部順序は維持しつつ、全体の待ち時間を大幅に短縮。
  - **チャンクサイズの拡大**: `MAX_TRANSLATION_CHUNK_SIZE` を 40,000 文字に拡大。リクエスト回数を減らし、処理効率を向上。
