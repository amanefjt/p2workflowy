import { GoogleGenerativeAI } from '@google/generative-ai';
import {
    DEFAULT_MODEL,
    STRUCTURING_PROMPT,
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
}
