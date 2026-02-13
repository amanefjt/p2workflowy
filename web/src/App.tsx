import React, { useState, useRef } from 'react';
import { useAppSettings } from './hooks/useAppSettings';
import { GeminiService } from './lib/gemini';
import { markdownToWorkflowy, splitMarkdownByHeaders } from './lib/formatter';
import { Book, FileText, Settings, Upload, Check, Copy, Loader2, AlertCircle } from 'lucide-react';

function App() {
    const { apiKey, setApiKey, dictionaries, addDictionary, removeDictionary, loading: settingsLoading } = useAppSettings();

    const [processing, setProcessing] = useState(false);
    const [result, setResult] = useState<string>('');
    const [error, setError] = useState<string>('');
    const [progress, setProgress] = useState<string>('');

    const fileInputRef = useRef<HTMLInputElement>(null);

    const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const files = e.target.files;
        if (!files || files.length === 0) return;

        if (!apiKey) {
            setError('Please enter your Gemini API Key first.');
            return;
        }

        setProcessing(true);
        setError('');
        setResult('');
        setProgress('Initializing...');

        try {
            const gemini = new GeminiService(apiKey);
            const fileArray = Array.from(files);

            // 1. OCR & Structuring (Image -> Markdown)
            setProgress('Processing images with Gemini Vision... (This may take a while)');
            const rawMarkdown = await gemini.processImagesToMarkdown(fileArray);

            // 2. Translation (Markdown -> Translated Markdown)
            let finalMarkdown = rawMarkdown;

            // Split into sections for translation/refinement
            const sections = splitMarkdownByHeaders(rawMarkdown);

            // Compile glossary
            const glossaryContent = dictionaries.map(d => d.content).join('\n');

            setProgress(`Translating ${sections.length} sections...`);

            const translationPromises = sections.map(async (section, idx) => {
                try {
                    // If no dictionary and English text, maybe we skip translation?
                    // But requirement implies reproducing "p2workflowy" which translates.
                    return await gemini.translateSection(section, "", glossaryContent);
                } catch (err) {
                    console.error(`Error translating section ${idx}:`, err);
                    return section + "\n\n(Translation Failed)";
                }
            });

            const translatedResults = await Promise.all(translationPromises);
            finalMarkdown = translatedResults.join('\n\n');

            // 3. Formatting (Markdown -> Workflowy)
            setProgress('Formatting...');
            const workflowyText = markdownToWorkflowy(finalMarkdown);

            setResult(workflowyText);
            setProgress('');
        } catch (err: any) {
            setError(`Error: ${err.message || 'Unknown error occurred'}`);
        } finally {
            setProcessing(false);
            if (fileInputRef.current) fileInputRef.current.value = ''; // Reset input
        }
    };

    const copyToClipboard = () => {
        navigator.clipboard.writeText(result);
        // You might want a toast here
        alert('Copied to clipboard!');
    };

    const handleDictUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;

        const text = await file.text();
        await addDictionary(file.name, text);
        if (e.target.value) e.target.value = ''; // reset
    };

    const handleDownloadSample = () => {
        const sample = "Term,Translation\nLLM,大規模言語モデル\nAgentic,エージェンティック\n";
        const blob = new Blob([sample], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'glossary_sample.csv';
        a.click();
        URL.revokeObjectURL(url);
    };

    if (settingsLoading) return <div className="flex items-center justify-center h-screen"><Loader2 className="animate-spin text-indigo-600" /></div>;

    return (
        <div className="min-h-screen bg-gray-50 text-gray-800 font-sans">
            <header className="bg-white border-b border-gray-200 py-4 px-6 sticky top-0 z-10 shadow-sm">
                <div className="max-w-4xl mx-auto flex items-center justify-between">
                    <h1 className="text-xl font-bold flex items-center gap-2 text-indigo-600">
                        <FileText className="w-6 h-6" />
                        p2workflowy Web
                    </h1>
                    <div className="text-xs text-gray-400 font-mono">Ver. Alpha</div>
                </div>
            </header>

            <main className="max-w-4xl mx-auto py-8 px-6 space-y-8">
                {/* Section 1: API Key */}
                <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 transition-all hover:shadow-md">
                    <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                        <Settings className="w-5 h-5 text-gray-400" />
                        API Settings
                    </h2>
                    <div className="flex flex-col gap-2">
                        <label className="text-sm text-gray-600 font-medium">Google Gemini API Key</label>
                        <input
                            type="password"
                            placeholder="Enter your API Key (starts with AIza...)"
                            className="w-full border border-gray-300 rounded-lg px-4 py-2 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                            value={apiKey}
                            onChange={(e) => setApiKey(e.target.value)}
                        />
                        <p className="text-xs text-gray-400 mt-1">
                            Key is stored locally in your browser. We never see it.
                        </p>
                    </div>
                </section>

                {/* Section 2: Dictionaries */}
                <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 transition-all hover:shadow-md">
                    <div className="flex items-center justify-between mb-4">
                        <h2 className="text-lg font-semibold flex items-center gap-2">
                            <Book className="w-5 h-5 text-gray-400" />
                            Custom Dictionaries
                        </h2>
                        <button onClick={handleDownloadSample} className="text-xs text-indigo-600 hover:underline">
                            Download Sample CSV
                        </button>
                    </div>

                    <div className="space-y-4">
                        <div className="relative border-2 border-dashed border-gray-200 rounded-lg p-6 text-center hover:border-indigo-400 hover:bg-gray-50 transition-all cursor-pointer group">
                            <Upload className="w-8 h-8 text-gray-300 mx-auto mb-2 group-hover:text-indigo-400 transition-colors" />
                            <p className="text-sm text-gray-500">Click to upload dictionary (CSV/TXT)</p>
                            <input type="file" className="absolute inset-0 opacity-0 cursor-pointer" accept=".csv,.txt,.json" onChange={handleDictUpload} />
                        </div>

                        {dictionaries.length > 0 && (
                            <ul className="space-y-2 bg-gray-50 p-4 rounded-lg">
                                {dictionaries.map(d => (
                                    <li key={d.name} className="flex items-center justify-between bg-white px-3 py-2 rounded-md text-sm border border-gray-100 shadow-sm">
                                        <span className="font-medium text-gray-700">{d.name}</span>
                                        <button onClick={() => removeDictionary(d.name)} className="text-red-400 hover:text-red-600 font-medium text-xs px-2 py-1 rounded hover:bg-red-50 transition-colors">Delete</button>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>
                </section>

                {/* Section 3: Image Upload & Process */}
                <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 transition-all hover:shadow-md">
                    <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                        <Upload className="w-5 h-5 text-gray-400" />
                        Process Paper
                    </h2>

                    {!processing && !result && (
                        <div className="relative border-2 border-dashed border-indigo-100 bg-indigo-50/30 rounded-lg p-12 text-center hover:bg-indigo-50 hover:border-indigo-300 transition-all cursor-pointer group">
                            <div className="flex flex-col items-center gap-3">
                                <FileText className="w-12 h-12 text-indigo-300 group-hover:text-indigo-500 transition-colors" />
                                <h3 className="text-lg font-medium text-indigo-900">Drop paper images here</h3>
                                <p className="text-sm text-indigo-600/70">Support: JPG, PNG, WEBP</p>
                            </div>
                            <input
                                ref={fileInputRef}
                                type="file"
                                className="absolute inset-0 opacity-0 cursor-pointer"
                                accept="image/*"
                                multiple
                                onChange={handleFileUpload}
                            />
                        </div>
                    )}

                    {processing && (
                        <div className="py-12 text-center flex flex-col items-center gap-4">
                            <Loader2 className="w-10 h-10 text-indigo-600 animate-spin" />
                            <p className="text-gray-600 font-medium animate-pulse">{progress}</p>
                        </div>
                    )}

                    {error && (
                        <div className="mt-4 p-4 bg-red-50 text-red-600 rounded-lg flex items-center gap-2 text-sm border border-red-100">
                            <AlertCircle className="w-5 h-5" />
                            {error}
                        </div>
                    )}
                </section>

                {/* Section 4: Result */}
                {result && (
                    <section className="bg-white p-6 rounded-xl shadow-lg border border-indigo-100">
                        <div className="flex items-center justify-between mb-4">
                            <h2 className="text-lg font-semibold flex items-center gap-2 text-indigo-900">
                                <Check className="w-5 h-5 text-green-500" />
                                Result
                            </h2>
                            <button
                                onClick={copyToClipboard}
                                className="flex items-center gap-2 bg-indigo-600 text-white px-4 py-2 rounded-lg hover:bg-indigo-700 transition-colors shadow-md hover:shadow-lg active:scale-95"
                            >
                                <Copy className="w-4 h-4" />
                                Copy to Clipboard
                            </button>
                        </div>

                        <div className="bg-gray-50 p-4 rounded-lg border border-gray-200 font-mono text-sm h-96 overflow-y-auto whitespace-pre-wrap">
                            {result}
                        </div>

                        <div className="mt-4 text-center">
                            <button
                                onClick={() => { setResult(''); setProcessing(false); }}
                                className="text-gray-400 hover:text-gray-600 text-sm hover:underline"
                            >
                                Process another file
                            </button>
                        </div>
                    </section>
                )}
            </main>
        </div>
    );
}

export default App;
