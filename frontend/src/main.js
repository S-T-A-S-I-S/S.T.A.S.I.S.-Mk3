import './style.css';
import { createOrb } from './orb';
import { createVoiceInput, createAudioPlayer } from './voice';
import { createWS } from './ws';
// ── DOM refs ──────────────────────────────────────────────────────────────────
const canvas = document.getElementById('canvas');
const statusEl = document.getElementById('status');
const responseEl = document.getElementById('response');
const transcriptEl = document.getElementById('transcript');
const connDot = document.getElementById('conn-dot');
const errorToast = document.getElementById('error-toast');
const settingsPanel = document.getElementById('settings-panel');
const muteBtn = document.getElementById('btn-mute');
const settingsBtn = document.getElementById('btn-settings');
const settingsClose = document.getElementById('settings-close');
const valBackend = document.getElementById('val-backend');
const valModel = document.getElementById('val-model');
const valUser = document.getElementById('val-user');
const valTts = document.getElementById('val-tts');
const valVoiceStatus = document.getElementById('val-voice-status');
const btnRecord = document.getElementById('btn-record');
const recordProgress = document.getElementById('record-progress');
const recordBar = document.getElementById('record-bar');
const recordTimer = document.getElementById('record-timer');
const recordStatus = document.getElementById('record-status');
// ── Core systems ──────────────────────────────────────────────────────────────
const orb = createOrb(canvas);
const audio = createAudioPlayer();
// ── UI helpers ────────────────────────────────────────────────────────────────
let responseTimer = null;
let transcriptTimer = null;
function setState(state) {
    orb.setState(state);
    if (state === 'idle') {
        statusEl.textContent = '';
        statusEl.classList.remove('visible', 'listening', 'thinking', 'speaking');
    }
    else {
        statusEl.textContent = state + '…';
        statusEl.className = `visible ${state}`;
    }
}
function showResponse(text) {
    responseEl.textContent = text;
    responseEl.classList.add('visible');
    if (responseTimer)
        clearTimeout(responseTimer);
    responseTimer = setTimeout(() => responseEl.classList.remove('visible'), 9000);
}
function showTranscript(text) {
    transcriptEl.textContent = `"${text}"`;
    transcriptEl.classList.add('visible');
    if (transcriptTimer)
        clearTimeout(transcriptTimer);
    transcriptTimer = setTimeout(() => transcriptEl.classList.remove('visible'), 3000);
}
function showError(msg) {
    errorToast.textContent = msg;
    errorToast.classList.add('visible');
    setTimeout(() => errorToast.classList.remove('visible'), 5000);
}
// ── WebSocket ─────────────────────────────────────────────────────────────────
let muted = false;
const ws = createWS((msg) => {
    const type = msg['type'];
    if (type === 'status') {
        const s = msg['state'];
        if (s)
            setState(s);
    }
    if (type === 'audio') {
        const b64 = msg['data'];
        const fmt = msg['format'] ?? 'wav';
        const text = msg['text'];
        if (text)
            showResponse(text);
        if (!muted)
            audio.enqueue(b64, fmt);
    }
    if (type === 'text') {
        showResponse(msg['text']);
        setState('idle');
    }
    if (type === 'error') {
        showError(msg['text']);
        setState('idle');
    }
}, () => connDot.classList.add('connected'), () => connDot.classList.remove('connected'));
// ── Voice input ───────────────────────────────────────────────────────────────
createVoiceInput((text) => {
    if (muted)
        return;
    showTranscript(text);
    setState('thinking');
    ws.send({ type: 'transcript', text });
});
// ── Audio visualiser → orb ────────────────────────────────────────────────────
if (audio.analyser) {
    const freq = new Uint8Array(audio.analyser.frequencyBinCount);
    (function loop() {
        requestAnimationFrame(loop);
        audio.analyser.getByteFrequencyData(freq);
        orb.pushAudio(freq);
    })();
}
// ── Controls ──────────────────────────────────────────────────────────────────
muteBtn.addEventListener('click', () => {
    muted = !muted;
    muteBtn.textContent = muted ? '🔇' : '🔊';
    muteBtn.classList.toggle('active', muted);
});
settingsBtn.addEventListener('click', () => settingsPanel.classList.add('open'));
settingsClose.addEventListener('click', () => settingsPanel.classList.remove('open'));
// ── Voice training ────────────────────────────────────────────────────────────
const RECORD_SECS = 30;
let mediaRecorder = null;
let recordChunks = [];
let recordInterval = null;
let recordStart = 0;
function setRecordStatus(msg, cls = '') {
    recordStatus.textContent = msg;
    recordStatus.className = `record-status${cls ? ' ' + cls : ''}`;
}
btnRecord.addEventListener('click', async () => {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        // Manual stop
        mediaRecorder.stop();
        return;
    }
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        recordChunks = [];
        mediaRecorder = new MediaRecorder(stream);
        mediaRecorder.ondataavailable = (e) => {
            if (e.data.size > 0)
                recordChunks.push(e.data);
        };
        mediaRecorder.onstop = async () => {
            // Stop mic tracks
            stream.getTracks().forEach(t => t.stop());
            if (recordInterval) {
                clearInterval(recordInterval);
                recordInterval = null;
            }
            recordProgress.classList.add('hidden');
            btnRecord.classList.remove('recording');
            btnRecord.textContent = '⏫ Uploading…';
            btnRecord.classList.add('uploading');
            btnRecord.disabled = true;
            setRecordStatus('Uploading to server…');
            const blob = new Blob(recordChunks, { type: mediaRecorder.mimeType });
            try {
                const res = await fetch('/api/voice/sample', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/octet-stream' },
                    body: blob,
                });
                const json = await res.json();
                if (res.ok) {
                    setRecordStatus(json['message'] ?? 'Voice sample saved!', 'ok');
                    btnRecord.textContent = '✓ Voice trained';
                    valVoiceStatus.textContent = 'trained';
                    valVoiceStatus.className = 'settings-value online';
                    valTts.textContent = 'cloned voice';
                }
                else {
                    setRecordStatus(json['message'] ?? 'Upload failed.', 'error');
                    btnRecord.textContent = '🎙 Start Recording';
                }
            }
            catch (err) {
                setRecordStatus(`Upload error: ${err}`, 'error');
                btnRecord.textContent = '🎙 Start Recording';
            }
            btnRecord.classList.remove('uploading');
            btnRecord.disabled = false;
        };
        // Start recording
        mediaRecorder.start(250);
        recordStart = Date.now();
        recordBar.style.width = '0%';
        recordProgress.classList.remove('hidden');
        btnRecord.classList.add('recording');
        btnRecord.textContent = '⏹ Stop Recording';
        setRecordStatus('Recording… speak naturally.');
        recordInterval = setInterval(() => {
            const elapsed = (Date.now() - recordStart) / 1000;
            const pct = Math.min((elapsed / RECORD_SECS) * 100, 100);
            recordBar.style.width = `${pct}%`;
            recordTimer.textContent = `${Math.floor(elapsed)} s`;
            if (elapsed >= RECORD_SECS) {
                mediaRecorder?.stop();
            }
        }, 1000);
    }
    catch (err) {
        setRecordStatus(`Mic error: ${err}`, 'error');
    }
});
// ── Health polling ────────────────────────────────────────────────────────────
async function poll() {
    try {
        const r = await fetch('/health');
        if (!r.ok)
            throw new Error(`${r.status}`);
        const d = await r.json();
        connDot.classList.add('connected');
        valBackend.textContent = d['backend'] ?? 'online';
        valBackend.className = 'settings-value online';
        valModel.textContent = d['model'] ?? '—';
        valUser.textContent = d['user'] ?? '—';
        const ttsVal = d['tts'] ?? 'pyttsx3';
        if (ttsVal === 'cloned') {
            valTts.textContent = 'cloned voice';
            valVoiceStatus.textContent = 'trained';
            valVoiceStatus.className = 'settings-value online';
            btnRecord.textContent = '🔄 Re-train Voice';
        }
        else {
            valTts.textContent = ttsVal === 'openai' ? `OpenAI (${d['voice'] ?? 'nova'})` :
                ttsVal === 'fish' ? 'Fish Audio' : 'pyttsx3 (SAPI5)';
        }
    }
    catch {
        valBackend.textContent = 'offline';
        valBackend.className = 'settings-value offline';
        connDot.classList.remove('connected');
    }
}
poll();
setInterval(poll, 15000);
