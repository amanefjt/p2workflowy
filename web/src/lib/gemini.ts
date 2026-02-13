import { GoogleGenerativeAI, Part } from '@google/generative-ai';
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

    private async fileToGenerativePart(file: File): Promise<Part> {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onloadend = () => {
                const base64Data = reader.result as string;
                const base64Content = base64Data.split(',')[1];
                resolve({
                    inlineData: {
                        data: base64Content,
                        mimeType: file.type,
                    },
                });
            };
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
    }

    /**
     * Process images to structured Markdown.
     * This combines OCR and Structuring in one step using Gemini Vision.
     */
    async processImagesToMarkdown(files: File[]): Promise<string> {
        const imageParts = await Promise.all(files.map(f => this.fileToGenerativePart(f)));

        // We adjust the prompt slightly for Vision input
        const prompt = `
${STRUCTURING_PROMPT}

[Instruction for Vision]
The input is provided as images. Please extract the text from these images and apply the structuring rules defined above.
`;

        const result = await this.model.generateContent([prompt, ...imageParts]);
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
