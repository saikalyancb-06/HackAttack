"""Headless browser (Playwright Chromium) for JS pages and screenshot evidence.

page_text(url) -> {"title","text"}
snapshot(url, out_path) -> {"title","text","screenshot","regions"}   screenshot is PII-blurred before it is saved
"""
from __future__ import annotations

import threading
from pathlib import Path

_lock = threading.Lock()  # one browser at a time keeps memory predictable on a laptop


def _dom_text_boxes(page) -> list[dict]:
    return page.evaluate("""() => {
        const out = []; const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        while (w.nextNode() && out.length < 3000) {
            const n = w.currentNode; const t = n.textContent.trim(); if (t.length < 5) continue;
            const r = document.createRange(); r.selectNodeContents(n); const b = r.getBoundingClientRect();
            if (b.width > 0 && b.height > 0 && b.top < window.innerHeight) out.push({text: t, x: b.left, y: b.top, w: b.width, h: b.height});
        }
        document.querySelectorAll('input').forEach(i => { const b = i.getBoundingClientRect();
            if (i.value && b.width > 0) out.push({text: (i.type === 'password' ? 'password: ' : '') + i.value, x: b.left, y: b.top, w: b.width, h: b.height}); });
        return out; }""")


def page_text(url: str, timeout_ms: int = 25000) -> dict:
    from playwright.sync_api import sync_playwright
    with _lock, sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            page = b.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            return {"title": page.title(), "text": page.inner_text("body")[:60000]}
        finally:
            b.close()


def snapshot(url: str, out_path: str, timeout_ms: int = 25000) -> dict:
    from playwright.sync_api import sync_playwright
    from backend.guard.visual_pii import redact_image
    with _lock, sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            page = b.new_page(viewport={"width": 1280, "height": 800})
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            png = page.screenshot(full_page=False)
            boxes = _dom_text_boxes(page)
            title, text = page.title(), page.inner_text("body")[:60000]
        finally:
            b.close()
    blurred, regions = redact_image(png, text_boxes=boxes)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_bytes(blurred)
    return {"title": title, "text": text, "screenshot": str(out_path), "regions": regions}


if __name__ == "__main__":
    import tempfile
    from backend import config
    demo = (config.ROOT / "demo-sites" / "login" / "index.html").resolve()
    out = Path(tempfile.mkdtemp()) / "shot.png"
    snap = snapshot(demo.as_uri(), str(out))
    assert out.exists() and snap["title"] is not None
    print(f"tools.browser self-check OK (title={snap['title']!r}, blurred_regions={len(snap['regions'])})")
