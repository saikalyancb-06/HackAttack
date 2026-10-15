"""Debug helper: start a task through the bridge and print the extension service-worker / page console."""
import functools, http.server, tempfile, threading, time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
EXT, SHOP = ROOT / "browser-extension", Path(__file__).resolve().parent / "fixtures" / "shop"
h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SHOP))
httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 8766), h)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(tempfile.mkdtemp(), headless=False,
                                               args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"])
    sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
    sw.on("console", lambda m: print("[SW]", m.text[:220]))
    ui = ctx.pages[0]
    ui.on("console", lambda m: print("[UI]", m.text[:220]) if "operator" in m.text.lower() or "error" in m.type else None)
    ui.goto("http://localhost:3000")
    ui.wait_for_function("document.documentElement.dataset.operatorExt", timeout=20000)
    ui.evaluate("""() => { window.__ev = []; window.addEventListener('message', e => { if (e.data && e.data.__operator === 'from-ext') window.__ev.push(e.data); });
      window.postMessage({__operator:'to-ext', reqId:'x1', type:'START_TASK', task:'add the blue pen to the cart', startUrl:'http://127.0.0.1:8766/index.html', runRef:{pid:'dbg', run_id:'r', step_id:'s1'}}, location.origin); }""")
    time.sleep(150)
    evs = ui.evaluate("window.__ev")
    print("events to page:", len(evs))
    for e in evs:
        ev = e.get("event") or {}
        if ev.get("log") and any(k in ev["log"] for k in ("[ACT]", "[YOUR TURN]", "[GOAL", "[VERIF", "[COMPLETED", "[PLANNER]", "FAILED")):
            print("  ", ev["log"][:200])
        if ev.get("kind") == "end":
            print("  END", {k: ev.get(k) for k in ("outcome", "status", "steps", "reason")})
    ctx.close()

