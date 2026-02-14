# p2workflowy v2.0 System Manual

## 1. System Overview
**p2workflowy** is a specialized pipeline for converting academic papers (PDF/Images) and books into deep, structured Workflowy outlines.
Version 2.0 (Feb 2026) introduces a robust, chunk-based architecture designed to handle large texts without hallucination or truncation.

- **Primary Model**: `gemini-3-flash-preview` (Required for long context and speed).

## 2. Core Architecture

### 2.1 Unified Chunking Strategy (`src/skills.py`)
Both Paper Mode and Book Mode now use a shared chunking logic to ensure consistency.

- **Paragraph-Aware Splitting**:
  - The system splits text into ~20,000 character chunks.
  - Crucially, it respects paragraph boundaries (`\n\n`) and line breaks (`\n`) to prevent cutting sentences in half.
  - **Benefit**: Reduces AI confusion at chunk boundaries, preventing "hallucinated" headers or broken sentences.

- **Parallel Processing**:
  - Chunks are processed in parallel (`asyncio.gather`) to maximize speed.
  - **Semaphore**: Book Mode limits concurrency to 5 threads to avoid API rate limits.

### 2.2 Paper Mode Flow
1. **Summarize**: Generates a global summary.
2. **Structure (Chunked)**: Splits the *entire* raw text into chunks and structures them in parallel.
   - *Why?* Previous versions tried to split by summary headers, which was fragile. Full-text chunking is robust against OCR noise.
3. **Translate**: Translates the clean Markdown section-by-section.

### 2.3 Book Mode Flow
1. **TOC Extraction**: Identifies chapters.
2. **Physical Splitting**: Splits the book into chapters using "Anchor Text" matching.
3. **Chapter Pipeline**:
   - **Summarize**: Chapter summary.
   - **Structure**: Uses the same **Chunked Structuring** as Paper Mode.
   - **Translate**: Translates section-by-section using `_split_markdown_by_headers`.

## 3. Prompt Engineering Guidelines

### 3.1 Strict Output Rules
To prevent "AI Rot" (meta-commentary, hallucinations), all prompts (`shared/prompts.json`) enforce strict rules:

1.  **NO Meta-Commentary**:
    - Forbidden: "Here is the structured text", "I removed the headers", "Final check".
    - Rule: Start directly with content, end with content.
2.  **Original English ONLY (Structuring)**:
    - The Structuring phase MUST output English. Japanese output here confuses the Translation phase.
3.  **No Redundant Headers**:
    - The AI is forbidden from repeating the Main Title (`# Title`) inside the body.
    - `Utils.sanitize_structured_output` programmatically removes duplicate H1s.

### 3.2 Translation Rules
- **No Self-Correction**: The AI must not say "I corrected a typo". It should just translate.
- **Handling Inconsistencies**: If the Glossary conflicts with the text, the AI is instructed to ignore the conflict and translate, rather than reporting it.

## 4. Troubleshooting & Gotchas

### 4.1 "H1 Header Duplication"
- **Symptom**: The same chapter title appears multiple times in the Workflowy output.
- **Cause**: In parallel processing, the AI often adds the title to *every* chunk.
- **Fix**: `Utils.sanitize_structured_output` removes all H1s after the first one.

### 4.2 "Introduction" Hallucination
- **Symptom**: `## Introduction` appears in the middle of a chapter.
- **Cause**: The AI sees a chunk starting with a general sentence and assumes it's a new intro.
- **Fix**: The system automatically scans for and removes `## Introduction` headers that appear after line 30.

### 4.3 "Missing Sections" in Long Papers
- **Symptom**: The output ends abruptly after page 10.
- **Cause**: Token limit of the model or single-pass processing.
- **Fix**: v2.0 uses **Chunked Structuring**. If parts are still missing, check `MAX_TRANSLATION_CHUNK_SIZE` in `src/skills.py` (Default: 20,000 chars).

## 5. Developer Notes
- **Testing**: Use `dummy_book.txt` for quick pipeline verification.
- **Logs**:
  - `docs/management/requirements_log.md`: History of feature changes.
  - `docs/management/troubleshooting_log.md`: Database of past errors and fixes.
