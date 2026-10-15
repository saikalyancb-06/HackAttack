"""Web research: search() and fetch(), both cached under data/cache/ so a flaky network degrades, not fails.

search(query, k=8) -> {"results": [{"title","url","snippet"}], "source": "tavily|ddgs|cache"}
fetch(url, max_chars=12000) -> {"url","title","text","source"}
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from backend import config
from backend.ledger.retry import NonRetryable

_SEARCH_CACHE = config.CACHE_DIR / "search"
_PAGE_CACHE = config.CACHE_DIR / "pages"
_SEARCH_CACHE.mkdir(parents=True, exist_ok=True)
_PAGE_CACHE.mkdir(parents=True, exist_ok=True)
_BLOCKED = re.compile(r"(?i)request blocked|you have been blocked|access denied|are you a robot|verify you are human|"
                      r"enable javascript and cookies|captcha|cloudflare ray id|unusual traffic|attention required")
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def _key(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def _cache_get(d: Path, key: str, max_age: float | None) -> dict | None:
    p = d / f"{key}.json"
    if p.exists() and (max_age is None or time.time() - p.stat().st_mtime < max_age):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _cache_put(d: Path, key: str, data: dict) -> None:
    (d / f"{key}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _tavily(query: str, k: int) -> list[dict]:
    import requests
    r = requests.post("https://api.tavily.com/search", json={"api_key": config.TAVILY_API_KEY, "query": query,
                                                              "max_results": k}, timeout=25)
    r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")[:500]}
            for x in r.json().get("results", [])]


def _ddgs(query: str, k: int) -> list[dict]:
    try:
        from ddgs import DDGS  # type: ignore
    except Exception:
        from duckduckgo_search import DDGS  # type: ignore
    with DDGS(timeout=20) as d:
        hits = list(d.text(query, max_results=k))
    return [{"title": h.get("title", ""), "url": h.get("href") or h.get("url", ""), "snippet": h.get("body", "")[:500]}
            for h in hits]


def search(query: str, k: int = 8) -> dict:
    query = (query or "").strip()
    if not query:
        raise ValueError("empty search query")
    key = _key(f"{query}|{k}")
    fresh = _cache_get(_SEARCH_CACHE, key, max_age=6 * 3600)
    if fresh:
        return {**fresh, "source": "cache"}
    errors = []
    providers = ([("tavily", _tavily)] if config.TAVILY_API_KEY else []) + [("ddgs", _ddgs)]
    for name, fn in providers:
        for attempt in range(2):
            try:
                results = [r for r in fn(query, k) if r.get("url")]
                if results:
                    data = {"query": query, "results": results[:k]}
                    _cache_put(_SEARCH_CACHE, key, data)
                    return {**data, "source": name}
                errors.append(f"{name}: no results")
                break
            except Exception as e:
                errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
                time.sleep(1.0 + attempt)
    stale = _cache_get(_SEARCH_CACHE, key, max_age=None)
    if stale:
        return {**stale, "source": "cache-stale"}
    raise RuntimeError("web search failed: " + "; ".join(errors))


def _html_to_text(html: str) -> tuple[str, str]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    for t in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "form", "iframe"]):
        t.decompose()
    text = soup.get_text("\n")
    text = re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t\r\f\v]+", " ", text)).strip()
    return title, text


def fetch(url: str, max_chars: int = 12000) -> dict:
    if not re.match(r"^https?://", url or ""):
        raise ValueError(f"not a web URL: {url!r}")
    key = _key(url)
    cached = _cache_get(_PAGE_CACHE, key, max_age=24 * 3600)
    if cached:
        return {**cached, "text": cached["text"][:max_chars], "source": "cache"}
    title, text, err = "", "", None
    try:
        import requests
        r = requests.get(url, headers={"User-Agent": _UA, "Accept-Language": "en"}, timeout=12)
        r.raise_for_status()
        if "html" in r.headers.get("content-type", "html"):
            title, text = _html_to_text(r.text)
        else:
            text = r.text
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:150]}"
    if len(text) < 400 and not (err and "timed out" in err.lower()):  # JS-heavy page: try a real browser
        try:
            from backend.tools import browser
            snap = browser.page_text(url)
            if len(snap.get("text", "")) > len(text):
                title, text, err = snap.get("title", title), snap["text"], None
        except Exception as e:
            err = err or f"browser: {str(e)[:150]}"
    if not text:
        stale = _cache_get(_PAGE_CACHE, key, max_age=None)
        if stale:
            return {**stale, "source": "cache-stale"}
        raise RuntimeError(f"could not fetch {url}: {err}")
    if _BLOCKED.search(f"{title}\n{text[:1500]}") and len(text) < 6000:
        raise NonRetryable(f"{url} blocks automated access (bot wall / captcha); use search snippets or another source")
    data = {"url": url, "title": title, "text": text[:60000]}
    _cache_put(_PAGE_CACHE, key, data)
    return {**data, "text": text[:max_chars], "source": "web"}


if __name__ == "__main__":
    _cache_put(_SEARCH_CACHE, _key("selfcheck query|3"), {"query": "selfcheck query", "results": [{"title": "t", "url": "http://x", "snippet": "s"}]})
    assert search("selfcheck query", k=3)["source"] == "cache"
    t, body = _html_to_text("<html><head><title>T</title><script>x()</script></head><body><p>Hello</p><p>World</p></body></html>")
    assert t == "T" and "Hello" in body and "x()" not in body
    try:
        fetch("ftp://nope")
        raise AssertionError
    except ValueError:
        pass
    print("tools.research self-check OK")
