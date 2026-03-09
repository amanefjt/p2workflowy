const form = document.getElementById('process-form');
const statusContainer = document.getElementById('status-container');
const logViewer = document.getElementById('log-viewer');
const progressFill = document.getElementById('progress-fill');
const statusText = document.getElementById('status-text');
const percentText = document.getElementById('percent-text');
const downloadLinks = document.getElementById('download-links');
const submitBtn = document.getElementById('submit-btn');

// Load saved values from localStorage
document.addEventListener('DOMContentLoaded', () => {
    const savedApiKey = localStorage.getItem('p2w_api_key');
    const savedExpertise = localStorage.getItem('p2w_expertise');
    if (savedApiKey) document.getElementById('api_key').value = savedApiKey;
    if (savedExpertise) document.getElementById('expertise').value = savedExpertise;
});

form.addEventListener('submit', async (e) => {
    e.preventDefault();

    const formData = new FormData(form);
    const apiKey = document.getElementById('api_key').value;
    const expertise = document.getElementById('expertise').value || '文化人類学';

    // Save to localStorage
    if (apiKey) localStorage.setItem('p2w_api_key', apiKey);
    localStorage.setItem('p2w_expertise', expertise);

    if (apiKey) {
        formData.set('api_key', apiKey);
    }
    formData.append('expertise', expertise);

    // UI Update
    submitBtn.disabled = true;
    submitBtn.innerText = 'Processing...';
    statusContainer.classList.remove('hidden');
    downloadLinks.classList.add('hidden');
    logViewer.innerHTML = 'Submitting task...<br>';
    progressFill.style.width = '5%';
    percentText.innerText = '5%';

    try {
        const response = await fetch('/api/process', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();
        if (data.task_id) {
            logViewer.innerHTML += `Task ID: ${data.task_id}<br>Pipeline started...<br>`;
            pollStatus(data.task_id);
        } else {
            alert('Failed to start process');
            resetButton();
        }
    } catch (err) {
        console.error(err);
        alert('Error communicating with server');
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
            const response = await fetch(`/api/status/${taskId}`);
            if (!response.ok) return;

            const data = await response.json();

            statusText.innerText = getFriendlyStatus(data.progress);

            if (data.status === 'processing') {
                const progress = calculateProgress(data.progress);
                updateProgress(progress);
            }

            if (data.status === 'completed') {
                clearInterval(interval);
                updateProgress(100);
                statusText.innerText = '完了！';
                showDownloads(taskId);
                resetButton();
            } else if (data.status === 'failed') {
                clearInterval(interval);
                statusText.innerText = 'エラーが発生しました';
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
    document.getElementById('dl-markdown').href = `/api/download/${taskId}/markdown`;
    document.getElementById('dl-workflowy').href = `/api/download/${taskId}/workflowy`;
    downloadLinks.classList.remove('hidden');
    // Scroll to download section
    downloadLinks.scrollIntoView({ behavior: 'smooth' });
}

function getFriendlyStatus(progressMsg) {
    if (!progressMsg) return '準備中...';
    if (progressMsg.includes('Phase 1')) return 'テキスト分析中...';
    if (progressMsg.includes('Phase 2') || progressMsg.includes('Phase 3')) return '構造解析中...';
    if (progressMsg.includes('Phase 4')) return '翻訳中...';
    if (progressMsg.includes('Phase 5')) return '仕上げ中...';
    return '処理中...';
}

function calculateProgress(progressMsg) {
    if (!progressMsg) return 5;
    if (progressMsg.includes('Phase 1')) return 25;
    if (progressMsg.includes('Phase 2') || progressMsg.includes('Phase 3')) return 50;
    if (progressMsg.includes('Phase 4')) return 75;
    if (progressMsg.includes('Phase 5')) return 90;
    return 10;
}
