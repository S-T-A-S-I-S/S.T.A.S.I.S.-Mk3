import './style.css';
import { createOrb, OrbState } from './orb';
import { createVoiceInput, createAudioPlayer } from './voice';
import { createWS, WsClient } from './ws';

// ── DOM refs ──────────────────────────────────────────────────────────────────
const canvas          = document.getElementById('canvas')            as HTMLCanvasElement;
const statusEl        = document.getElementById('status')            as HTMLDivElement;
const responseEl      = document.getElementById('response')          as HTMLDivElement;
const transcriptEl    = document.getElementById('transcript')        as HTMLDivElement;
const connDot         = document.getElementById('conn-dot')          as HTMLDivElement;
const errorToast      = document.getElementById('error-toast')       as HTMLDivElement;
const settingsPanel   = document.getElementById('settings-panel')    as HTMLDivElement;
const muteBtn         = document.getElementById('btn-mute')          as HTMLButtonElement;
const settingsBtn     = document.getElementById('btn-settings')      as HTMLButtonElement;
const settingsClose   = document.getElementById('settings-close')    as HTMLButtonElement;
const valBackend      = document.getElementById('val-backend')       as HTMLSpanElement;
const valModel        = document.getElementById('val-model')         as HTMLSpanElement;
const valUser         = document.getElementById('val-user')          as HTMLSpanElement;
const valTts          = document.getElementById('val-tts')           as HTMLSpanElement;
const valVoiceStatus  = document.getElementById('val-voice-status')  as HTMLSpanElement;
const btnRecord       = document.getElementById('btn-record')        as HTMLButtonElement;
const recordProgress  = document.getElementById('record-progress')   as HTMLDivElement;
const recordBar       = document.getElementById('record-bar')        as HTMLDivElement;
const recordTimer     = document.getElementById('record-timer')      as HTMLSpanElement;
const recordStatus    = document.getElementById('record-status')     as HTMLDivElement;
const inputBackendUrl = document.getElementById('input-backend-url') as HTMLInputElement;

// ── Backend URL (configurable, stored in localStorage) ────────────────────────
const BACKEND_KEY     = 'stasis_backend_url';
const DEFAULT_BACKEND = 'http://localhost:8765';

function toWsUrl(base: string): string {
  try {
    const u = new URL(base.startsWith('http') ? base : `http://${base}`);
    u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
    u.pathname = '/ws/voice';
    return u.toString();
  } catch { return 'ws://localhost:8765/ws/voice'; }
}

function toHttpBase(base: string): string {
  try {
    const u = new URL(base.startsWith('http') ? base : `http://${base}`);
    u.pathname = u.search = u.hash = '';
    return u.toString().replace(/\/$/, '');
  } catch { return 'http://localhost:8765'; }
}

let backendBase = localStorage.getItem(BACKEND_KEY) ?? DEFAULT_BACKEND;
let httpBase    = toHttpBase(backendBase);

// ── Core systems ──────────────────────────────────────────────────────────────
const orb   = createOrb(canvas);
const audio = createAudioPlayer();

// ── UI helpers ────────────────────────────────────────────────────────────────
let responseTimer:   ReturnType<typeof setTimeout> | null = null;
let transcriptTimer: ReturnType<typeof setTimeout> | null = null;

function setState(state: OrbState): void {
  orb.setState(state);
  if (state === 'idle') {
    statusEl.textContent = '';
    statusEl.classList.remove('visible', 'listening', 'thinking', 'speaking');
  } else {
    statusEl.textContent = state + '…';
    statusEl.className   = `visible ${state}`;
  }
}

function showResponse(text: string): void {
  responseEl.textContent = text;
  responseEl.classList.add('visible');
  if (responseTimer) clearTimeout(responseTimer);
  responseTimer = setTimeout(() => responseEl.classList.remove('visible'), 9_000);
}

function showTranscript(text: string): void {
  transcriptEl.textContent = `"${text}"`;
  transcriptEl.classList.add('visible');
  if (transcriptTimer) clearTimeout(transcriptTimer);
  transcriptTimer = setTimeout(() => transcriptEl.classList.remove('visible'), 3_000);
}

function showError(msg: string): void {
  errorToast.textContent = msg;
  errorToast.classList.add('visible');
  setTimeout(() => errorToast.classList.remove('visible'), 5_000);
}

// ── WebSocket ─────────────────────────────────────────────────────────────────
let muted = false;

function handleMsg(msg: Record<string, unknown>): void {
  const type = msg['type'] as string;

  if (type === 'status') {
    const s = msg['state'] as OrbState | undefined;
    if (s) setState(s);
  }

  if (type === 'audio') {
    const b64  = msg['data']    as string;
    const fmt  = (msg['format'] as string | undefined) ?? 'wav';
    const text = msg['text']    as string | undefined;
    if (text) showResponse(text);
    if (!muted) audio.enqueue(b64, fmt);
  }

  if (type === 'text') {
    showResponse(msg['text'] as string);
    setState('idle');
  }

  if (type === 'error') {
    showError(msg['text'] as string);
    setState('idle');
  }
}

function onConn()    { connDot.classList.add('connected'); }
function onDisconn() { connDot.classList.remove('connected'); }

