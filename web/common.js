// app.js / app_ronbun.js 共通のポーリング・ダウンロード・UI ヘルパー。
// ページ固有の差分（エンドポイント・モーダル等）は各 app ファイルに残す。

function updateProgress(percent) {
    progressFill.style.width = `${percent}%`;
    percentText.innerText = `${percent}%`;
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
