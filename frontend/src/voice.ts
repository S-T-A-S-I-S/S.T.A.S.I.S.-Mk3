// Web Speech API types (not in default TS lib)
interface SpeechRecognitionResult {
  readonly isFinal: boolean;
  readonly [index: number]: SpeechRecognitionAlternative;
}
interface SpeechRecognitionAlternative {
  readonly transcript: string;
  readonly confidence: number;
}
interface SpeechRecognitionResultList {
  readonly length: number;
  readonly [index: number]: SpeechRecognitionResult;
}
interface SpeechRecognitionEvent extends Event {
  readonly results: SpeechRecognitionResultList;
}
interface SpeechRecognitionErrorEvent extends Event {
  readonly error: string;
}
interface ISpeechRecognition extends EventTarget {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: SpeechRecognitionEvent) => void) | null;
  onerror: ((e: SpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
declare const SpeechRecognition: { new(): ISpeechRecognition } | undefined;

// ── Voice input ───────────────────────────────────────────────────────────────
export function createVoiceInput(onResult: (text: string) => void): () => void {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const Ctor = (typeof SpeechRecognition !== 'undefined' ? SpeechRecognition : (window as any).webkitSpeechRecognition) as
    | { new(): ISpeechRecognition }
    | undefined;

  if (!Ctor) {
    console.warn('[STASIS] Web Speech API not available in this browser');
    return () => {};
  }

  const rec = new Ctor();
  rec.lang = 'en-US';
  rec.continuous = true;
  rec.interimResults = false;

  rec.onresult = (e: SpeechRecognitionEvent) => {
    const last = e.results[e.results.length - 1];
    if (last.isFinal) {
      const text = last[0].transcript.trim();
      if (text) onResult(text);
    }
  };

  rec.onerror = (e: SpeechRecognitionErrorEvent) => {
    if (e.error !== 'no-speech') console.warn('[STASIS] Speech error:', e.error);
  };

  // Auto-restart on end (continuous listening)
  rec.onend = () => {
    try { rec.start(); } catch { /* already started */ }
  };

  try { rec.start(); } catch { /* ignore */ }

  return () => { rec.onend = null; rec.abort(); };
}

// ── Audio playback queue ──────────────────────────────────────────────────────
export interface AudioPlayer {
  enqueue(b64: string, format: string): void;
  readonly analyser: AnalyserNode | null;
}

export function createAudioPlayer(): AudioPlayer {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const AudioCtx = (window.AudioContext ?? (window as any).webkitAudioContext) as typeof AudioContext;
  const ctx = new AudioCtx();

  const analyser = ctx.createAnalyser();
  analyser.fftSize = 512;
  analyser.connect(ctx.destination);

  const queue: Array<{ b64: string; format: string }> = [];
  let playing = false;

  async function playNext(): Promise<void> {
    if (playing || queue.length === 0) return;
    playing = true;
    const item = queue.shift()!;

    try {
      if (ctx.state === 'suspended') await ctx.resume();
      const bytes = Uint8Array.from(atob(item.b64), (c) => c.charCodeAt(0));
      const buf   = await ctx.decodeAudioData(bytes.buffer);
      const src   = ctx.createBufferSource();
      src.buffer  = buf;
      src.connect(analyser);
      src.onended = () => { playing = false; void playNext(); };
      src.start();
    } catch (err) {
      console.error('[STASIS] Audio decode error:', err);
      playing = false;
      void playNext();
    }
  }

  return {
    analyser,
    enqueue(b64: string, format: string): void {
      queue.push({ b64, format });
      void playNext();
    },
  };
}
