import React, { useState, useRef } from 'react';
import { useAppSettings } from './hooks/useAppSettings';
import { GeminiService } from './lib/gemini';
import { DEFAULT_MODEL, MAX_TRANSLATION_CHUNK_SIZE } from './lib/constants';
import { markdownToWorkflowy, splitMarkdownByHeaders } from './lib/formatter';
import { Book, FileText, Settings, Upload, Check, Copy, Loader2, AlertCircle, Trash2, X } from 'lucide-react';

function App() {
    const { apiKey, setApiKey, dictionaries, addDictionary, removeDictionary, loading: settingsLoading } = useAppSettings();

    const [processing, setProcessing] = useState(false);
    const [result, setResult] = useState<string>('');
    const [error, setError] = useState<string>('');
    const [progress, setProgress] = useState<string>('');
    const [fileName, setFileName] = useState<string>('');
    const [inputMode, setInputMode] = useState<'file' | 'text'>('file');
    const [directText, setDirectText] = useState<string>('');
    const [docType, setDocType] = useState<'paper' | 'book'>('paper');

    const fileInputRef = useRef<HTMLInputElement>(null);

    const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;

        const text = await file.text();
        startProcessing(text, file.name);
    };

    const handleProcess = () => {
        if (!directText.trim()) {
            setError('テキストを入力してください。');
            return;
        }
        startProcessing(directText, 'Direct Input');
    };

    const startProcessing = async (text: string, currentFileName: string) => {
        if (!apiKey) {
            setError('Gemini APIキーを設定してください。');
            return;
        }

        setFileName(currentFileName);
        setProcessing(true);
        setError('');
        setResult('');
        setProgress('初期化中...');

        try {
            const gemini = new GeminiService(apiKey);
            const glossaryContent = dictionaries.map(d => d.content).join('\n');

            let finalMarkdown = '';
            let rawMarkdown = '';
            let summaryContext = '';

            if (docType === 'paper') {
                // --- Paper Mode ---
                setProgress(`AIが文書構造を解析中... (${DEFAULT_MODEL})`);
                rawMarkdown = await gemini.structureText(text);

                setProgress('全体要約を作成中...');
                summaryContext = await gemini.generateSummary(rawMarkdown);

                const sections = splitMarkdownByHeaders(rawMarkdown, MAX_TRANSLATION_CHUNK_SIZE);
                setProgress(`${sections.length}セクションを並列翻訳・整形中...`);

                const translationPromises = sections.map(async (section, idx) => {
                    try {
                        return await gemini.translateSection(section, summaryContext, glossaryContent);
                    } catch (err) {
                        console.error(`Error translating section ${idx}:`, err);
                        return section + "\n\n(翻訳エラー)";
                    }
                });

                const translatedResults = await Promise.all(translationPromises);
                finalMarkdown = translatedResults.join('\n\n');
            } else {
                // --- Book Mode ---
                setProgress('Phase 1 (書籍): 全体の構成と要約を分析中...');
                const structureInfo = await gemini.analyzeBookStructure(text);
                summaryContext = structureInfo;

                // Split text by headers (Book usually has clear chapter headers)
                const chapters = splitMarkdownByHeaders(text, MAX_TRANSLATION_CHUNK_SIZE);

                const translatedChapters = [];
                const cleanChaptersEng = [];
                const chapterSummaries = [];

                for (let i = 0; i < chapters.length; i++) {
                    const idx = i + 1;
                    setProgress(`Phase 2 (書籍): 章 ${idx}/${chapters.length} 処理中 (要約・構造化)...`);
                    const chSummary = await gemini.summarizeChapter(structureInfo, chapters[i]);
                    const cleanCh = await gemini.structureChapter(structureInfo, chapters[i]);

                    setProgress(`Phase 3 (書籍): 章 ${idx}/${chapters.length} 翻訳中...`);
                    const transCh = await gemini.translateChapter(structureInfo, chSummary, cleanCh, glossaryContent);

                    cleanChaptersEng.push(cleanCh);
                    translatedChapters.push(transCh);
                    chapterSummaries.push(chSummary);
                }

                rawMarkdown = cleanChaptersEng.join('\n\n');
                finalMarkdown = translatedChapters.join('\n\n');
                summaryContext = `# Book Summary\n${structureInfo}\n\n# Chapters Summary\n${chapterSummaries.join('\n\n')}`;
            }

            // --- Formatting ---
            setProgress('Workflowy形式に変換中...');

            let title = currentFileName;
            const engLines = rawMarkdown.split('\n');
            if (engLines.length > 0 && engLines[0].startsWith('# ')) {
                title = engLines[0].replace('# ', '').trim();
            }

            const lines = finalMarkdown.split('\n');
            let bodyText = finalMarkdown;
            if (lines.length > 0 && lines[0].startsWith('# ')) {
                bodyText = lines.slice(1).join('\n').trim();
            }

            const workflowyBody = markdownToWorkflowy(bodyText);
            const nestedBody = workflowyBody.split('\n').map(line => '    ' + line).join('\n');

            const summaryWorkflowy = markdownToWorkflowy(summaryContext);
            const nestedSummary = summaryWorkflowy.split('\n').map(line => '        ' + line).join('\n');
            const summarySection = `    - 要約 (Summary)\n${nestedSummary}`;

            const finalResult = `- ${title}\n${summarySection}\n${nestedBody}`;

            setResult(finalResult);
            setProgress('');
        } catch (err: any) {
            setError(`エラーが発生しました: ${err.message || '不明なエラー'}`);
        } finally {
            setProcessing(false);
            if (fileInputRef.current) fileInputRef.current.value = '';
        }
    };

    const copyToClipboard = () => {
        navigator.clipboard.writeText(result);
    };

    const handleDictUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;

        const text = await file.text();
        await addDictionary(file.name, text);
        if (e.target.value) e.target.value = '';
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

    const clearResult = () => {
        setResult('');
        setFileName('');
        setError('');
    };

    if (settingsLoading) return <div className="flex items-center justify-center h-screen"><Loader2 className="animate-spin text-indigo-600" /></div>;

    return (
        <div className="min-h-screen bg-gray-50 text-gray-800 font-sans">
            <header className="bg-white border-b border-gray-200 py-4 px-6 sticky top-0 z-10 shadow-sm">
                <div className="max-w-4xl mx-auto flex items-center justify-between">
                    <h1 className="text-xl font-bold flex items-center gap-2 text-indigo-600">
                        <FileText className="w-6 h-6" />
                        p2workflowy
                    </h1>
                    <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noreferrer" className="text-xs text-indigo-600 hover:underline">
                        Get API Key
                    </a>
                </div>
            </header>

            <main className="max-w-4xl mx-auto py-8 px-4 sm:px-6 space-y-8">
                {/* Introduction Section */}
                <section className="bg-indigo-50/50 p-6 rounded-xl border border-indigo-100 shadow-sm">
                    <h2 className="text-lg font-bold text-indigo-900 mb-3 flex items-center gap-2">
                        <FileText className="w-5 h-5" />
                        p2workflowy
                    </h2>
                    <p className="text-sm text-indigo-800 leading-relaxed mb-4">
                        p2workflowyは、長い英語の論文や書籍をGemini AIで解析し、意味のある構造に整理（構造化）した上で、日本語に翻訳して<strong>Workflowy形式</strong>で出力するツールです。
                        そのままWorkflowyに貼り付けるだけで、階層構造を保ったまま論文を読み進めることができます。
                    </p>

                    <div className="space-y-3 bg-white/60 p-4 rounded-lg border border-indigo-100">
                        <div className="flex gap-2">
                            <span className="text-lg">🔑</span>
                            <div className="text-sm">
                                <p className="font-bold text-gray-800 mb-1">Gemini APIキーについて</p>
                                <p className="text-gray-600">
                                    このツールは <a href="https://aistudio.google.com/" target="_blank" rel="noreferrer" className="text-indigo-600 underline">Google AI Studio</a> で取得できる APIキーを使用して動作します。
                                    無料枠で利用可能ですが、Google側の仕様により、支払い手段（クレジットカード等）の登録が必要な場合があります。
                                    入力したキーはブラウザのローカルストレージにのみ保存され、開発者を含む外部に送信されることはありません。
                                </p>
                            </div>
                        </div>
                        <div className="flex gap-2 border-t border-indigo-50 pt-3">
                            <span className="text-lg">⚠️</span>
                            <div className="text-sm">
                                <p className="font-bold text-gray-800 mb-1">モデル改善への利用について</p>
                                <p className="text-gray-600">
                                    無料枠の APIキーを使用する場合、入力したデータや翻訳結果は Google のモデル改善のために学習に利用される可能性があります。
                                    機密性の高い文書や未発表の研究資料などを扱う際は、十分にご注意ください。
                                </p>
                            </div>
                        </div>
                    </div>
                </section>

                {/* API Settings */}
                <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                    <h2 className="text-base font-bold mb-4 flex items-center gap-2 text-gray-700">
                        <Settings className="w-4 h-4" />
                        API設定
                    </h2>
                    <div className="flex flex-col gap-4">
                        <div className="flex flex-col gap-2">
                            <label className="text-xs font-medium text-gray-500">APIキー</label>
                            <input
                                type="password"
                                placeholder="AIza... から始まるGoogle Gemini APIキーを入力"
                                className="w-full border border-gray-300 rounded-md px-4 py-2 focus:ring-2 focus:ring-indigo-500 outline-none transition-all text-sm"
                                value={apiKey}
                                onChange={(e) => setApiKey(e.target.value)}
                            />
                            <p className="text-xs text-gray-400">
                                キーはブラウザに保存され、サーバーには送信されません。
                            </p>
                        </div>
                    </div>
                </section>

                {/* Dictionary Settings */}
                <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                    <div className="flex items-center justify-between mb-4">
                        <h2 className="text-base font-bold flex items-center gap-2 text-gray-700">
                            <Book className="w-4 h-4" />
                            辞書設定 (任意)
                        </h2>
                        <button onClick={handleDownloadSample} className="text-xs text-indigo-600 hover:underline">
                            サンプルCSV
                        </button>
                    </div>

                    <div className="space-y-3">
                        <div className="relative border border-dashed border-gray-300 rounded-lg p-4 text-center hover:bg-gray-50 transition-all cursor-pointer group">
                            <div className="flex items-center justify-center gap-2 text-gray-500 group-hover:text-indigo-600">
                                <Upload className="w-4 h-4" />
                                <span className="text-sm">辞書ファイルを追加 (.csv, .txt)</span>
                            </div>
                            <input type="file" className="absolute inset-0 opacity-0 cursor-pointer" accept=".csv,.txt,.json" onChange={handleDictUpload} />
                        </div>

                        {dictionaries.length > 0 && (
                            <ul className="flex flex-wrap gap-2">
                                {dictionaries.map(d => (
                                    <li key={d.name} className="flex items-center gap-2 bg-gray-100 px-3 py-1 rounded-full text-xs text-gray-700 border border-gray-200">
                                        <span>{d.name}</span>
                                        <button onClick={() => removeDictionary(d.name)} className="text-gray-400 hover:text-red-500">
                                            <X className="w-3 h-3" />
                                        </button>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>
                </section>

                {/* Main Processor */}
                <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                    <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 mb-4">
                        <h2 className="text-base font-bold flex items-center gap-2 text-gray-700">
                            <FileText className="w-4 h-4" />
                            テキスト処理
                        </h2>

                        <div className="flex flex-wrap gap-2">
                            {/* Input Mode Toggle */}
                            <div className="flex bg-gray-100 p-1 rounded-lg">
                                <button
                                    onClick={() => { setInputMode('file'); setError(''); }}
                                    className={`px-3 py-1 text-xs font-medium rounded-md transition-all ${inputMode === 'file' ? 'bg-white text-indigo-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                                >
                                    ファイル
                                </button>
                                <button
                                    onClick={() => { setInputMode('text'); setError(''); }}
                                    className={`px-3 py-1 text-xs font-medium rounded-md transition-all ${inputMode === 'text' ? 'bg-white text-indigo-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                                >
                                    直接入力
                                </button>
                            </div>

                            {/* Processing Mode Selection */}
                            <div className="flex bg-indigo-50 p-1 rounded-lg border border-indigo-100">
                                <button
                                    onClick={() => setDocType('paper')}
                                    className={`flex items-center gap-1.5 px-3 py-1 text-xs font-medium rounded-md transition-all ${docType === 'paper' ? 'bg-white text-indigo-600 shadow-sm' : 'text-indigo-400 hover:text-indigo-600'}`}
                                >
                                    <FileText className="w-3 h-3" />
                                    論文モード
                                </button>
                                <button
                                    onClick={() => setDocType('book')}
                                    className={`flex items-center gap-1.5 px-3 py-1 text-xs font-medium rounded-md transition-all ${docType === 'book' ? 'bg-white text-indigo-600 shadow-sm' : 'text-indigo-400 hover:text-indigo-600'}`}
                                >
                                    <Book className="w-3 h-3" />
                                    書籍モード
                                </button>
                            </div>
                            {docType === 'book' && (
                                <p className="text-[10px] text-amber-600 font-medium animate-pulse">
                                    ※書籍モードは現在やや不安定です
                                </p>
                            )}
                        </div>
                    </div>

                    {!processing && !result && (
                        <div className="space-y-4">
                            {inputMode === 'file' ? (
                                <div className="relative border-2 border-dashed border-indigo-100 bg-indigo-50/50 rounded-lg p-10 text-center hover:bg-indigo-50 hover:border-indigo-300 transition-all cursor-pointer group">
                                    <div className="flex flex-col items-center gap-3">
                                        <div className="bg-indigo-100 p-3 rounded-full group-hover:scale-110 transition-transform">
                                            <Upload className="w-6 h-6 text-indigo-600" />
                                        </div>
                                        <div>
                                            <h3 className="text-base font-bold text-gray-900">テキストファイルをアップロード</h3>
                                            <p className="text-xs text-gray-500 mt-1">.txt ファイルをここにドロップ</p>
                                        </div>
                                    </div>
                                    <input
                                        ref={fileInputRef}
                                        type="file"
                                        className="absolute inset-0 opacity-0 cursor-pointer"
                                        accept=".txt"
                                        onChange={handleFileUpload}
                                    />
                                </div>
                            ) : (
                                <div className="space-y-4">
                                    <textarea
                                        className="w-full h-64 p-4 bg-gray-50 rounded-lg border border-gray-200 font-mono text-sm focus:ring-2 focus:ring-indigo-500 outline-none resize-y"
                                        placeholder="ここに論文のテキストを貼り付けてください..."
                                        value={directText}
                                        onChange={(e) => setDirectText(e.target.value)}
                                    />
                                    <button
                                        onClick={handleProcess}
                                        className="w-full bg-indigo-600 text-white py-3 rounded-lg font-bold hover:bg-indigo-700 transition-colors shadow-md flex items-center justify-center gap-2"
                                    >
                                        <Check className="w-5 h-5" />
                                        実行する
                                    </button>
                                </div>
                            )}
                        </div>
                    )}

                    {processing && (
                        <div className="py-12 text-center flex flex-col items-center gap-4">
                            <Loader2 className="w-8 h-8 text-indigo-600 animate-spin" />
                            <p className="text-sm text-gray-600 font-medium animate-pulse">{progress}</p>
                        </div>
                    )}

                    {error && (
                        <div className="mt-4 p-4 bg-red-50 text-red-600 rounded-lg flex items-center gap-2 text-sm border border-red-100">
                            <AlertCircle className="w-4 h-4" />
                            {error}
                        </div>
                    )}
                </section>

                {/* Result Area */}
                {result && (
                    <section className="bg-white p-6 rounded-xl shadow-lg border border-indigo-100 scroll-mt-20" id="result">
                        <div className="flex items-center justify-between mb-4">
                            <div>
                                <h2 className="text-base font-bold flex items-center gap-2 text-gray-800">
                                    <Check className="w-4 h-4 text-green-500" />
                                    処理結果
                                </h2>
                                {fileName && <p className="text-xs text-gray-400 mt-1">{fileName}</p>}
                            </div>
                            <div className="flex gap-2">
                                <button
                                    onClick={clearResult}
                                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-500 hover:text-red-600 hover:bg-red-50 rounded-md transition-colors"
                                >
                                    <Trash2 className="w-3.5 h-3.5" />
                                    クリア
                                </button>
                                <button
                                    onClick={copyToClipboard}
                                    className="flex items-center gap-1.5 bg-indigo-600 text-white px-4 py-1.5 rounded-md hover:bg-indigo-700 transition-colors shadow-sm text-xs font-bold"
                                >
                                    <Copy className="w-3.5 h-3.5" />
                                    コピー
                                </button>
                            </div>
                        </div>

                        <textarea
                            className="w-full h-96 p-4 bg-gray-50 rounded-lg border border-gray-200 font-mono text-sm focus:ring-2 focus:ring-indigo-500 outline-none resize-y"
                            value={result}
                            readOnly
                        />
                    </section>
                )}
            </main>

            <footer className="max-w-4xl mx-auto py-6 text-center text-xs text-gray-400">
                p2workflowy Web - Powered by Google Gemini
            </footer>
        </div>
    );
}

export default App;
