"""
S.T.A.S.I.S. Mk3 — FastAPI backend
Merges Mk1 (PC optimizer, voice UI) + Mk2 (richer memory, Wikipedia)

Run:   python server.py
Then:  http://127.0.0.1:8765

Voice training (web UI):
  Settings panel → Train My Voice → speak for 30 s → Upload
  The bot will start using your cloned voice for all TTS responses.
"""
import asyncio
import base64
import json
import os
import re
import subprocess
import tempfile
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
OLLAMA_BASE   = os.getenv("OLLAMA_BASE",    "http://127.0.0.1:11434")
OPENAI_KEY    = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
_default_model = "gpt-4o-mini" if OPENAI_KEY else ("claude-haiku-4-5-20251001" if ANTHROPIC_KEY else "llama3.2:3b")
MODEL         = os.getenv("STASIS_MODEL", _default_model)
OPENAI_VOICE  = os.getenv("OPENAI_VOICE",  "nova")
FISH_API_KEY  = os.getenv("FISH_API_KEY",  "")
FISH_VOICE_ID = os.getenv("FISH_VOICE_ID", "")
USER_NAME     = os.getenv("USER_NAME",     "User")
VOICE_RATE    = int(os.getenv("VOICE_RATE", "185"))
PROJECT_DIR   = Path(__file__).parent
VOICE_DIR     = PROJECT_DIR / "data" / "voice"
VOICE_DIR.mkdir(parents=True, exist_ok=True)

# ── Subsystems ────────────────────────────────────────────────────────────────
from memory       import build_context, remember, forget, add_task
from pc_optimizer import match as pc_match, load_profile, generate_profile, profile_summary
from voice_cloner import validate_sample, convert_to_wav, SAMPLE_PATH, has_sample, synth as vc_synth
from wiki_knowledge import wiki_search
import browser as _browser_mod

_pc_profile = load_profile() or generate_profile()

# ── System Prompt ─────────────────────────────────────────────────────────────
_SYSTEM = """\
You are S.T.A.S.I.S., a personal AI assistant running on Windows 11.
Talk like a sharp, helpful friend — direct, no corporate filler, no empty affirmations.
Use contractions. Keep spoken responses to 1–3 sentences.
Never say "How can I help?", "Is there anything else?", "I'd be happy to", \
"Certainly!", "Of course", or "Great question".

Hardware: {hw}
{memory}
Available action tags (strip ALL of them before speaking):
  [ACTION:OPEN] app_name              — launch an app (Spotify, Steam, Chrome…)
  [ACTION:TERMINAL]                   — open Windows Terminal
  [ACTION:BROWSE] url_or_query        — open browser / search Google (no control)
  [ACTION:SCREEN]                     — take a screenshot
  [ACTION:REMEMBER] fact              — store a permanent fact about the user
  [ACTION:FORGET] text                — delete memories matching this text
  [ACTION:ADD_TASK] description       — add a task
  [ACTION:WIKI] query                 — look up a topic on Wikipedia (always fresh)
  [ACTION:READ_FILE] filename         — read a project file
  [ACTION:WRITE_FILE] filename|content — overwrite a project file
  [ACTION:BROWSER_GO] url             — navigate the controlled browser to a URL
  [ACTION:BROWSER_SEARCH] query       — Google search in the controlled browser
  [ACTION:BROWSER_CLICK] text         — click element by visible text or CSS selector
  [ACTION:BROWSER_FILL] selector|text — type into a form field
  [ACTION:BROWSER_READ]               — read the current page and summarise it
  [ACTION:BROWSER_BACK]               — go back one page
  [ACTION:BROWSER_SCROLL] down/up     — scroll the page

Use [ACTION:WIKI] for quick facts. Use BROWSER_* when the user wants you to actually
control the browser, navigate a site, read live content, or interact with a page.
Use BROWSER_READ after navigating to answer questions about page contents.
For code edits: read the file first, then rewrite with WRITE_FILE.
User: {user}  |  Time: {time}
"""

