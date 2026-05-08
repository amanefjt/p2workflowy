const form = document.getElementById('process-form');
const statusContainer = document.getElementById('status-container');
const logViewer = document.getElementById('log-viewer');
const progressFill = document.getElementById('progress-fill');
const statusText = document.getElementById('status-text');
const percentText = document.getElementById('percent-text');
const downloadLinks = document.getElementById('download-links');
const submitBtn = document.getElementById('submit-btn');

// APIのベースURL設定: Cloudflare Pages 上では外部のバックエンドURLを指定
const API_BASE = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? ''
    : 'https://amanefjt-p2workflowy.hf.space';

// Load saved values from localStorage
document.addEventListener('DOMContentLoaded', () => {
    const savedApiKey = localStorage.getItem('p2workflowy_api_key');
    const savedExpertise = localStorage.getItem('p2workflowy_expertise');
    if (savedApiKey) document.getElementById('api_key').value = savedApiKey;
    if (savedExpertise) document.getElementById('expertise').value = savedExpertise;

    // Set sample glossary link
    const glossaryLink = document.getElementById('sample-glossary-link');
    if (glossaryLink) {
        glossaryLink.href = `${API_BASE}/api/glossary/sample`;
    }
});

// タブ切り替え
function switchInputTab(tab) {
    const isPdf = tab === 'pdf';
    document.getElementById('input-pdf').style.display = isPdf ? '' : 'none';
    document.getElementById('input-text').style.display = isPdf ? 'none' : '';
    const tabPdf  = document.getElementById('tab-pdf');
    const tabText = document.getElementById('tab-text');
    tabPdf.style.background    = isPdf ? 'var(--accent, #6366f1)' : 'white';
    tabPdf.style.color         = isPdf ? 'white' : '#64748b';
    tabPdf.style.borderColor   = isPdf ? 'var(--accent, #6366f1)' : '#cbd5e1';
    tabText.style.background   = isPdf ? 'white' : 'var(--accent, #6366f1)';
    tabText.style.color        = isPdf ? '#64748b' : 'white';
    tabText.style.borderColor  = isPdf ? '#cbd5e1' : 'var(--accent, #6366f1)';
}

form.addEventListener('submit', async (e) => {
    e.preventDefault();

    const formData = new FormData(form);
    const apiKey = document.getElementById('api_key').value;
    const expertise = document.getElementById('expertise').value;
    const pdfFile = document.getElementById('pdf_file').files[0];
    const textInput = document.getElementById('text_input').value.trim();
    const isTextMode = document.getElementById('input-text').style.display !== 'none';

    if (isTextMode) {
        if (!textInput) {
            alert("テキストを入力してください。");
            return;
        }
        // PDF フィールドを除去してテキストのみ送信
        formData.delete('pdf_file');
        formData.set('text', textInput);
    } else {
        if (!pdfFile) {
            alert("PDFファイルをアップロードしてください。");
            return;
        }
        formData.delete('text');
    }

    // 設定の保存（空入力でも上書き保存を許可）
    localStorage.setItem('p2workflowy_api_key', apiKey);
    localStorage.setItem('p2workflowy_expertise', expertise);

    const isBook = document.getElementById('is_book').checked;
    const maxChaptersEl = document.getElementById('max_chapters');
    const maxChapters = maxChaptersEl && maxChaptersEl.value ? parseInt(maxChaptersEl.value) : null;

    if (apiKey) {
        formData.set('api_key', apiKey);
    }
    formData.set('expertise', expertise);
    formData.set('export_mode', 'p2workflowy');
    formData.set('is_book', isBook ? 'true' : 'false');
    if (isBook && maxChapters) {
        formData.set('max_chapters', maxChapters);
    }

    // UI Update
    submitBtn.disabled = true;
    submitBtn.innerText = '処理中...';
    statusContainer.classList.remove('hidden');
    downloadLinks.classList.add('hidden');
    logViewer.innerHTML = '処理を開始中...<br>';
    progressFill.style.width = '5%';
    percentText.innerText = '5%';

    try {
        console.log("Submitting form data:", Object.fromEntries(formData.entries()));
        const response = await fetch(`${API_BASE}/api/process`, {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`Server returned ${response.status}: ${errorText}`);
        }

        const data = await response.json();
        console.log("Server response:", data);
        if (data.task_id) {
            logViewer.innerHTML += `パイプラインを起動しました...<br>`;
            pollStatus(data.task_id, data.download_token || '');
        } else {
            throw new Error('タスクIDが取得できませんでした');
        }
    } catch (err) {
        console.error("Form submission error:", err);
        statusText.innerText = 'リクエストに失敗しました';
        logViewer.innerHTML += `<span style="color:var(--text-danger)">エラー: ${err.message}</span><br>`;
        alert(`サーバーとの通信に失敗しました: ${err.message}`);
        resetButton();
    }
});

function resetButton() {
    submitBtn.disabled = false;
    submitBtn.innerText = '変換を開始する';
}

async function pollStatus(taskId, downloadToken) {
    const interval = setInterval(async () => {
        try {
            const response = await fetch(`${API_BASE}/api/status/${taskId}`);
            if (!response.ok) return;

            const data = await response.json();

            statusText.innerText = getFriendlyStatus(data.progress);

            if (data.status === 'processing') {
                updateProgress(data.percentage || 0);
            }

            if (data.status === 'completed') {
                clearInterval(interval);
                updateProgress(100);
                statusText.innerText = '完了！';
                showDownloads(taskId, downloadToken);
                resetButton();
            } else if (data.status === 'failed') {
                clearInterval(interval);
                const errDetail = data.error || data.progress || '詳細不明';
                statusText.innerText = `エラーが発生しました: ${errDetail}`;
                const safeErr = errDetail.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
                logViewer.innerHTML += `<span style="color:var(--text-danger, #ef4444)">処理失敗: ${safeErr}</span><br>`;
                resetButton();
            }
        } catch (err) {
            console.error('Polling error:', err);
        }
    }, 2000);
}

function updateProgress(percent) {
    progressFill.style.width = `${percent}%`;
    percentText.innerText = `${percent}%`;
}

function showDownloads(taskId, downloadToken) {
    const t = encodeURIComponent(downloadToken);
    document.getElementById('dl-markdown').href = `${API_BASE}/api/download/${taskId}/markdown?token=${t}`;
    document.getElementById('dl-workflowy').href = `${API_BASE}/api/download/${taskId}/workflowy?token=${t}`;
    downloadLinks.classList.remove('hidden');
    // Scroll to download section
    downloadLinks.scrollIntoView({ behavior: 'smooth' });
}

function getFriendlyStatus(progressMsg) {
    if (!progressMsg) return '準備中...';
    return progressMsg;
}

function calculateProgress(progressMsg) {
    if (!progressMsg) return 5;
    if (progressMsg.includes('準備')) return 20;
    if (progressMsg.includes('解析') || progressMsg.includes('構築')) return 50;
    if (progressMsg.includes('翻訳')) return 75;
    if (progressMsg.includes('ファイル')) return 95;
    return 10;
}
