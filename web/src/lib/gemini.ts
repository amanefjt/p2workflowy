import { GoogleGenerativeAI } from '@google/generative-ai';
import {
    DEFAULT_MODEL,
    STRUCTURING_WITH_HINT_PROMPT,
    SUMMARY_PROMPT,
    TRANSLATION_PROMPT
} from './constants';

export class GeminiService {
    private genAI: GoogleGenerativeAI;
    private model: any;

    constructor(apiKey: string) {
        this.genAI = new GoogleGenerativeAI(apiKey);
        this.model = this.genAI.getGenerativeModel({ model: DEFAULT_MODEL });
    }

    // --- Phase 1: Resume Generation ---
    async generateResume(text: string): Promise<string> {
        const prompt = SUMMARY_PROMPT.replace('{text}', text);
        const result = await this.model.generateContent(prompt);
        const response = await result.response;
        return response.text();
    }

    // --- Phase 2: Structuring with Hint ---
    async structureTextWithHint(rawText: string, summaryHint: string): Promise<string> {
        const prompt = STRUCTURING_WITH_HINT_PROMPT
            .replace('{summary_hint}', summaryHint)
            .replace('{raw_text}', rawText);
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

}

// --- Batch Concurrency Utility ---
/**
 * Promise.all のバッチ制御版。
 * 最大 concurrency 件ずつ並列実行し、Rate Limit を回避する。
 */
export async function batchProcess<T, R>(
    items: T[],
    fn: (item: T, index: number) => Promise<R>,
    concurrency: number = 3
): Promise<R[]> {
    const results: R[] = new Array(items.length);
    const queue = [...items.entries()];

    const workers = Array(Math.min(concurrency, items.length)).fill(null).map(async () => {
        while (queue.length > 0) {
            const entry = queue.shift();
            if (!entry) break;
            const [idx, item] = entry;
            results[idx] = await fn(item, idx);
        }
    });

    await Promise.all(workers);
    return results;
}
