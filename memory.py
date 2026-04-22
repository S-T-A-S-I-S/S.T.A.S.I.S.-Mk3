"""
S.T.A.S.I.S. Mk3 — Persistent Memory
SQLite + FTS5 backend (from Mk2), DB at data/stasis.db

Tables
------
  memories  — facts the AI learns (with importance scoring)
  tasks     — to-do items with priority / due date
  notes     — freeform notes tied to topics

All relevant memories are injected into every LLM prompt via build_context().
"""
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("stasis.memory")

DB_PATH = Path(__file__).parent / "data" / "stasis.db"


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            type         TEXT    NOT NULL DEFAULT 'fact',
            content      TEXT    NOT NULL,
            source       TEXT    DEFAULT '',
            importance   INTEGER DEFAULT 5,
            created_at   REAL    NOT NULL,
            last_accessed REAL,
            access_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            title        TEXT NOT NULL,
            description  TEXT DEFAULT '',
            priority     TEXT DEFAULT 'medium',
            status       TEXT DEFAULT 'open',
            due_date     TEXT DEFAULT '',
            project      TEXT DEFAULT '',
            tags         TEXT DEFAULT '[]',
            created_at   REAL NOT NULL,
            completed_at REAL
        );

        CREATE TABLE IF NOT EXISTS notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT DEFAULT '',
            content    TEXT NOT NULL,
            topic      TEXT DEFAULT '',
            tags       TEXT DEFAULT '[]',
            created_at REAL NOT NULL,
            updated_at REAL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            content, type, source,
            content='memories', content_rowid='id'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS task_fts USING fts5(
            title, description, project,
            content='tasks', content_rowid='id'
        );
    """)
    conn.close()


# ── Memories ──────────────────────────────────────────────────────────────────

def remember(content: str, mem_type: str = "fact", source: str = "",
             importance: int = 5) -> int:
    """Store a memory. Silently skips exact duplicates."""
    content = content.strip()
    if not content:
        return -1
    conn = _get_db()
    # Check for near-duplicate (exact match)
    existing = conn.execute(
        "SELECT id FROM memories WHERE content = ?", (content,)
    ).fetchone()
    if existing:
        conn.close()
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO memories (type, content, source, importance, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (mem_type, content, source, importance, time.time())
    )
    mem_id = cur.lastrowid
    conn.execute(
        "INSERT INTO memory_fts (rowid, content, type, source) VALUES (?, ?, ?, ?)",
        (mem_id, content, mem_type, source)
    )
    conn.commit()
    conn.close()
    log.debug(f"Stored [{mem_type}]: {content[:60]}")
    return mem_id


def _fts_query(raw: str) -> str:
    words = [w.replace('"', "").replace("'", "") for w in raw.split() if len(w) > 2]
    return " OR ".join(words[:6]) if words else ""


def recall(query: str, limit: int = 5) -> list[dict]:
    fts = _fts_query(query)
    if not fts:
        return []
    conn = _get_db()
    try:
        rows = conn.execute("""
            SELECT m.id, m.type, m.content, m.importance, m.created_at, m.access_count
            FROM memory_fts f
            JOIN memories m ON f.rowid = m.id
            WHERE memory_fts MATCH ?
            ORDER BY rank LIMIT ?
        """, (fts, limit)).fetchall()
        for r in rows:
            conn.execute(
                "UPDATE memories SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
                (time.time(), r["id"])
            )
        conn.commit()
    except Exception:
        rows = []
    conn.close()
    return [dict(r) for r in rows]


def get_important_memories(limit: int = 15) -> list[dict]:
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM memories ORDER BY importance DESC, access_count DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def forget(fragment: str) -> int:
    conn = _get_db()
    cur = conn.execute(
        "DELETE FROM memories WHERE content LIKE ?", (f"%{fragment}%",)
    )
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n


# ── Tasks ─────────────────────────────────────────────────────────────────────

def add_task(title: str, description: str = "", priority: str = "medium",
             due_date: str = "", project: str = "") -> int:
    conn = _get_db()
    cur = conn.execute(
        "INSERT INTO tasks (title, description, priority, due_date, project, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (title.strip(), description, priority, due_date, project, time.time())
    )
    task_id = cur.lastrowid
    conn.execute(
        "INSERT INTO task_fts (rowid, title, description, project) VALUES (?, ?, ?, ?)",
        (task_id, title, description, project)
    )
    conn.commit()
    conn.close()
    return task_id


def get_open_tasks(limit: int = 20) -> list[dict]:
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status IN ('open','in_progress') "
        "ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, "
        "due_date LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def complete_task(task_id: int) -> None:
    conn = _get_db()
    conn.execute(
        "UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ?",
        (time.time(), task_id)
    )
    conn.commit()
    conn.close()


# ── Notes ─────────────────────────────────────────────────────────────────────

def create_note(content: str, title: str = "", topic: str = "") -> int:
    now = time.time()
    conn = _get_db()
    cur = conn.execute(
        "INSERT INTO notes (title, content, topic, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (title, content, topic, now, now)
    )
    note_id = cur.lastrowid
    conn.commit()
    conn.close()
    return note_id


# ── Context builder ───────────────────────────────────────────────────────────

def build_context(user_message: str = "") -> str:
    """
    Returns a compact context block for the system prompt.
    Injects high-priority tasks + relevant + important memories.
    """
    parts: list[str] = []

    # High-priority tasks
    tasks = [t for t in get_open_tasks(limit=10) if t["priority"] == "high"]
    if tasks:
        lines = [f"  - [{t['priority']}] {t['title']}" +
                 (f" (due {t['due_date']})" if t.get("due_date") else "")
                 for t in tasks[:5]]
        parts.append("High-priority tasks:\n" + "\n".join(lines))

    # Relevant memories for current query
    if user_message and len(user_message) > 5:
        relevant = recall(user_message, limit=4)
        if relevant:
            parts.append("Relevant memories:\n" + "\n".join(
                f"  - [{m['type']}] {m['content']}" for m in relevant
            ))

    # Top important memories (always available)
    important = get_important_memories(limit=5)
    seen = {m["content"] for m in (relevant if "relevant" in dir() else [])}
    top = [m for m in important if m["content"] not in seen][:3]
    if top:
        parts.append("Key facts:\n" + "\n".join(f"  - {m['content']}" for m in top))

    return "\n\n".join(parts)


# Compatibility aliases used by discord_bot.py
def get_recent_memories(limit: int = 10) -> list[dict]:
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Auto-init ─────────────────────────────────────────────────────────────────
init_db()
