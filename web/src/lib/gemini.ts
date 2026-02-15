import { GoogleGenerativeAI } from '@google/generative-ai';
import {
    DEFAULT_MODEL,
    STRUCTURING_PROMPT,
    STRUCTURING_WITH_HINT_PROMPT,
    SUMMARY_PROMPT,
    TRANSLATION_PROMPT,
    BOOK_SUMMARY_PROMPT,
    BOOK_CHAPTER_SUMMARY_PROMPT,
    BOOK_STRUCTURING_PROMPT,
    BOOK_TRANSLATION_PROMPT,
    BOOK_TRANSLATION_PROMPT_SIMPLE,
    TOC_ANALYSIS_PROMPT
} from './constants';

export class GeminiService {
    private genAI: GoogleGenerativeAI;
    private model: any;

    constructor(apiKey: string) {
        this.genAI = new GoogleGenerativeAI(apiKey);
        this.model = this.genAI.getGenerativeModel({ model: DEFAULT_MODEL });
    }

    // --- Shared / Paper Mode Methods ---

    /** Raw text → Clean Markdown structure */
    async structureText(text: string): Promise<string> {
        const prompt = STRUCTURING_PROMPT.replace('{text}', text);
        const result = await this.model.generateContent(prompt);
        const response = await result.response;
        return response.text();
    }

    /** Markdown → Workflowy-style summary */
    async generateSummary(markdownText: string): Promise<string> {
        const prompt = SUMMARY_PROMPT.replace('{text}', markdownText);
        const result = await this.model.generateContent(prompt);
        const response = await result.response;
        return response.text();
    }

    /** Paper section → Japanese translation */
    async translateSection(chunk: string, summaryContext: string, glossary: string): Promise<string> {
        const prompt = TRANSLATION_PROMPT
            .replace('{chunk_text}', chunk)
            .replace('{summary_content}', summaryContext)
            .replace('{glossary_content}', glossary);
        const result = await this.model.generateContent(prompt);
        const response = await result.response;
        return response.text();
    }

    // --- Book Mode Methods (Multi-Pass Pipeline) ---

    /** Phase 1: TOC Analysis (Extract structure as JSON) */
    async analyzeBookStructure(text: string): Promise<any> {
        const prompt = TOC_ANALYSIS_PROMPT.replace('{text}', text);
        const result = await this.model.generateContent(prompt);
        const response = await result.response;
        const responseText = response.text();

        try {
            // Robust JSON extraction (handle markdown blocks)
            const jsonMatch = responseText.match(/```json\s*([\s\S]*?)```/) || responseText.match(/{[\s\S]*}/);
            const jsonStr = jsonMatch ? jsonMatch[1] || jsonMatch[0] : responseText;
            const data = JSON.parse(jsonStr);
            return Array.isArray(data) ? { chapters: data } : data;
        } catch (e) {
            console.error('Failed to parse TOC JSON:', e, responseText);
            throw new Error('目次データの解析に失敗しました。');
        }
    }

    /** Phase 2: Structure a chapter with hint (chapter title as outline) */
    async structureWithHint(rawText: string, summaryHint: string): Promise<string> {
        const prompt = STRUCTURING_WITH_HINT_PROMPT
            .replace('{raw_text}', rawText)
            .replace('{summary_hint}', summaryHint);
        const result = await this.model.generateContent(prompt);
        const response = await result.response;
        return response.text();
    }

    /** Phase 2 (alt): Structure chapter using BOOK_STRUCTURING_PROMPT */
    async structureChapter(bookContext: string, chapterText: string): Promise<string> {
        const prompt = BOOK_STRUCTURING_PROMPT
            .replace('{overall_summary}', bookContext)
            .replace('{chapter_text}', chapterText);
        const result = await this.model.generateContent(prompt);
        const response = await result.response;
        return response.text();
    }

    /** Phase 3: Generate book-level summary from all structured chapters */
    async generateBookSummary(allStructuredText: string): Promise<string> {
        const prompt = BOOK_SUMMARY_PROMPT.replace('{text}', allStructuredText);
        const result = await this.model.generateContent(prompt);
        const response = await result.response;
        return response.text();
    }

    /** Phase 4: Generate chapter-level summary */
    async generateChapterSummary(chapterText: string): Promise<string> {
        const prompt = BOOK_CHAPTER_SUMMARY_PROMPT.replace('{text}', chapterText);
        const result = await this.model.generateContent(prompt);
        const response = await result.response;
        return response.text();
    }

    /** Phase 5: Translate a book chapter with full context */
    async translateBookChapter(
        bookSummary: string,
        chapterSummary: string,
        chapterText: string,
        glossary: string
    ): Promise<string> {
        const prompt = BOOK_TRANSLATION_PROMPT
            .replace('{overall_summary}', bookSummary)
            .replace('{chapter_summary}', chapterSummary)
            .replace('{glossary_content}', glossary)
            .replace('{chunk_text}', chapterText);
        const result = await this.model.generateContent(prompt);
        const response = await result.response;
        return response.text();
    }

    /** Phase 5 (Simple): Translate without structuring, using summary as context */
    async translateBookChapterSimple(
        bookSummary: string,
        chapterSummary: string,
        chapterText: string,
        glossary: string
    ): Promise<string> {
        const prompt = BOOK_TRANSLATION_PROMPT_SIMPLE
            .replace('{overall_summary}', bookSummary)
            .replace('{chapter_summary}', chapterSummary)
            .replace('{glossary_content}', glossary)
            .replace('{chunk_text}', chapterText);
        const result = await this.model.generateContent(prompt);
        const response = await result.response;
        return response.text();
    }
}

// --- Batch Concurrency Utility ---

/**
 * Promise.all のバッチ制御版。
 * 最大 concurrency 件ずつ並列実行し、Rate Limit を回避する。
 */
export async function batchProcess<T>(
    items: T[],
    fn: (item: T, index: number) => Promise<any>,
    concurrency: number = 3
): Promise<any[]> {
    const results: any[] = new Array(items.length);

    for (let i = 0; i < items.length; i += concurrency) {
        const batch = items.slice(i, i + concurrency);
        const batchResults = await Promise.all(
            batch.map((item, batchIdx) => fn(item, i + batchIdx))
        );
        for (let j = 0; j < batchResults.length; j++) {
            results[i + j] = batchResults[j];
        }
    }

    return results;
}
