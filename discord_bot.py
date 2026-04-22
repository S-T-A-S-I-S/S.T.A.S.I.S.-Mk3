"""
S.T.A.S.I.S. Mk3 — Discord Bot
Brings the full Mk1 PC-optimizer + Mk2 memory + Wikipedia to Discord.

Setup
-----
1. Discord Developer Portal → New App → Bot → copy token → .env DISCORD_BOT_TOKEN
2. Bot → Privileged Gateway Intents → enable "Message Content Intent"
3. OAuth2 → URL Generator → scopes: bot → perms: Send Messages, Read History,
   Use Slash Commands, Attach Files → invite to your server
4. python discord_bot.py

Commands
--------
  @STASIS <message>        — chat with STASIS
  Any message in DISCORD_CHANNEL_ID channel
  !clear                   — wipe your conversation history
  !memory                  — show stored memories
  !tasks                   — list open tasks
  !forget <text>           — delete memories matching text
  !wiki <query>            — direct Wikipedia lookup
  !pc <command>            — run a PC optimizer command (e.g. !pc disk space)
  !voice                   — have STASIS reply as an audio file (if TTS available)
"""
import asyncio
import io
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
import discord
from discord.ext import commands

# ── Env loading ───────────────────────────────────────────────────────────────
_env = Path(__file__).parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# ── Config ────────────────────────────────────────────────────────────────────
DISCORD_TOKEN  = os.getenv("DISCORD_BOT_TOKEN",  "")
OPENAI_KEY     = os.getenv("OPENAI_API_KEY",      "")
ANTHROPIC_KEY  = os.getenv("ANTHROPIC_API_KEY",   "")
OLLAMA_BASE    = os.getenv("OLLAMA_BASE",          "http://127.0.0.1:11434")
CHANNEL_ID     = int(os.getenv("DISCORD_CHANNEL_ID", "0") or "0")
USER_NAME      = os.getenv("USER_NAME",            "")
OPENAI_VOICE   = os.getenv("OPENAI_VOICE",         "nova")

if OPENAI_KEY:
    MODEL   = os.getenv("STASIS_MODEL", "gpt-4o-mini")
    BACKEND = "openai"
elif ANTHROPIC_KEY:
    MODEL   = os.getenv("STASIS_MODEL", "claude-haiku-4-5-20251001")
    BACKEND = "anthropic"
else:
    MODEL   = os.getenv("STASIS_MODEL", "llama3.2:3b")
    BACKEND = "ollama"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("stasis.discord")
log.info(f"Backend: {BACKEND}  Model: {MODEL}")

# ── Subsystems ────────────────────────────────────────────────────────────────
from memory        import remember, recall, get_open_tasks, get_important_memories, add_task, forget as mem_forget
from pc_optimizer  import match as pc_match
from wiki_knowledge import wiki_search
from voice_cloner  import has_sample, synth as vc_synth

# ── System prompt ─────────────────────────────────────────────────────────────
_SYSTEM = """\
You are S.T.A.S.I.S., a personal AI assistant. You're responding via Discord.
Talk like a sharp, helpful friend — direct, no filler. Use contractions. Be brief.
Keep responses under 3 sentences unless detail is genuinely needed.
Never say "How can I help?", "Is there anything else?", "I'd be happy to",
"Certainly!", or "Great question".
{user_line}Use [ACTION:WIKI] query to look up anything factual you're unsure about.
When you see [ACTION:WIKI] in your own response, a lookup will run automatically.
{memory_block}
Time: {time}
"""

# ── Per-user history ──────────────────────────────────────────────────────────
_history: dict[int, list[dict]] = {}
MAX_HISTORY = 14


def _get_history(uid: int) -> list[dict]:
    return _history.setdefault(uid, [])


def _push(uid: int, role: str, content: str) -> None:
    h = _get_history(uid)
    h.append({"role": role, "content": content})
    if len(h) > MAX_HISTORY:
        del h[:-MAX_HISTORY]


# ── Memory context ────────────────────────────────────────────────────────────
def _mem_block() -> str:
    important = get_important_memories(limit=15)
    tasks     = get_open_tasks()
    lines: list[str] = []
    if important:
        lines.append("Known facts about the user:")
        for m in important:
            lines.append(f"  - {m['content']}")
    if tasks:
        lines.append("Open tasks:")
        for t in tasks:
            lines.append(f"  - [{t.get('priority','medium')}] {t['title']}")
    return ("\n" + "\n".join(lines) + "\n") if lines else ""


def _build_system() -> str:
    return _SYSTEM.format(
        user_line=f"User: {USER_NAME}\n" if USER_NAME else "",
        memory_block=_mem_block(),
        time=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )


# ── Action handling in Discord responses ─────────────────────────────────────
_ACTION_RE = re.compile(r"\[ACTION:(\w+)\](?:\s*([^\[\n]+))?")


