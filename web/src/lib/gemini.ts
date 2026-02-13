import { GoogleGenerativeAI } from '@google/generative-ai';
import {
    DEFAULT_MODEL,
    STRUCTURING_PROMPT,
    SUMMARY_PROMPT,
    TRANSLATION_PROMPT,
    BOOK_STRUCTURE_PROMPT,
    BOOK_CHAPTER_SUMMARY_PROMPT,
    BOOK_STRUCTURING_PROMPT,
    BOOK_TRANSLATION_PROMPT
} from './constants';

export class GeminiService {
    private genAI: GoogleGenerativeAI;
    private model: any;

    constructor(apiKey: string) {
        this.genAI = new GoogleGenerativeAI(apiKey);
        this.model = this.genAI.getGenerativeModel({ model: DEFAULT_MODEL });
    }

    /**
   * Structure raw text into Markdown.
   * Uses Gemini to restore structure from disordered text (e.g. from PDF copy-paste).
   */
    async structureText(text: string): Promise<string> {
        const prompt = STRUCTURING_PROMPT.replace('{text}', text);
        const result = await this.model.generateContent(prompt);
        const response = await result.response;
        return response.text();
    }

    async generateSummary(markdownText: string): Promise<string> {
        const prompt = SUMMARY_PROMPT.replace('{text}', markdownText);
        const result = await this.model.generateContent(prompt);
        const response = await result.response;
        return response.text();
    }

    async translateSection(chunk: string, summaryContext: string, glossary: string): Promise<string> {
        let prompt = TRANSLATION_PROMPT
            .replace('{chunk_text}', chunk)
            .replace('{summary_content}', summaryContext)
            .replace('{glossary_content}', glossary);

        const result = await this.model.generateContent(prompt);
        const response = await result.response;
        return response.text();
    }

    // --- Book Mode Methods ---

    async analyzeBookStructure(text: string): Promise<string> {
        const prompt = BOOK_STRUCTURE_PROMPT.replace('{text}', text);
        const result = await this.model.generateContent(prompt);
        const response = await result.response;
        return response.text();
    }

    async summarizeChapter(overallSummary: string, chapterText: string): Promise<string> {
        const prompt = BOOK_CHAPTER_SUMMARY_PROMPT
            .replace('{overall_summary}', overallSummary)
            .replace('{chapter_text}', chapterText);
        const result = await this.model.generateContent(prompt);
        const response = await result.response;
        return response.text();
    }

    async structureChapter(overallSummary: string, chapterText: string): Promise<string> {
        const prompt = BOOK_STRUCTURING_PROMPT
            .replace('{overall_summary}', overallSummary)
            .replace('{chapter_text}', chapterText);
        const result = await this.model.generateContent(prompt);
        const response = await result.response;
        return response.text();
    }

    async translateChapter(overallSummary: string, chapterSummary: string, chapterText: string, glossary: string): Promise<string> {
        // Simple implementation: for web, we might need further chunking if chapters > 30k
        const prompt = BOOK_TRANSLATION_PROMPT
            .replace('{overall_summary}', overallSummary)
            .replace('{chapter_summary}', chapterSummary)
            .replace('{glossary_content}', glossary)
            .replace('{chunk_text}', chapterText);
        const result = await this.model.generateContent(prompt);
        const response = await result.response;
        return response.text();
    }
}