# ── LLM ───────────────────────────────────────────────────────────────────────
import urllib.request, urllib.error

_hw_summary = ""

def _build_system(user_message: str = "") -> str:
    global _hw_summary
    if not _hw_summary:
        _hw_summary = profile_summary(_pc_profile)
    mem = build_context(user_message)
    mem_block = f"\n{mem}\n" if mem else ""
    return _SYSTEM.format(
        user=USER_NAME,
        time=datetime.now().strftime("%Y-%m-%d %H:%M"),
        hw=_hw_summary,
        memory=mem_block,
    )


def llm_stream(prompt: str, system_override: str = ""):
    system = system_override or _build_system(prompt)
    if OPENAI_KEY:
        yield from _openai_stream(prompt, system)
    elif ANTHROPIC_KEY:
        yield from _anthropic_stream(prompt, system)
    else:
        yield from _ollama_stream(prompt, system)


def _openai_stream(prompt: str, system: str):
    url = "https://api.openai.com/v1/chat/completions"
    payload = json.dumps({
        "model": MODEL, "stream": True, "max_tokens": 512, "temperature": 0.6,
        "messages": [{"role": "system", "content": system},
                     {"role": "user",   "content": prompt}],
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_KEY}",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line or line == "data: [DONE]":
                    continue
                if line.startswith("data: "):
                    try:
                        chunk = json.loads(line[6:])["choices"][0]["delta"].get("content")
                        if chunk:
                            yield chunk
                    except Exception:
                        pass
    except Exception as e:
        yield f"⚠ OpenAI error: {e}"


def _anthropic_stream(prompt: str, system: str):
    url = "https://api.anthropic.com/v1/messages"
    payload = json.dumps({
        "model": MODEL, "max_tokens": 512,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            yield data["content"][0]["text"]
    except Exception as e:
        yield f"⚠ Anthropic error: {e}"


def _ollama_stream(prompt: str, system: str):
    url = f"{OLLAMA_BASE}/api/chat"
    payload = json.dumps({
        "model": MODEL, "stream": True,
        "messages": [{"role": "system", "content": system},
                     {"role": "user",   "content": prompt}],
        "options": {"temperature": 0.6, "num_predict": 512},
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    chunk = obj.get("message", {}).get("content")
                    if chunk:
                        yield chunk
                    if obj.get("done"):
                        return
                except Exception:
                    pass
    except Exception as e:
        yield f"⚠ Ollama error: {e}"


_FACT_PATTERNS = [
    re.compile(r"my name is ([A-Za-z][\w ]{1,30})", re.I),
    re.compile(r"I(?:'m| am) (?:a |an )?(.{3,60}?)(?:\.|,|$)", re.I),
    re.compile(r"I (?:work|study) (?:at|in|for) (.{3,60}?)(?:\.|,|$)", re.I),
    re.compile(r"I (?:like|love|hate|prefer|use|run|have) (.{3,60}?)(?:\.|,|$)", re.I),
]

async def _auto_extract(user_msg: str, _: str = "") -> None:
    for pat in _FACT_PATTERNS:
        m = pat.search(user_msg)
        if m and len(m.group(0)) < 100:
            remember(m.group(0).strip().rstrip(".,"), source="auto-extract")


# ── TTS ───────────────────────────────────────────────────────────────────────
async def synth_audio(text: str) -> tuple[Optional[bytes], str]:
    """Return (audio_bytes, format). Priority: XTTS → OpenAI → Fish → pyttsx3."""
    # 1. Coqui XTTS v2 (cloned voice)
    if has_sample():
        result = await asyncio.to_thread(vc_synth, text)
        if result:
            return result, "wav"

    # 2. OpenAI TTS
    if OPENAI_KEY:
        result = await _openai_tts(text)
        if result:
            return result, "mp3"

    # 3. Fish Audio
    if FISH_API_KEY and FISH_VOICE_ID:
        result = await _fish_tts(text)
        if result:
            return result, "mp3"

    # 4. pyttsx3
    return await asyncio.to_thread(_pyttsx3_tts, text), "wav"


async def _openai_tts(text: str) -> Optional[bytes]:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {OPENAI_KEY}",
                         "Content-Type": "application/json"},
                json={"model": "tts-1", "input": text, "voice": OPENAI_VOICE,
                      "response_format": "mp3"},
            )
            return r.content if r.status_code == 200 else None
    except Exception as e:
        print(f"[OpenAI TTS] {e}")
        return None


async def _fish_tts(text: str) -> Optional[bytes]:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.fish.audio/v1/tts",
                headers={"Authorization": f"Bearer {FISH_API_KEY}",
                         "Content-Type": "application/json"},
                json={"text": text, "reference_id": FISH_VOICE_ID,
                      "format": "mp3", "latency": "normal"},
            )
            return r.content if r.status_code == 200 else None
    except Exception as e:
        print(f"[Fish TTS] {e}")
        return None


_pyttsx3_engine = None

def _pyttsx3_tts(text: str) -> Optional[bytes]:
    global _pyttsx3_engine
    try:
        import pyttsx3
        if _pyttsx3_engine is None:
            _pyttsx3_engine = pyttsx3.init()
            _pyttsx3_engine.setProperty("rate", VOICE_RATE)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
        _pyttsx3_engine.save_to_file(text, tmp)
        _pyttsx3_engine.runAndWait()
        with open(tmp, "rb") as f:
            audio = f.read()
        os.unlink(tmp)
        return audio if len(audio) > 100 else None
    except Exception as e:
        print(f"[pyttsx3 TTS] {e}")
        _pyttsx3_engine = None
        return None


# ── Action dispatch ───────────────────────────────────────────────────────────
_ACTION_RE = re.compile(r"\[ACTION:(\w+)\](?:\s*([^\[]+))?", re.DOTALL)


async def dispatch_actions(text: str, ws: WebSocket) -> None:
    for m in _ACTION_RE.finditer(text):
        action = m.group(1)
        arg    = (m.group(2) or "").strip()

        if action == "OPEN":
            from pc_optimizer import open_app
            await ws.send_json({"type": "status", "text": open_app(arg)})

        elif action == "TERMINAL":
            try:
                subprocess.Popen(["wt.exe"])
            except FileNotFoundError:
                subprocess.Popen(["cmd.exe"])

        elif action == "BROWSE":
            url = arg if arg.startswith("http") else (
                f"https://www.google.com/search?q={arg.replace(' ', '+')}"
            )
            webbrowser.open(url)

        elif action == "SCREEN":
            try:
                from PIL import ImageGrab
                img = ImageGrab.grab()
                msg = f"Screenshot: {img.width}×{img.height}"
            except Exception as e:
                msg = f"Screenshot failed: {e}"
            await ws.send_json({"type": "status", "text": msg})

        elif action == "REMEMBER":
            if arg:
                remember(arg, source="voice-command")

        elif action == "FORGET":
            if arg:
                n = forget(arg)
                await ws.send_json({"type": "status", "text": f"Forgot {n} entries matching '{arg}'."})

        elif action == "ADD_TASK":
            if arg:
                add_task(arg)
                await ws.send_json({"type": "status", "text": f"Task added: {arg}"})

        elif action == "WIKI":
            if arg:
                result = wiki_search(arg)
                await ws.send_json({"type": "status", "text": f"Wikipedia: {result[:200]}"})

        elif action == "READ_FILE":
            content = _read_file(arg)
            await ws.send_json({"type": "status", "text": f"Read {arg}"})
            if content and not content.startswith("⚠"):
                follow = f"File contents of {arg}:\n\n{content}\n\nApply the requested change and use WRITE_FILE to save."
                full2  = "".join(llm_stream(follow))
                await dispatch_actions(full2, ws)
                voice2 = strip_for_tts(full2)
                audio2, fmt2 = await synth_audio(" ".join(re.split(r"(?<=[.!?])\s+", voice2)[:2]))
                if audio2:
                    await ws.send_json({"type": "audio", "data": base64.b64encode(audio2).decode(),
                                        "text": voice2, "format": fmt2})
                else:
                    await ws.send_json({"type": "text", "text": voice2})

        elif action == "WRITE_FILE":
            if "|" in arg:
                fname, content = arg.split("|", 1)
                result = _write_file(fname.strip(), content.strip())
                await ws.send_json({"type": "status", "text": result})

        elif action == "BROWSER_GO":
            if arg:
                result = await _browser_mod.go(arg)
                await ws.send_json({"type": "status", "text": result})

        elif action == "BROWSER_SEARCH":
            if arg:
                result = await _browser_mod.search(arg)
                await ws.send_json({"type": "status", "text": result})

        elif action == "BROWSER_CLICK":
            if arg:
                result = await _browser_mod.click(arg)
                await ws.send_json({"type": "status", "text": result})

        elif action == "BROWSER_FILL":
            if "|" in arg:
                selector, value = arg.split("|", 1)
                result = await _browser_mod.fill(selector.strip(), value.strip())
                await ws.send_json({"type": "status", "text": result})

        elif action == "BROWSER_BACK":
            result = await _browser_mod.back()
            await ws.send_json({"type": "status", "text": result})

        elif action == "BROWSER_SCROLL":
            result = await _browser_mod.scroll(arg or "down")
            await ws.send_json({"type": "status", "text": result})

        elif action == "BROWSER_READ":
            page_text = await _browser_mod.read_page()
            url = await _browser_mod.current_url()
            await ws.send_json({"type": "status", "text": f"Reading {url}…"})
            follow = (
                f"Current browser page ({url}):\n\n{page_text}\n\n"
                "Summarise what's relevant to the user's request in 1–2 sentences."
            )
            summary = "".join(llm_stream(follow))
            summary = strip_for_tts(summary)
            audio, fmt = await synth_audio(summary)
            if audio:
                await ws.send_json({"type": "audio",
                                    "data": base64.b64encode(audio).decode(),
                                    "text": summary, "format": fmt})
            else:
                await ws.send_json({"type": "text", "text": summary})


def _read_file(filename: str) -> str:
    try:
        path = (PROJECT_DIR / filename.strip()).resolve()
        if not str(path).startswith(str(PROJECT_DIR.resolve())):
            return "⚠ Access denied."
        return path.read_text(encoding="utf-8") if path.exists() else f"⚠ Not found: {filename}"
    except Exception as e:
        return f"⚠ {e}"


def _write_file(filename: str, content: str) -> str:
    try:
        path = (PROJECT_DIR / filename.strip()).resolve()
        if not str(path).startswith(str(PROJECT_DIR.resolve())):
            return "⚠ Access denied."
        if path.suffix not in {".py", ".txt", ".json", ".md", ".env", ".ts", ".css", ".html"}:
            return f"⚠ Won't write {path.suffix} files."
        path.write_text(content, encoding="utf-8")
        return f"Wrote {filename} ({len(content)} chars)."
    except Exception as e:
        return f"⚠ {e}"


def strip_for_tts(text: str) -> str:
    text = _ACTION_RE.sub("", text)
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)
    return re.sub(r"\n{2,}", " ", text).strip()


# ── FastAPI ───────────────────────────────────────────────────────────────────
from contextlib import asynccontextmanager

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    yield
    await _browser_mod.close()

app = FastAPI(title="S.T.A.S.I.S. Mk3", lifespan=_lifespan)
_DIST = PROJECT_DIR / "frontend" / "dist"


@app.get("/health")
async def health():
    return {
        "status": "online",
        "model":    MODEL,
        "backend":  "openai" if OPENAI_KEY else ("anthropic" if ANTHROPIC_KEY else "ollama"),
        "user":     USER_NAME,
        "tts":      "openai" if OPENAI_KEY else ("fish" if FISH_API_KEY else "pyttsx3"),
        "voice":    OPENAI_VOICE if OPENAI_KEY else "—",
        "wiki":     "live-api",
        "time":     datetime.now().isoformat(),
    }


@app.get("/api/memories")
async def api_memories():
    from memory import get_important_memories, get_open_tasks
    return {"facts": get_important_memories(50), "tasks": get_open_tasks()}


@app.get("/api/scan")
async def api_scan():
    global _pc_profile
    _pc_profile = generate_profile()
    return {"status": "ok", "profile": _pc_profile}


# ── Voice training endpoints ──────────────────────────────────────────────────
@app.post("/api/voice/sample")
async def api_voice_sample(request: Request):
    """
    Receive audio from the browser (WebM or WAV).
    Saves to data/voice/sample.wav (converting via ffmpeg if needed).
    """
    data = await request.body()
    if not data:
        return JSONResponse({"status": "error", "message": "No audio data."}, 400)

    raw = VOICE_DIR / "sample_raw.bin"
    raw.write_bytes(data)

    # Try ffmpeg conversion (WebM → WAV)
    if not convert_to_wav(raw, SAMPLE_PATH):
        # Maybe it's already WAV; save directly
        SAMPLE_PATH.write_bytes(data)

    err = validate_sample()
    if err:
        return JSONResponse({"status": "error", "message": err}, 422)

    return {"status": "ok", "bytes": len(data), "message": "Voice sample saved. STASIS will now speak in your voice."}


@app.get("/api/voice/status")
async def api_voice_status():
    from voice_cloner import status as vc_status
    return vc_status()


# ── WebSocket ─────────────────────────────────────────────────────────────────
@app.websocket("/ws/voice")
async def ws_voice(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") != "transcript":
                continue

            text = (msg.get("text") or "").strip()
            if not text:
                continue

            # STT corrections
            for wrong, right in [("travis","stasis"),("spaces","stasis"),("status ai","stasis")]:
                text = re.sub(rf"\b{wrong}\b", right, text, flags=re.IGNORECASE)

            await ws.send_json({"type": "status", "state": "thinking"})

            # Local PC optimizer (no API call, no cost)
            local = pc_match(text)
            if local is not None:
                await ws.send_json({"type": "status", "state": "speaking"})
                sents = re.split(r"(?<=[.!?])\s+", local)
                audio, fmt = await synth_audio(" ".join(sents[:2]))
                if audio:
                    await ws.send_json({"type": "audio",
                                        "data": base64.b64encode(audio).decode(),
                                        "text": local, "format": fmt})
                else:
                    await ws.send_json({"type": "text", "text": local})
                await ws.send_json({"type": "status", "state": "idle"})
                continue

            # LLM (run synchronous urllib calls off the event loop)
            full = await asyncio.to_thread(lambda: "".join(llm_stream(text)))
            await dispatch_actions(full, ws)

            voice = strip_for_tts(full)
            sents = re.split(r"(?<=[.!?])\s+", voice)
            audio, fmt = await synth_audio(" ".join(sents[:2]))

            await ws.send_json({"type": "status", "state": "speaking"})
            if audio:
                await ws.send_json({"type": "audio",
                                    "data": base64.b64encode(audio).decode(),
                                    "text": voice, "format": fmt})
            else:
                await ws.send_json({"type": "text", "text": voice})
            await ws.send_json({"type": "status", "state": "idle"})

            asyncio.create_task(_auto_extract(text, voice))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[WS error] {e}")
        try:
            await ws.send_json({"type": "error", "text": str(e)})
        except Exception:
            pass


# ── Static files ──────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    idx = _DIST / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return HTMLResponse(
        "<h1 style='font-family:sans-serif;color:#2B7DFF'>S.T.A.S.I.S. Mk3</h1>"
        "<p>Build the frontend first:<br>"
        "<code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code></p>"
    )


if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")


if __name__ == "__main__":
    print("S.T.A.S.I.S. Mk3  →  http://127.0.0.1:8765")
    uvicorn.run("server:app", host="127.0.0.1", port=8765, reload=False)
