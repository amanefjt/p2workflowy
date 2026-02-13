/**
 * formatter.ts: Logic for converting Markdown to Workflowy (OPML-compatible) format.
 * Ported from src/utils.py
 */

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
