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
    submitBtn.innerText = '翻訳を開始する';
}

async function pollStatus(taskId) {
    const interval = setInterval(async () => {
        try {
            const response = await fetch(`/api/status/${taskId}`);
            if (!response.ok) return;

            const data = await response.json();

            statusText.innerText = data.progress || 'Processing...';

            if (data.status === 'processing') {
                if (data.progress.includes('Phase 1')) updateProgress(20);
                else if (data.progress.includes('Phase 2')) updateProgress(40);
                else if (data.progress.includes('Phase 3')) updateProgress(60);
                else if (data.progress.includes('Phase 4')) updateProgress(80);
            }

            if (data.status === 'completed') {
                clearInterval(interval);
                updateProgress(100);
                statusText.innerText = 'Completed';
                showDownloads(taskId);
                logViewer.innerHTML += '<br><strong style="color: #4ade80">Success: RonbunNihongo output generated.</strong>';
                logViewer.scrollTop = logViewer.scrollHeight;
                resetButton();
            } else if (data.status === 'failed') {
                clearInterval(interval);
                statusText.innerText = 'Failed';
                logViewer.innerHTML += `<br><span style="color: #ef4444">Error: ${data.error}</span>`;
                logViewer.scrollTop = logViewer.scrollHeight;
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
    document.getElementById('dl-ronbun').href = `/api/download/${taskId}/ronbun`;
    downloadLinks.classList.remove('hidden');
    downloadLinks.scrollIntoView({ behavior: 'smooth' });
}
