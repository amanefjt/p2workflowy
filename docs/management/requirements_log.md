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
