import { useState, useEffect, useCallback } from 'react';
import { SettingsStorage, DictStorage, DictionaryFile } from '../lib/storage';

export const useAppSettings = () => {
    const [apiKey, setApiKeyState] = useState<string>('');
    const [dictionaries, setDictionaries] = useState<DictionaryFile[]>([]);
    const [loading, setLoading] = useState(true);

    // Load initial state
    useEffect(() => {
        const load = async () => {
            const storedKey = SettingsStorage.getApiKey();
            setApiKeyState(storedKey);

            const storedDicts = await DictStorage.getAll();
            setDictionaries(storedDicts);

            setLoading(false);
        };
        load();
    }, []);

    const setApiKey = useCallback((key: string) => {
        SettingsStorage.setApiKey(key);
        setApiKeyState(key);
    }, []);

    const addDictionary = useCallback(async (name: string, content: string) => {
        const newFile: DictionaryFile = {
            name,
            content,
            updatedAt: Date.now(),
        };
        await DictStorage.save(newFile);
        // Refresh list
        const updated = await DictStorage.getAll();
        setDictionaries(updated);
    }, []);

    const removeDictionary = useCallback(async (name: string) => {
        await DictStorage.delete(name);
        const updated = await DictStorage.getAll();
        setDictionaries(updated);
    }, []);

    return {
        apiKey,
        setApiKey,
        dictionaries,
        addDictionary,
        removeDictionary,
        loading
    };
};
