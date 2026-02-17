import { get, set, del, keys } from 'idb-keyval';

// --- Dictionary Storage (IndexedDB) ---
export interface DictionaryFile {
    name: string;
    content: string; // CSV/TXT content
    updatedAt: number;
}

const DICT_STORE_PREFIX = 'p2w_dict_';

export const DictStorage = {
    async save(file: DictionaryFile): Promise<void> {
        await set(DICT_STORE_PREFIX + file.name, file);
    },

    async getAll(): Promise<DictionaryFile[]> {
        const allKeys = await keys();
        const dictKeys = allKeys.filter((k) => typeof k === 'string' && k.startsWith(DICT_STORE_PREFIX));
        const files: DictionaryFile[] = [];

        for (const key of dictKeys) {
            const file = await get<DictionaryFile>(key);
            if (file) files.push(file);
        }

        return files.sort((a, b) => b.updatedAt - a.updatedAt);
    },

    async delete(fileName: string): Promise<void> {
        await del(DICT_STORE_PREFIX + fileName);
    },

    // Helper to compile all dictionaries into one string for LLM
    async getCompiledGlossary(): Promise<string> {
        const files = await this.getAll();
        return files.map(f => f.content).join('\n');
    }
};

// --- Settings Storage (localStorage) ---
const STORAGE_KEY_API_KEY = 'p2w_gemini_api_key';

export const SettingsStorage = {
    getApiKey(): string {
        return localStorage.getItem(STORAGE_KEY_API_KEY) || '';
    },

    setApiKey(key: string): void {
        if (key) {
            localStorage.setItem(STORAGE_KEY_API_KEY, key);
        } else {
            localStorage.removeItem(STORAGE_KEY_API_KEY);
        }
    },

    hasApiKey(): boolean {
        return !!this.getApiKey();
    }
};
