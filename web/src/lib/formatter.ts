/**
 * formatter.ts: Logic for converting Markdown to Workflowy (OPML-compatible) format.
 * Ported from src/utils.py
 */

export interface ChapterData {
    id: number;
    title: string;
    raw_text: string;
    is_numbered?: boolean;
    section_type?: string;
    start_snippet?: string;
}

/**
 * AIの目次解析結果に基づいて、アンカーテキスト照合によりテキストを分割する (Anchor Text Method)
 */
export const splitByAnchors = (fullText: string, structureData: { chapters: any[] }): ChapterData[] => {
    const chaptersToc = structureData.chapters || [];
    if (chaptersToc.length === 0) {
        return [{ id: 1, title: 'Full Text', raw_text: fullText.trim() }];
    }

    const indices: number[] = [];
    const validChapters: any[] = [];
    let currentSearchPos = 0;

    for (const chapter of chaptersToc) {
        const title = chapter.title || "Unknown Chapter";
        const startSnippet = (chapter.start_snippet || "").trim();

        if (!startSnippet && !title) continue;

        const { pos, strategy } = findAnchorPosition(fullText, startSnippet, title, currentSearchPos);

        if (pos !== -1) {
            console.log(`[splitByAnchors] ✓ '${title}' — ${strategy} (pos=${pos})`);
            indices.push(pos);
            validChapters.push(chapter);
            currentSearchPos = pos + 1;
        } else {
            console.log(`[splitByAnchors] ✗ '${title}' — 発見できず`);
        }
    }

    const result: ChapterData[] = [];
    for (let i = 0; i < indices.length; i++) {
        const start = indices[i];
        const end = i + 1 < indices.length ? indices[i + 1] : fullText.length;
        const chunkText = fullText.slice(start, end).trim();
        const tocItem = validChapters[i];

        result.push({
            id: i + 1,
            title: tocItem.title,
            raw_text: chunkText,
            is_numbered: tocItem.is_numbered,
            section_type: tocItem.section_type,
            start_snippet: tocItem.start_snippet
        });
    }

    return result.length > 0 ? result : [{ id: 1, title: 'Full Text', raw_text: fullText.trim() }];
};

/**
 * アンカーテキストの位置を多段階で検索する
 */
const findAnchorPosition = (fullText: string, startSnippet: string, title: string, searchFrom: number): { pos: number, strategy: string } => {
    const targetText = fullText.slice(searchFrom);
    const normalizedSnippet = normalizeWhitespace(startSnippet);
    const normalizedTitle = normalizeWhitespace(title);

    // Plan A: Title + Snippet (Regex)
    if (normalizedTitle && normalizedSnippet) {
        const tWords = normalizedTitle.split(/\s+/).filter(w => w.length > 0);
        const sWords = normalizedSnippet.split(/\s+/).filter(w => w.length > 0).slice(0, 5);

        if (tWords.length > 0 && sWords.length > 0) {
            const pT = tWords.map(w => escapeRegExp(w)).join('\\s+');
            const pS = sWords.map(w => escapeRegExp(w)).join('\\s+');
            const regex = new RegExp(`(${pT})[\\s\\S]{0,500}?${pS}`, 'i');
            const match = targetText.match(regex);
            if (match && match.index !== undefined) {
                return { pos: searchFrom + match.index, strategy: "Plan A (Title + Snippet)" };
            }
        }
    }

    // Plan B: Snippet Only
    if (normalizedSnippet) {
        const sWords = normalizedSnippet.split(/\s+/).filter(w => w.length > 0).slice(0, 10);
        if (sWords.length >= 5) {
            const pS = sWords.map(w => escapeRegExp(w)).join('\\s+');
            const regex = new RegExp(pS, 'i');
            const match = targetText.match(regex);
            if (match && match.index !== undefined) {
                return { pos: searchFrom + match.index, strategy: "Plan B (Snippet Only)" };
            }
        }
    }

    // Plan C: Title Only
    if (normalizedTitle) {
        const tWords = normalizedTitle.split(/\s+/).filter(w => w.length > 0);
        if (tWords.length > 0) {
            const pT = tWords.map(w => escapeRegExp(w)).join('\\s+');
            const regex = new RegExp(pT, 'i');
            const match = targetText.match(regex);
            if (match && match.index !== undefined) {
                return { pos: searchFrom + match.index, strategy: "Plan C (Title Only)" };
            }
        }
    }

    return { pos: -1, strategy: "Failed" };
};

const normalizeWhitespace = (text: string): string => {
    return text.replace(/\s+/g, ' ').trim();
};

const escapeRegExp = (string: string): string => {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
};