def process_actions(text: str) -> tuple[str, list[str]]:
    """
    Scan the LLM reply for action tags.
    Returns (cleaned_text, list_of_status_messages).
    """
    statuses: list[str] = []
    for m in _ACTION_RE.finditer(text):
        action = m.group(1)
        arg    = (m.group(2) or "").strip()

        if action == "WIKI" and arg:
            result = wiki_search(arg)
            statuses.append(f"Wikipedia — {arg}:\n{result}")

        elif action == "REMEMBER" and arg:
            remember(arg, source="discord")
            statuses.append(f"Remembered: {arg}")

        elif action == "FORGET" and arg:
            n = mem_forget(arg)
            statuses.append(f"Forgot {n} memory entries matching '{arg}'.")

        elif action == "ADD_TASK" and arg:
            add_task(arg)
            statuses.append(f"Task added: {arg}")

    # Strip all action tags from the reply
    clean = _ACTION_RE.sub("", text).strip()
    return clean, statuses


# ── LLM call ─────────────────────────────────────────────────────────────────
async def ask_llm(user_id: int, message: str) -> str:
    # Check PC optimizer first — no API call needed
    pc = pc_match(message)
    if pc is not None:
        return pc

    system = _build_system()
    messages = [{"role": "system", "content": system}]
    messages += _get_history(user_id)
    messages.append({"role": "user", "content": message})

    if BACKEND == "openai":
        reply = await _call_openai(messages)
    elif BACKEND == "anthropic":
        reply = await _call_anthropic(system, messages[1:])
    else:
        reply = _call_ollama(messages)

    return reply


async def _call_openai(messages: list[dict]) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_KEY}",
                     "Content-Type": "application/json"},
            json={"model": MODEL, "messages": messages,
                  "max_tokens": 600, "temperature": 0.7},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


async def _call_anthropic(system: str, messages: list[dict]) -> str:
    merged: list[dict] = []
    for m in messages:
        if m["role"] == "system":
            continue
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1]["content"] += "\n" + m["content"]
        else:
            merged.append(dict(m))
    if not merged or merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": "(start)"})

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY,
                     "anthropic-version": "2023-06-01",
                     "Content-Type": "application/json"},
            json={"model": MODEL, "system": system, "messages": merged,
                  "max_tokens": 600},
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"].strip()


def _call_ollama(messages: list[dict]) -> str:
    import urllib.request
    url     = f"{OLLAMA_BASE}/api/chat"
    payload = json.dumps({"model": MODEL, "messages": messages, "stream": False}).encode()
    req     = urllib.request.Request(url, data=payload, method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read()).get("message", {}).get("content", "⚠ No response.").strip()
    except Exception as e:
        return f"⚠ Ollama error: {e}"


# ── TTS for Discord (optional audio attachments) ──────────────────────────────
async def tts_bytes(text: str) -> Optional[bytes]:
    """Generate speech bytes. Returns None if TTS is unavailable."""
    # 1. Cloned voice
    if has_sample():
        wav = vc_synth(text)
        if wav:
            return wav

    # 2. OpenAI TTS
    if OPENAI_KEY:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers={"Authorization": f"Bearer {OPENAI_KEY}",
                             "Content-Type": "application/json"},
                    json={"model": "tts-1", "input": text,
                          "voice": OPENAI_VOICE, "response_format": "mp3"},
                )
                if r.status_code == 200:
                    return r.content
        except Exception as e:
            log.warning(f"OpenAI TTS: {e}")

    return None


# ── Text chunker ──────────────────────────────────────────────────────────────
def _chunk(text: str, limit: int = 1900) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split = text.rfind("\n", 0, limit)
        if split == -1:
            split = text.rfind(" ", 0, limit)
        if split == -1:
            split = limit
        chunks.append(text[:split].rstrip())
        text = text[split:].lstrip()
    return chunks


# ── Heuristic memory extraction ───────────────────────────────────────────────
def _extract_facts(user_msg: str) -> None:
    patterns = [
        r"my name is ([A-Z][a-z]+)",
        r"I(?:'m| am) (?:a |an )?(.+?)(?:\.|,|$)",
        r"I (?:work|study) (?:at|in) (.+?)(?:\.|,|$)",
        r"I (?:like|love|hate|prefer) (.+?)(?:\.|,|$)",
        r"I (?:use|run|have) (.+?)(?:\.|,|$)",
    ]
    for pat in patterns:
        m = re.search(pat, user_msg, re.IGNORECASE)
        if m and len(m.group(1)) < 80:
            remember(m.group(0).strip().rstrip(".,"), mem_type="fact",
                     source="discord", importance=6)


# ── Bot setup ─────────────────────────────────────────────────────────────────
intents                 = discord.Intents.default()
intents.message_content = True
bot                     = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    if CHANNEL_ID:
        log.info(f"Dedicated channel: {CHANNEL_ID}")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening, name="your commands"
    ))


