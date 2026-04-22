"""
Wikipedia Knowledge — real-time lookups with SQLite caching.

STASIS uses this via the action tag:
    [ACTION:WIKI] search query

How it works
------------
1. Queries the Wikipedia REST API for the most current article summary.
2. Caches results in data/wiki_cache.db for 7 days.
3. Results are injected into the conversation so STASIS can answer
   factual questions accurately without baking stale knowledge into
   the model weights.

Offline mode (optional)
-----------------------
Run:  python wiki_knowledge.py --download
Downloads the latest Wikipedia Simple English abstracts (~120 MB).
Indexes them into data/wiki_offline.db using SQLite FTS5.
Subsequent queries hit the local index first before falling back to the API.

This gives STASIS access to ~230,000 article summaries entirely offline.
"""
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

_DATA = Path(__file__).parent / "data"
_DATA.mkdir(exist_ok=True)

_CACHE_DB   = _DATA / "wiki_cache.db"
_OFFLINE_DB = _DATA / "wiki_offline.db"
_CACHE_TTL  = 7 * 24 * 3600   # 7 days

_API_SEARCH  = "https://en.wikipedia.org/w/api.php"
_API_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
_UA          = "STASIS-Mk3/1.0 (personal AI assistant)"


# ── Cache database ────────────────────────────────────────────────────────────

def _cache_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_CACHE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wiki_cache (
            query      TEXT PRIMARY KEY,
            result     TEXT NOT NULL,
            fetched_at REAL NOT NULL
        )
    """)
    conn.commit()
    return conn


def _cache_get(query: str) -> Optional[str]:
    try:
        conn = _cache_conn()
        row = conn.execute(
            "SELECT result, fetched_at FROM wiki_cache WHERE query = ?",
            (query.lower().strip(),)
        ).fetchone()
        conn.close()
        if row and (time.time() - row[1]) < _CACHE_TTL:
            return row[0]
    except Exception:
        pass
    return None


def _cache_set(query: str, result: str) -> None:
    try:
        conn = _cache_conn()
        conn.execute(
            "INSERT OR REPLACE INTO wiki_cache (query, result, fetched_at) VALUES (?, ?, ?)",
            (query.lower().strip(), result, time.time())
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── Live Wikipedia API ────────────────────────────────────────────────────────

def _api_get(url: str) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _search_titles(query: str, limit: int = 5) -> list[str]:
    """Return article titles matching query."""
    params = urllib.parse.urlencode({
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": limit,
        "format": "json",
    })
    data = _api_get(f"{_API_SEARCH}?{params}")
    if not data:
        return []
    return [r["title"] for r in data.get("query", {}).get("search", [])]


def _fetch_summary(title: str) -> Optional[str]:
    """Fetch the introductory summary for a Wikipedia article."""
    url = _API_SUMMARY.format(title=urllib.parse.quote(title, safe=""))
    data = _api_get(url)
    if not data:
        return None
    extract = data.get("extract", "").strip()
    if not extract:
        return None
    # Trim to ~4 sentences for context injection
    sentences = re.split(r"(?<=[.!?])\s+", extract)
    return " ".join(sentences[:5])


# ── Offline index ─────────────────────────────────────────────────────────────

def _offline_search(query: str) -> Optional[str]:
    """Search the local offline Wikipedia index (if downloaded)."""
    if not _OFFLINE_DB.exists():
        return None
    try:
        conn = sqlite3.connect(str(_OFFLINE_DB))
        words = " OR ".join(w for w in query.split() if len(w) > 2)
        if not words:
            return None
        rows = conn.execute(
            "SELECT title, abstract FROM articles WHERE articles MATCH ? LIMIT 3",
            (words,)
        ).fetchall()
        conn.close()
        if rows:
            title, abstract = rows[0]
            return f"**{title}**: {abstract}"
    except Exception:
        pass
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def wiki_search(query: str) -> str:
    """
    Look up a topic on Wikipedia.
    Returns a concise summary string, or an error message.
    Called by [ACTION:WIKI] in the server and Discord bot.
    """
    query = query.strip()
    if not query:
        return "No query provided."

    # 1. Cache hit
    cached = _cache_get(query)
    if cached:
        return cached

    # 2. Offline index
    offline = _offline_search(query)
    if offline:
        _cache_set(query, offline)
        return offline

    # 3. Live Wikipedia API
    titles = _search_titles(query)
    if not titles:
        return f"No Wikipedia results for '{query}'."

    for title in titles[:3]:
        summary = _fetch_summary(title)
        if summary:
            result = f"**{title}**: {summary}"
            _cache_set(query, result)
            return result

    return f"Couldn't retrieve a summary for '{query}' from Wikipedia."


# ── Offline downloader (optional) ────────────────────────────────────────────

def download_offline(lang: str = "simple") -> None:
    """
    Download and index the Wikipedia abstracts for the given language edition.
    Default: Simple English Wikipedia (~120 MB compressed, ~230k articles).

    Usage:  python wiki_knowledge.py --download
    """
    import gzip
    import xml.etree.ElementTree as ET

    base = f"https://dumps.wikimedia.org/{lang}wiki/latest"
    dump_url = f"{base}/{lang}wiki-latest-abstract.xml.gz"
    gz_path  = _DATA / f"{lang}wiki-abstract.xml.gz"

    print(f"Downloading {dump_url} …")
    req = urllib.request.Request(dump_url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(gz_path, "wb") as f:
        total = 0
        while chunk := r.read(65536):
            f.write(chunk)
            total += len(chunk)
            print(f"\r  {total / 1e6:.1f} MB", end="", flush=True)
    print()

    print("Building offline index …")
    conn = sqlite3.connect(str(_OFFLINE_DB))
    conn.execute("DROP TABLE IF EXISTS articles")
    conn.execute("""
        CREATE VIRTUAL TABLE articles USING fts5(
            title, abstract,
            tokenize = 'porter ascii'
        )
    """)

    count = 0
    with gzip.open(gz_path, "rb") as f:
        for event, elem in ET.iterparse(f, events=("end",)):
            if elem.tag != "doc":
                continue
            title    = (elem.findtext("title")    or "").replace("Wikipedia: ", "")
            abstract = (elem.findtext("abstract") or "").strip()
            if title and abstract and len(abstract) > 40:
                conn.execute("INSERT INTO articles VALUES (?, ?)", (title, abstract))
                count += 1
                if count % 10_000 == 0:
                    conn.commit()
                    print(f"\r  Indexed {count:,} articles", end="", flush=True)
            elem.clear()

    conn.commit()
    conn.close()
    print(f"\nDone — {count:,} articles in {_OFFLINE_DB}")


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if "--download" in sys.argv:
        lang = "simple"
        for a in sys.argv:
            if a.startswith("--lang="):
                lang = a.split("=", 1)[1]
        download_offline(lang)
    else:
        query = " ".join(sys.argv[1:]) or "Python programming language"
        print(wiki_search(query))
