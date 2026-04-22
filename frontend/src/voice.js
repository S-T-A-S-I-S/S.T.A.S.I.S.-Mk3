// ── Voice input ───────────────────────────────────────────────────────────────
export function createVoiceInput(onResult) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const Ctor = (typeof SpeechRecognition !== 'undefined' ? SpeechRecognition : window.webkitSpeechRecognition);
    if (!Ctor) {
        console.warn('[STASIS] Web Speech API not available in this browser');
        return () => { };
    }
    const rec = new Ctor();
    rec.lang = 'en-US';
    rec.continuous = true;
    rec.interimResults = false;
    rec.onresult = (e) => {
        const last = e.results[e.results.length - 1];
        if (last.isFinal) {
            const text = last[0].transcript.trim();
            if (text)
                onResult(text);
        }
    };
    rec.onerror = (e) => {
        if (e.error !== 'no-speech')
            console.warn('[STASIS] Speech error:', e.error);
    };
    // Auto-restart on end (continuous listening)
    rec.onend = () => {
        try {
            rec.start();
        }
        catch { /* already started */ }
    };
    try {
        rec.start();
    }
    catch { /* ignore */ }
    return () => { rec.onend = null; rec.abort(); };
}
export function createAudioPlayer() {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const AudioCtx = (window.AudioContext ?? window.webkitAudioContext);
    const ctx = new AudioCtx();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    analyser.connect(ctx.destination);
    const queue = [];
    let playing = false;
    async function playNext() {
        if (playing || queue.length === 0)
            return;
        playing = true;
        const item = queue.shift();
        try {
            if (ctx.state === 'suspended')
                await ctx.resume();
            const bytes = Uint8Array.from(atob(item.b64), (c) => c.charCodeAt(0));
            const buf = await ctx.decodeAudioData(bytes.buffer);
            const src = ctx.createBufferSource();
            src.buffer = buf;
            src.connect(analyser);
            src.onended = () => { playing = false; void playNext(); };
            src.start();
        }
        catch (err) {
            console.error('[STASIS] Audio decode error:', err);
            playing = false;
            void playNext();
        }
    }
    return {
        analyser,
        enqueue(b64, format) {
            queue.push({ b64, format });
            void playNext();
        },
    };
}