# ── Commands ──────────────────────────────────────────────────────────────────
@bot.command(name="clear")
async def cmd_clear(ctx: commands.Context):
    _history.pop(ctx.author.id, None)
    await ctx.reply("Conversation history cleared.", mention_author=False)


@bot.command(name="memory")
async def cmd_memory(ctx: commands.Context):
    mems = get_important_memories(limit=20)
    if not mems:
        await ctx.reply("Nothing stored yet.", mention_author=False)
        return
    lines = [f"**{m['type']}** — {m['content']}" for m in mems]
    text  = "**Stored memories:**\n" + "\n".join(lines)
    for chunk in _chunk(text):
        await ctx.reply(chunk, mention_author=False)


@bot.command(name="tasks")
async def cmd_tasks(ctx: commands.Context):
    tasks = get_open_tasks()
    if not tasks:
        await ctx.reply("No open tasks.", mention_author=False)
        return
    lines = [f"`[{t.get('priority','?')}]` {t['title']}" for t in tasks]
    await ctx.reply("**Open tasks:**\n" + "\n".join(lines), mention_author=False)


@bot.command(name="forget")
async def cmd_forget(ctx: commands.Context, *, fragment: str):
    n = mem_forget(fragment)
    await ctx.reply(f"Deleted {n} memory entries matching '{fragment}'.", mention_author=False)


@bot.command(name="wiki")
async def cmd_wiki(ctx: commands.Context, *, query: str):
    """Direct Wikipedia lookup."""
    async with ctx.typing():
        result = wiki_search(query)
    for chunk in _chunk(result):
        await ctx.reply(chunk, mention_author=False)


@bot.command(name="pc")
async def cmd_pc(ctx: commands.Context, *, command: str):
    """Run a PC optimizer command directly (e.g. !pc disk space)."""
    result = pc_match(command)
    if result is None:
        await ctx.reply(f"No local handler for '{command}'. Try: disk space, ram, cpu, temp files, …", mention_author=False)
    else:
        for chunk in _chunk(f"```\n{result}\n```"):
            await ctx.reply(chunk, mention_author=False)


@bot.command(name="voice")
async def cmd_voice(ctx: commands.Context, *, text: str = ""):
    """Reply as an audio file using the current TTS voice."""
    if not text:
        await ctx.reply("Usage: `!voice <text to speak>`", mention_author=False)
        return
    async with ctx.typing():
        audio = await tts_bytes(text)
    if audio:
        ext  = "mp3" if audio[:3] == b"ID3" or audio[:2] == b"\xff\xfb" else "wav"
        file = discord.File(io.BytesIO(audio), filename=f"stasis.{ext}")
        await ctx.reply(file=file, mention_author=False)
    else:
        await ctx.reply("TTS unavailable — add OPENAI_API_KEY or train a voice first.", mention_author=False)


# ── Main message handler ──────────────────────────────────────────────────────
@bot.event
async def on_message(msg: discord.Message):
    if msg.author.bot:
        return

    await bot.process_commands(msg)
    if msg.content.startswith("!"):
        return

    mentioned  = bot.user in msg.mentions
    in_channel = CHANNEL_ID and msg.channel.id == CHANNEL_ID
    if not (mentioned or in_channel):
        return

    text = re.sub(rf"<@!?{bot.user.id}>", "", msg.content).strip()
    if not text:
        await msg.reply("Yeah?", mention_author=False)
        return

    async with msg.channel.typing():
        try:
            raw_reply = await ask_llm(msg.author.id, text)
        except Exception as e:
            log.error(f"LLM error: {e}")
            await msg.reply(f"Something went wrong: {e}", mention_author=False)
            return

    # Process inline actions
    reply, statuses = process_actions(raw_reply)

    # Push history
    _push(msg.author.id, "user",      text)
    _push(msg.author.id, "assistant", reply or raw_reply)

    # Send any action status messages first (wiki results, etc.)
    for status in statuses:
        for chunk in _chunk(f"> {status}"):
            await msg.channel.send(chunk)

    # Send the main reply
    if reply:
        first = True
        for chunk in _chunk(reply):
            if first:
                await msg.reply(chunk, mention_author=False)
                first = False
            else:
                await msg.channel.send(chunk)

    # Background memory extraction
    asyncio.create_task(_bg_extract(text))


async def _bg_extract(user_msg: str) -> None:
    try:
        _extract_facts(user_msg)
    except Exception as e:
        log.warning(f"Memory extraction: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not set in .env")
        print("  1. https://discord.com/developers/applications")
        print("  2. Create a bot → copy token → add to .env")
        raise SystemExit(1)
    if not (OPENAI_KEY or ANTHROPIC_KEY):
        print("WARNING: No cloud API key — falling back to local Ollama.")
    bot.run(DISCORD_TOKEN, log_handler=None)