let ws: WsClient = createWS(toWsUrl(backendBase), handleMsg, onConn, onDisconn);

// ── Voice input ───────────────────────────────────────────────────────────────
createVoiceInput((text) => {
  if (muted) return;
  showTranscript(text);
  setState('thinking');
  ws.send({ type: 'transcript', text });
});

// ── Audio visualiser → orb ────────────────────────────────────────────────────
if (audio.analyser) {
  const freq = new Uint8Array(audio.analyser.frequencyBinCount) as Uint8Array<ArrayBuffer>;
  (function loop() {
    requestAnimationFrame(loop);
    audio.analyser!.getByteFrequencyData(freq);
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

// ── Backend URL ───────────────────────────────────────────────────────────────
inputBackendUrl.value = backendBase;

inputBackendUrl.addEventListener('change', () => {
  const v = inputBackendUrl.value.trim() || DEFAULT_BACKEND;
  inputBackendUrl.value = v;
  localStorage.setItem(BACKEND_KEY, v);
  backendBase = v;
  httpBase    = toHttpBase(v);
  ws.close();
  ws = createWS(toWsUrl(v), handleMsg, onConn, onDisconn);
  poll();
});

// ── Voice training ────────────────────────────────────────────────────────────
const RECORD_SECS = 30;
let mediaRecorder: MediaRecorder | null = null;
let recordChunks:  Blob[]               = [];
let recordInterval: ReturnType<typeof setInterval> | null = null;
let recordStart    = 0;

function setRecordStatus(msg: string, cls: 'ok' | 'error' | '' = ''): void {
  recordStatus.textContent = msg;
  recordStatus.className   = `record-status${cls ? ' ' + cls : ''}`;
}

btnRecord.addEventListener('click', async () => {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recordChunks = [];
    mediaRecorder = new MediaRecorder(stream);

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) recordChunks.push(e.data);
    };

    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());

      if (recordInterval) { clearInterval(recordInterval); recordInterval = null; }
      recordProgress.classList.add('hidden');
      btnRecord.classList.remove('recording');
      btnRecord.textContent = '⏫ Uploading…';
      btnRecord.classList.add('uploading');
      btnRecord.disabled = true;
      setRecordStatus('Uploading to server…');

      const blob = new Blob(recordChunks, { type: mediaRecorder!.mimeType });
      try {
        const res = await fetch(`${httpBase}/api/voice/sample`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/octet-stream' },
          body: blob,
        });
        const json = await res.json() as Record<string, string>;
        if (res.ok) {
          setRecordStatus(json['message'] ?? 'Voice sample saved!', 'ok');
          btnRecord.textContent = '✓ Voice trained';
          valVoiceStatus.textContent = 'trained';
          valVoiceStatus.className   = 'settings-value online';
          valTts.textContent         = 'cloned voice';
        } else {
          setRecordStatus(json['message'] ?? 'Upload failed.', 'error');
          btnRecord.textContent = '🎙 Start Recording';
        }
      } catch (err) {
        setRecordStatus(`Upload error: ${err}`, 'error');
        btnRecord.textContent = '🎙 Start Recording';
      }
      btnRecord.classList.remove('uploading');
      btnRecord.disabled = false;
    };

    mediaRecorder.start(250);
    recordStart = Date.now();
    recordBar.style.width = '0%';
    recordProgress.classList.remove('hidden');
    btnRecord.classList.add('recording');
    btnRecord.textContent = '⏹ Stop Recording';
    setRecordStatus('Recording… speak naturally.');

    recordInterval = setInterval(() => {
      const elapsed = (Date.now() - recordStart) / 1000;
      const pct     = Math.min((elapsed / RECORD_SECS) * 100, 100);
      recordBar.style.width    = `${pct}%`;
      recordTimer.textContent  = `${Math.floor(elapsed)} s`;
      if (elapsed >= RECORD_SECS) mediaRecorder?.stop();
    }, 1000);

  } catch (err) {
    setRecordStatus(`Mic error: ${err}`, 'error');
  }
});

// ── Health polling ────────────────────────────────────────────────────────────
async function poll(): Promise<void> {
  try {
    const r = await fetch(`${httpBase}/health`);
    if (!r.ok) throw new Error(`${r.status}`);
    const d = await r.json() as Record<string, string>;
    connDot.classList.add('connected');
    valBackend.textContent = d['backend'] ?? 'online';
    valBackend.className   = 'settings-value online';
    valModel.textContent   = d['model']   ?? '—';
    valUser.textContent    = d['user']    ?? '—';

    const ttsVal = d['tts'] ?? 'pyttsx3';
    if (ttsVal === 'cloned') {
      valTts.textContent         = 'cloned voice';
      valVoiceStatus.textContent = 'trained';
      valVoiceStatus.className   = 'settings-value online';
      btnRecord.textContent      = '🔄 Re-train Voice';
    } else {
      valTts.textContent = ttsVal === 'openai' ? `OpenAI (${d['voice'] ?? 'nova'})` :
                           ttsVal === 'fish'   ? 'Fish Audio' : 'pyttsx3 (SAPI5)';
    }
  } catch {
    valBackend.textContent = 'offline';
    valBackend.className   = 'settings-value offline';
    connDot.classList.remove('connected');
  }
}

poll();
setInterval(poll, 15_000);
