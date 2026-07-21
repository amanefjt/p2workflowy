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
    const savedExpertise = localStorage.getItem('p2workflowy_expertise');
    if (savedExpertise) document.getElementById('expertise').value = savedExpertise;

    // Set sample glossary link
    const glossaryLink = document.getElementById('sample-glossary-link');
    if (glossaryLink) {
        glossaryLink.href = `${API_BASE}/api/glossary/sample`;
    }
});

// 論文 / 書籍 トグル
function switchBookMode(isBook) {
    document.getElementById('is_book').checked = isBook;
    document.getElementById('tab-paper').classList.toggle('active', !isBook);
    document.getElementById('tab-book').classList.toggle('active', isBook);
    document.getElementById('book-options').style.display = isBook ? 'block' : 'none';
}

form.addEventListener('submit', async (e) => {
    e.preventDefault();

    const formData = new FormData(form);
    const expertise = document.getElementById('expertise').value;
    const pdfFile = document.getElementById('pdf_file').files[0];

    if (!pdfFile) {
        alert("PDFファイルをアップロードしてください。");
        return;
    }

    // 設定の保存
    localStorage.setItem('p2workflowy_expertise', expertise);

    formData.set('expertise', expertise); // name属性で既に入っている場合があるためsetで上書き
    formData.set('export_mode', 'ronbunnihongo');

    // UI Update
    submitBtn.disabled = true;
    submitBtn.innerText = 'Processing...';
    statusContainer.classList.remove('hidden');
    downloadLinks.classList.add('hidden');
    statusText.classList.remove('status-busy-text');
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
            pollStatus(data.task_id, data.download_token || '');
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
    submitBtn.innerText = '翻訳を開始する';
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
            } else if (data.status === 'busy') {
                // プール枯渇: 障害ではなく想定内の混雑状態なので穏やかな表示にする
                clearInterval(interval);
                statusText.innerText = data.error || 'ただいま混み合っています。';
                statusText.classList.add('status-busy-text');
                resetButton();
            } else if (data.status === 'failed') {
                clearInterval(interval);
                statusText.innerText = data.error || 'エラーが発生しました';
                resetButton();
            }
        } catch (err) {
            console.error('Polling error:', err);
        }
    }, 2000);
}

function showDownloads(taskId, downloadToken) {
    const t = encodeURIComponent(downloadToken || '');
    document.getElementById('dl-ronbun').href = `${API_BASE}/api/download/${taskId}/ronbun?token=${t}`;
    downloadLinks.classList.remove('hidden');
    downloadLinks.scrollIntoView({ behavior: 'smooth' });
}

