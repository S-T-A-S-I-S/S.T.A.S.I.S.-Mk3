"""
Browser controller for S.T.A.S.I.S. — powered by Playwright.

Keeps a single visible browser page open so STASIS can maintain
browsing context across commands (navigate → read → click → fill).

Install:
    pip install playwright
    playwright install chromium
"""
from __future__ import annotations

import os
from typing import Optional

# Lazy globals — created on first use
_playwright = None
_browser    = None
_page       = None

_HEADLESS = os.getenv("BROWSER_HEADLESS", "false").lower() == "true"
# BROWSER_CHANNEL: "chrome" | "msedge" | "" (blank = bundled Chromium)
_CHANNEL  = os.getenv("BROWSER_CHANNEL", "chrome")


async def _page_handle():
    """Return (or create) the shared browser page."""
    global _playwright, _browser, _page
    if _page is not None:
        return _page
    from playwright.async_api import async_playwright
    _playwright = await async_playwright().start()

    # Try requested channel first, fall back through msedge → bundled chromium
    for channel in ([_CHANNEL] if _CHANNEL else []) + ["msedge", ""]:
        try:
            kwargs = {"headless": _HEADLESS}
            if channel:
                kwargs["channel"] = channel
            _browser = await _playwright.chromium.launch(**kwargs)
            break
        except Exception:
            continue

    context = await _browser.new_context()
    _page   = await context.new_page()
    return _page


async def go(url: str) -> str:
    """Navigate to a URL. Prepends https:// if missing."""
    page = await _page_handle()
    try:
        if not url.startswith("http"):
            url = "https://" + url
        await page.goto(url, timeout=15_000, wait_until="domcontentloaded")
        return f"Navigated to {page.url}"
    except Exception as e:
        return f"Navigation failed: {e}"


async def search(query: str) -> str:
    """Open a Google search for query."""
    url = "https://www.google.com/search?q=" + query.replace(" ", "+")
    return await go(url)


async def click(text_or_selector: str) -> str:
    """Click the first element matching visible text, then fall back to CSS selector."""
    page = await _page_handle()
    try:
        await page.get_by_text(text_or_selector, exact=False).first.click(timeout=5_000)
        return f"Clicked '{text_or_selector}'"
    except Exception:
        pass
    try:
        await page.click(text_or_selector, timeout=5_000)
        return f"Clicked selector '{text_or_selector}'"
    except Exception as e:
        return f"Click failed: {e}"


async def fill(selector: str, value: str) -> str:
    """Type value into a form field (CSS selector or label text)."""
    page = await _page_handle()
    try:
        await page.fill(selector, value, timeout=5_000)
        return f"Filled '{selector}' with '{value}'"
    except Exception:
        pass
    try:
        await page.get_by_label(selector).fill(value, timeout=5_000)
        return f"Filled field '{selector}' with '{value}'"
    except Exception as e:
        return f"Fill failed: {e}"


async def read_page() -> str:
    """Return visible text from the current page (up to 3 000 chars)."""
    page = await _page_handle()
    try:
        raw: str = await page.evaluate("() => document.body.innerText")
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        text  = "\n".join(lines[:120])
        return text[:3_000] if len(text) > 3_000 else text
    except Exception as e:
        return f"Could not read page: {e}"


async def back() -> str:
    """Go back in browser history."""
    page = await _page_handle()
    try:
        await page.go_back(timeout=10_000)
        return f"Went back to {page.url}"
    except Exception as e:
        return f"Back failed: {e}"


async def scroll(direction: str = "down") -> str:
    """Scroll the page up or down."""
    page = await _page_handle()
    try:
        delta = 600 if direction.lower() != "up" else -600
        await page.evaluate(f"window.scrollBy(0, {delta})")
        return f"Scrolled {direction}."
    except Exception as e:
        return f"Scroll failed: {e}"


async def current_url() -> str:
    if _page is None:
        return "no browser open"
    return _page.url


async def close() -> None:
    """Shut down the browser (called on server shutdown)."""
    global _playwright, _browser, _page
    try:
        if _browser:
            await _browser.close()
        if _playwright:
            await _playwright.stop()
    except Exception:
        pass
    _playwright = _browser = _page = None
