# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

S.T.A.S.I.S. Mk3 — a merged evolution of Mk1 (voice UI, PC optimizer) and Mk2 (richer memory, Discord bot). Key additions: Coqui XTTS v2 voice cloning (user records their voice; STASIS speaks back in it), live Wikipedia lookups via action tags, and the full Mk1 PC optimizer accessible from Discord.

## Running

### Backend
```bash
pip install -r requirements.txt
python server.py          # http://127.0.0.1:8765
```
Set at least one LLM key in `.env` (copy `.env.example`). Ollama is the offline fallback.

### Discord bot
```bash
python discord_bot.py
```
Requires `DISCORD_BOT_TOKEN` in `.env`.

### Frontend
```bash
cd frontend
npm install
npm run build             # output → frontend/dist/
# or for hot-reload dev:
npm run dev               # http://localhost:5173 (proxies to backend)
```

## Architecture

| File | Role |
|------|------|
| `server.py` | FastAPI + WebSocket backend |
| `discord_bot.py` | Discord bot — text responses + optional audio files |
| `memory.py` | SQLite + FTS5 memory (facts, tasks, notes) → `data/stasis.db` |
| `pc_optimizer.py` | Local rule-based Windows PC commands (no API call) |
| `voice_cloner.py` | Coqui XTTS v2 voice cloning from `data/voice/sample.wav` |
| `wiki_knowledge.py` | Wikipedia REST API lookups with SQLite cache |

## WebSocket protocol (`/ws/voice`)
```
client → { type: "transcript", text: "..." }
server → { type: "status",  state: "thinking|speaking|idle" }
server → { type: "audio",   data: "<base64>", text: "...", format: "mp3|wav" }
server → { type: "text",    text: "..." }
server → { type: "error",   text: "..." }
```

## Action tags (stripped before TTS/Discord output)
- `[ACTION:OPEN] app` — open app via URI/exe
- `[ACTION:TERMINAL]` — Windows Terminal
- `[ACTION:BROWSE] url_or_query` — browser/Google
- `[ACTION:SCREEN]` — PIL screenshot
- `[ACTION:REMEMBER] fact` — persist to SQLite
- `[ACTION:FORGET] text` — delete matching memories
- `[ACTION:ADD_TASK] desc` — add task
- `[ACTION:WIKI] query` — Wikipedia lookup (injected back into context)
- `[ACTION:READ_FILE] filename` — read project file
- `[ACTION:WRITE_FILE] filename|content` — overwrite project file

## TTS priority
1. Coqui XTTS v2 cloned voice (if `data/voice/sample.wav` exists + TTS installed)
2. OpenAI TTS `tts-1` (if `OPENAI_API_KEY` set)
3. Fish Audio (if `FISH_API_KEY` + `FISH_VOICE_ID` set)
4. pyttsx3 SAPI5 (always available on Windows)

## Voice training
- Web UI: Settings panel → Train My Voice → record 30 s → uploaded to `POST /api/voice/sample`
- Server converts WebM→WAV via ffmpeg, saves to `data/voice/sample.wav`
- `voice_cloner.py` validates the sample; XTTS loads on first synth call

## Wikipedia
- Live Wikipedia REST API by default (no keys, always current)
- `python wiki_knowledge.py --download` downloads Simple English Wikipedia (~120 MB) for offline use
- Results cached in `data/wiki_cache.db` for 7 days

## Discord commands
`!clear` `!memory` `!tasks` `!forget <text>` `!wiki <query>` `!pc <command>` `!voice <text>`

## Data paths
- Memory DB: `data/stasis.db`
- PC profile: `%APPDATA%\STASIS\pc_profile.json`
- Voice sample: `data/voice/sample.wav`
- Wiki cache: `data/wiki_cache.db`
