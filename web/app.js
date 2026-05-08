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
    const accentStyle = 'background: var(--accent, #6366f1); color: white; border-color: var(--accent, #6366f1);';
    const inactiveStyle = 'background: white; color: #64748b; border-color: #cbd5e1;';
    document.getElementById('tab-pdf').style.cssText = isPdf ? accentStyle : inactiveStyle;
    document.getElementById('tab-text').style.cssText = isPdf ? inactiveStyle : accentStyle;
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
    
    if (apiKey) {
        formData.set('api_key', apiKey);
    }
    formData.set('expertise', expertise); 
    formData.set('export_mode', 'p2workflowy');
    formData.set('is_book', isBook ? 'true' : 'false');

    // UI Update
    submitBtn.disabled = true;
    submitBtn.innerText = 'Processing...';
    statusContainer.classList.remove('hidden');
    downloadLinks.classList.add('hidden');
    logViewer.innerHTML = 'Submitting task...<br>';
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
            logViewer.innerHTML += `Task ID: ${data.task_id}<br>Pipeline started...<br>`;
            pollStatus(data.task_id);
        } else {
            throw new Error('Failed to start process: No task ID received');
        }
    } catch (err) {
        console.error("Form submission error:", err);
        statusText.innerText = 'リクエストに失敗しました';
        logViewer.innerHTML += `<span style="color:var(--text-danger)">Error: ${err.message}</span><br>`;
        alert(`Error communicating with server: ${err.message}`);
        resetButton();
    }
});

function resetButton() {
    submitBtn.disabled = false;
    submitBtn.innerText = '変換を開始する';
}

async function pollStatus(taskId) {
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
                showDownloads(taskId);
                resetButton();
            } else if (data.status === 'failed') {
                clearInterval(interval);
                const errDetail = data.error || data.progress || '詳細不明';
                statusText.innerText = `エラーが発生しました: ${errDetail}`;
                logViewer.innerHTML += `<span style="color:var(--text-danger, #ef4444)">処理失敗: ${errDetail}</span><br>`;
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

function showDownloads(taskId) {
    document.getElementById('dl-markdown').href = `${API_BASE}/api/download/${taskId}/markdown`;
    document.getElementById('dl-workflowy').href = `${API_BASE}/api/download/${taskId}/workflowy`;
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