export const normalizeMarkdownHeadings = (markdownText: string): string => {
    const lines = markdownText.split('\n');
    const normalizedLines: string[] = [];

    // Find the minimum heading level used in the document
    let minLevel = 100;
    for (const line of lines) {
        const match = line.match(/^(#+)\s/);
        if (match) {
            const level = match[1].length;
            if (level < minLevel) {
                minLevel = level;
            }
        }
    }

    // Return original if no headings found
    if (minLevel === 100) {
        return markdownText;
    }

    // Calculate offset (e.g., if min is ## (2), offset is 1 to make it # (1))
    const offset = minLevel - 1;

    for (const line of lines) {
        const match = line.match(/^(#+)\s+(.*)/);
        if (match) {
            const originalHashes = match[1];
            const content = match[2];
            const currentLevel = originalHashes.length;

            // Correct the level (minimum 1)
            const newLevel = Math.max(1, currentLevel - offset);

            normalizedLines.push(`${'#'.repeat(newLevel)} ${content}`);
        } else {
            normalizedLines.push(line);
        }
    }

    return normalizedLines.join('\n');
};

export const markdownToWorkflowy = (markdownText: string): string => {
    // First normalize headings
    const normalizedText = normalizeMarkdownHeadings(markdownText);

    const lines = normalizedText.split('\n');
    const workflowyLines: string[] = [];
    let currentLevel = 0;

    for (const line of lines) {
        const stripped = line.trim();
        if (!stripped) continue;

        // 1. Handle Headings (#) -> Convert to hierarchical list
        if (stripped.startsWith('#')) {
            // Count #
            const match = stripped.match(/^(#+)/);
            const level = match ? match[1].length - 1 : 0;
            currentLevel = Math.max(0, level);
            const content = stripped.replace(/^#+\s*/, '').trim();

            // Indent: 4 spaces per level
            const indent = "    ".repeat(currentLevel);
            workflowyLines.push(`${indent}- ${content}`);
            continue;
        }

        // 2. Handle List Markers (-, *, 1.)
        const listMatch = line.match(/^(\s*)([-*]|\d+\.)\s+(.*)/);
        if (listMatch) {
            const originalIndent = listMatch[1];
            const content = listMatch[3];
            const spaces = originalIndent.replace(/\t/g, '    ');
            // Internal list depth (2 spaces = 1 level)
            const listInternalLevel = Math.floor(spaces.length / 2);

            // Nest under current heading level + 1
            const newLevel = currentLevel + 1 + listInternalLevel;
            const newIndent = "    ".repeat(newLevel);
            workflowyLines.push(`${newIndent}- ${content}`);
        } else {
            // Body text -> Nest 1 level deeper than heading
            const bodyLevel = currentLevel + 1;
            const newIndent = "    ".repeat(bodyLevel);
            workflowyLines.push(`${newIndent}- ${stripped}`);
        }
    }

    return workflowyLines.join('\n');
};

export const splitMarkdownByHeaders = (text: string, maxChars: number = 4000): string[] => {
    const lines = text.split('\n');
    const chunks: string[] = [];
    let currentChunk: string[] = [];

    for (const line of lines) {
        if (line.trim().startsWith('#')) {
            if (currentChunk.length > 0) {
                chunks.push(currentChunk.join('\n'));
            }
            currentChunk = [line];
        } else {
            currentChunk.push(line);
        }
    }

    if (currentChunk.length > 0) {
        chunks.push(currentChunk.join('\n'));
    }

    // Check chunk sizes and split if necessary
    const finalChunks: string[] = [];
    for (const chunk of chunks) {
        if (chunk.length > maxChars) {
            finalChunks.push(...splitTextByLength(chunk, maxChars));
        } else {
            finalChunks.push(chunk);
        }
    }

    return finalChunks;
};

const splitTextByLength = (text: string, maxLength: number): string[] => {
    if (text.length <= maxLength) return [text];

    const chunks: string[] = [];
    let currentChunk: string[] = [];
    let currentLength = 0;

    const lines = text.split('\n');
    for (const line of lines) {
        const lineLen = line.length + 1; // +1 for newline
        if (currentLength + lineLen > maxLength) {
            if (currentChunk.length > 0) {
                chunks.push(currentChunk.join('\n'));
            }
            currentChunk = [line];
            currentLength = lineLen;
        } else {
            currentChunk.push(line);
            currentLength += lineLen;
        }
    }

    if (currentChunk.length > 0) {
        chunks.push(currentChunk.join('\n'));
    }

    return chunks;
};

// --- Book Mode Utilities ---

/**
 * Phase 1: テキストをH1見出し（# Chapter ...）で章ごとに分割する。
 * 見出しが見つからない場合はテキスト全体を1つの章として返す。
 */
export const splitByChapterHeaders = (text: string): ChapterData[] => {
    const lines = text.split('\n');
    const chapters: ChapterData[] = [];
    let currentTitle = '';
    let currentLines: string[] = [];
    let chapterId = 0;

    for (const line of lines) {
        // H1見出し（# で始まり ## でない）で章を区切る
        const h1Match = line.match(/^#\s+(.+)/);
        const isH2OrDeeper = line.match(/^#{2,}\s/);

        if (h1Match && !isH2OrDeeper) {
            // 前の章を保存
            if (currentLines.length > 0 || currentTitle) {
                chapterId++;
                chapters.push({
                    id: chapterId,
                    title: currentTitle || `Section ${chapterId}`,
                    raw_text: currentLines.join('\n').trim(),
                });
            }
            currentTitle = h1Match[1].trim();
            currentLines = [line];
        } else {
            currentLines.push(line);
        }
    }

    // 最後の章を保存
    if (currentLines.length > 0) {
        chapterId++;
        chapters.push({
            id: chapterId,
            title: currentTitle || `Section ${chapterId}`,
            raw_text: currentLines.join('\n').trim(),
        });
    }

    // 章が見つからなかった場合、全体を1つの章として扱う
    if (chapters.length === 0) {
        chapters.push({
            id: 1,
            title: 'Full Text',
            raw_text: text.trim(),
        });
    }

    return chapters;
};

/**
 * Phase 6: 全中間データをWorkFlowy形式の最終出力に組み立てる。
 *
 * 出力フォーマット:
 * - Book Summary
 *     - [book_summary content]
 * - Chapter 1: Title
 *     - Summary
 *         - [chapter_summary content]
 *     - Translation / Body
 *         - [chapter_translation content]
 * - Chapter 2: Title
 *     ...
 */
export const assembleBookWorkflowy = (
    bookSummary: string,
    chapters: ChapterData[],
    chapterSummaries: string[],
    chapterTranslations: string[]
): string => {
    const parts: string[] = [];

    // Book Summary (Root level)
    const summaryWorkflowy = bookSummary
        .split('\n')
        .filter(line => line.trim())
        .map(line => `    ${line}`)
        .join('\n');
    parts.push(`- Book Summary\n${summaryWorkflowy}`);

    // Each Chapter
    for (let i = 0; i < chapters.length; i++) {
        const chapter = chapters[i];
        const summary = chapterSummaries[i] || '(要約なし)';
        const translation = chapterTranslations[i] || '(翻訳なし)';

        // Convert translation markdown to workflowy format
        const translationWorkflowy = markdownToWorkflowy(translation)
            .split('\n')
            .map(line => `        ${line}`)
            .join('\n');

        // Summary lines (already in workflowy "-" format from SUMMARY_PROMPT)
        const summaryLines = summary
            .split('\n')
            .filter(line => line.trim())
            .map(line => `        ${line}`)
            .join('\n');

        parts.push(
            `- ${chapter.title}\n` +
            `    - Summary\n${summaryLines}\n` +
            `    - Translation / Body\n${translationWorkflowy}`
        );
    }

    return parts.join('\n');
};

/**
 * AIによるハルシネーション（重複したH1見出しや、文中の不自然なIntroduction）を強制的に削除する。
 */
export function removeRedundantHeaders(markdownText: string): string {
    const lines = markdownText.split('\n');
    let firstH1Found = false;
    const cleanedLines: string[] = [];

    for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();

        // 1. レベル1見出し (# Title) の重複排除
        if (line.startsWith('# ')) {
            if (!firstH1Found) {
                // 最初のH1だけは許可
                firstH1Found = true;
                cleanedLines.push(lines[i]);
            } else {
                // 2回目以降のH1はAIの幻覚なので、行ごと削除
                console.log('Detected and removed hallucinated H1:', line);
                continue;
            }
        }
        // 2. 文中の不自然な "## Introduction" の排除
        // ルール: 冒頭(例えば最初の30行以内)以外で "## Introduction" が現れたら削除する
        else if (line.toLowerCase().startsWith('## introduction')) {
            if (i > 30) { // 30行目以降なら「文中のIntroduction」とみなして削除
                console.log('Detected and removed hallucinated Introduction:', line);
                continue;
            } else {
                cleanedLines.push(lines[i]);
            }
        }
        else {
            cleanedLines.push(lines[i]);
        }
    }

    return cleanedLines.join('\n');
}
