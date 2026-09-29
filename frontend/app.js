// Configuration
const API_BASE = 'http://localhost:8000/api';
let currentDocId = null;
let msgSeq = 0;

// DOM Elements
const fileInput = document.getElementById('file-input');
const uploadZone = document.getElementById('upload-zone');
const uploadStatus = document.getElementById('upload-status');
const progressContainer = document.getElementById('progress-container');
const progressBar = document.getElementById('progress-bar');
const chatContainer = document.getElementById('chat-container');
const chatHistory = document.getElementById('chat-history');
const questionInput = document.getElementById('question-input');
const sendBtn = document.getElementById('send-btn');

// Settings Elements
const topKInput = document.getElementById('top_k');
const minScoreInput = document.getElementById('min_score');
const apiKeyInput = document.getElementById('api_key');

// Metrics Elements
const metricIdxLatency = document.getElementById('metric-index-latency');
const metricRetLatency = document.getElementById('metric-retrieval-latency');
const metricChunks = document.getElementById('metric-chunks');

// Event Listeners
fileInput.addEventListener('change', handleFileUpload);
sendBtn.addEventListener('click', handleAskQuestion);
questionInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') handleAskQuestion();
});

async function handleFileUpload(e) {
    const file = e.target.files[0];
    if (!file) return;

    uploadStatus.textContent = 'Uploading and indexing document...';
    uploadStatus.className = 'status-msg';
    progressContainer.classList.remove('hidden');

    // Simulate progress bar (since it's a single blocking request)
    let progress = 0;
    const progressInterval = setInterval(() => {
        progress += 5;
        if (progress > 90) progress = 90;
        progressBar.style.width = `${progress}%`;
    }, 100);

    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch(`${API_BASE}/upload`, {
            method: 'POST',
            body: formData
        });

        clearInterval(progressInterval);
        progressBar.style.width = '100%';

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || 'Upload failed');
        }

        currentDocId = data.doc_id;
        metricIdxLatency.textContent = `${data.latency_ms} ms`;

        setTimeout(() => {
            uploadZone.classList.add('hidden');
            chatContainer.classList.remove('hidden');
            questionInput.disabled = false;
            sendBtn.disabled = false;
            questionInput.focus();
        }, 500);

    } catch (error) {
        clearInterval(progressInterval);
        uploadStatus.textContent = error.message;
        uploadStatus.className = 'status-msg error';
        progressBar.style.width = '0%';
    }
}

async function handleAskQuestion() {
    const question = questionInput.value.trim();
    if (!question || !currentDocId) return;

    // Append User Message
    appendMessage('user', question);
    questionInput.value = '';
    questionInput.disabled = true;
    sendBtn.disabled = true;

    // Loading indicator
    const loadingId = appendMessage('bot', 'Thinking...');

    try {
        const response = await fetch(`${API_BASE}/ask`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                doc_id: currentDocId,
                question: question,
                top_k: parseInt(topKInput.value),
                min_score: parseFloat(minScoreInput.value),
                api_key: apiKeyInput.value
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || 'Failed to get answer');
        }

        updateMessage(loadingId, data);
        metricRetLatency.textContent = `${data.retrieval_latency_ms} ms`;
        metricChunks.textContent = data.citations.length.toString();

    } catch (error) {
        updateMessageError(loadingId, error.message);
    } finally {
        questionInput.disabled = false;
        sendBtn.disabled = false;
        questionInput.focus();
    }
}

function appendMessage(role, text) {
    const msgId = 'msg-' + Date.now() + '-' + (msgSeq++);
    const div = document.createElement('div');
    div.className = `message ${role}`;
    div.id = msgId;

    if (role === 'user') {
        div.textContent = text;
    } else {
        div.innerHTML = `<div class="content">${text}</div>`;
    }

    chatHistory.appendChild(div);
    chatHistory.scrollTop = chatHistory.scrollHeight;
    return msgId;
}

function updateMessage(msgId, data) {
    const msgElement = document.getElementById(msgId);
    if (!msgElement) return;

    let html = `<div class="bot-mode-badge">${data.mode} mode</div>`;

    // Format Answer text
    html += `<div class="content"><pre>${escapeHtml(data.answer)}</pre></div>`;

    // Add Citations if available
    if (data.citations && data.citations.length > 0) {
        html += `<div class="citations-container">`;
        data.citations.forEach((cit, index) => {
            html += `
                <div class="citation-card">
                    <div class="citation-header">
                        <span>Source ${index + 1}</span>
                        <span>Similarity: ${cit.score.toFixed(2)}</span>
                    </div>
                    <div class="citation-highlight">${escapeHtml(cit.text)}</div>
                </div>
            `;
        });
        html += `</div>`;
    }

    msgElement.innerHTML = html;
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

function updateMessageError(msgId, errorMsg) {
    const msgElement = document.getElementById(msgId);
    if (msgElement) {
        msgElement.innerHTML = `<div style="color: var(--error)">Error: ${escapeHtml(errorMsg)}</div>`;
    }
}

function escapeHtml(unsafe) {
    return unsafe
         .replace(/&/g, "&amp;")
         .replace(/</g, "&lt;")
         .replace(/>/g, "&gt;")
         .replace(/"/g, "&quot;")
         .replace(/'/g, "&#039;");
}
