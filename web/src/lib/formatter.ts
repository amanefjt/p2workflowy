/**
 * formatter.ts: Logic for converting Markdown to Workflowy (OPML-compatible) format.
 * Ported from src/utils.py
 */



export const normalizeMarkdownHeadings = (markdownText: string): string => {
    if (!markdownText) return markdownText;

    const lines = markdownText.split('\n');
    let minLevel = 10;

    for (const line of lines) {
        const match = line.match(/^(#{1,10})\s/);
        if (match) {
            const level = match[1].length;
            if (level < minLevel) {
                minLevel = level;
            }
        }
    }

    if (minLevel === 10) {
        return markdownText;
    }

    const offset = minLevel - 1;
    const normalizedLines: string[] = [];

    for (const line of lines) {
        const match = line.match(/^(#{1,10})\s+(.*)/);
        if (match) {
            const currentLevel = match[1].length;
            const content = match[2];
            const newLevel = Math.max(1, currentLevel - offset);
            normalizedLines.push(`${'#'.repeat(newLevel)} ${content}`);
        } else {
            normalizedLines.push(line);
        }
    }

    return normalizedLines.join('\n');
};

export const markdownToWorkflowy = (markdownText: string): string => {
    if (!markdownText) return "";

    // First normalize headings
    const normalizedText = normalizeMarkdownHeadings(markdownText);

    const lines = normalizedText.split('\n');
    const workflowyLines: string[] = [];
    let currentHeaderLevel = 0;

    for (const line of lines) {
        if (!line.trim()) continue;

        // 1. Handle Headings (#) -> Convert to hierarchical list (H1-H10)
        const headerMatch = line.trim().match(/^(#{1,10})\s+(.*)/);
        if (headerMatch) {
            const level = headerMatch[1].length;
            const content = headerMatch[2];
            currentHeaderLevel = level;

            // Indent: (level - 1) * 2 spaces
            const indentSize = (level - 1) * 2;
            workflowyLines.push(`${" ".repeat(indentSize)}- ${content}`);
            continue;
        }

        // 2. Handle List Markers (-, *, 1.)
        const listMatch = line.match(/^(\s*)([-*+]|\d+\.)\s+(.*)/);
        if (listMatch) {
            const originalIndent = listMatch[1].length;
            const content = listMatch[3];

            // Indent: (currentHeaderLevel * 2) + originalIndent
            const indentSize = (currentHeaderLevel * 2) + originalIndent;
            workflowyLines.push(`${" ".repeat(indentSize)}- ${content}`);
        } else {
            // Body text
            const originalIndent = line.length - line.trimStart().length;
            const indentSize = (currentHeaderLevel * 2) + originalIndent;
            workflowyLines.push(`${" ".repeat(indentSize)}- ${line.trim()}`);
        }
    }

    return workflowyLines.join('\n');
};


/**
 * Markdownの見出し階層を考慮して階層的に分割する。
 * Ported from src/skills.py: _split_markdown_hierarchically
 */
export const splitMarkdownHierarchically = (text: string, maxLength: number = 4000): string[] => {
    // 1. まず H2 で分割
    const sections = splitByHeadingLevel(text, 2);

    const finalChunks: string[] = [];
    for (const section of sections) {
        if (section.length <= maxLength) {
            finalChunks.push(section);
            continue;
        }

        // 2. H2 が大きすぎる場合、H3 で分割
        const subsections = splitByHeadingLevel(section, 3);
        for (const sub of subsections) {
            if (sub.length <= maxLength) {
                finalChunks.push(sub);
                continue;
            }

            // 3. H3 も大きすぎる場合、H4 で分割
            const subsubsections = splitByHeadingLevel(sub, 4);
            for (const subsub of subsubsections) {
                if (subsub.length <= maxLength) {
                    finalChunks.push(subsub);
                    continue;
                }

                // 4. H4 も大きすぎる場合、段落 (\n\n) で分割
                const paragraphs = splitByParagraph(subsub, maxLength);
                finalChunks.push(...paragraphs);
            }
        }
    }

    return finalChunks;
};

const splitByHeadingLevel = (text: string, level: number): string[] => {
    const marker = "#".repeat(level) + " ";
    const lines = text.split('\n');
    const chunks: string[] = [];
    let currentChunk: string[] = [];

    for (const line of lines) {
        if (line.startsWith(marker)) {
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
    return chunks;
};

const splitByParagraph = (text: string, maxLength: number): string[] => {
    const paragraphs = text.split('\n\n');
    const chunks: string[] = [];
    let currentChunk: string[] = [];
    let currentLen = 0;

    for (const p of paragraphs) {
        const pLen = p.length + 2; // \n\n 分
        if (currentLen + pLen > maxLength && currentChunk.length > 0) {
            chunks.push(currentChunk.join('\n\n'));
            currentChunk = [p];
            currentLen = pLen;
        } else {
            currentChunk.push(p);
            currentLen += pLen;
        }
    }

    if (currentChunk.length > 0) {
        chunks.push(currentChunk.join('\n\n'));
    }
    return chunks;
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
