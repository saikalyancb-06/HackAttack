"""Browser feature end to end, in a real Chromium with the Operator extension loaded.

    python tests/e2e/browser_agent_check.py      (backend :8000, agent server :8080 and UI :3000 must be running)

1. The Operator page (localhost:3000) gets a web goal -> the backend plans a browser.agent step.
2. The page asks the extension, which opens a NEW TAB next to it and runs the task there live.
3. The shop needs a login -> the agent pauses ("your turn"); this script plays the user: types the test login in the
   tab and clicks "I'm done - continue" in Operator.
4. The agent resumes, adds the item to the cart and ends; the step and run finish; the cart really has the item.
Test credentials below exist only in this local fixture shop (tests/e2e/fixtures/shop).
"""
from __future__ import annotations

import functools
import http.server
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
EXT = ROOT / "browser-extension"
SHOP = Path(__file__).resolve().parent / "fixtures" / "shop"
API = "http://localhost:8000"
TEST_USER, TEST_PASS = "test.user@penmart.test", "fixture-only-pass"  # fixture shop accepts any non-empty login


def serve_shop(port=8765):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SHOP))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main():
    serve_shop()
    pid = requests.post(f"{API}/projects", json={"name": "Browser check"}).json()["id"]
    tid = requests.post(f"{API}/projects/{pid}/threads", json={"title": "Browser check"}).json()["id"]
    profile = tempfile.mkdtemp(prefix="op_chrome_")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(profile, headless=False, viewport={"width": 1280, "height": 800},
                                                   args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"])
        ui = ctx.pages[0] if ctx.pages else ctx.new_page()
        ui.goto("http://localhost:3000")
        ui.wait_for_function("document.documentElement.dataset.operatorExt", timeout=20000)
        print("extension bridge ready:", ui.evaluate("document.documentElement.dataset.operatorExt"))
        ui.evaluate(f"localStorage.setItem('op-project', '{pid}')")
        ui.reload()
        ui.wait_for_function("document.documentElement.dataset.operatorExt", timeout=20000)
        time.sleep(3)
        goal = ("On the website http://127.0.0.1:8765/index.html find the best-selling pen and add it to the cart. "
                "Your choice on anything else.")
        rid = requests.post(f"{API}/projects/{pid}/threads/{tid}/messages", json={"text": goal, "mode": "task"}).json()["run_id"]
        print("run", rid)
        shop_tab, logged_in, t0 = None, False, time.time()
        while time.time() - t0 < 600:
            run = requests.get(f"{API}/projects/{pid}/runs/{rid}").json()
            for a in requests.get(f"{API}/projects/{pid}/approvals").json():
                if a["run_id"] == rid:
                    requests.post(f"{API}/projects/{pid}/approvals/{a['approval_id']}", json={"decision": "approve"})
            if shop_tab is None:
                shop_tab = next((pg for pg in ctx.pages if "127.0.0.1:8765" in (pg.url or "")), None)
                if shop_tab is None and any(e["kind"] == "browser_task" for e in requests.get(f"{API}/projects/{pid}/audit?run_id={rid}").json()):
                    time.sleep(2)  # tab events reach Playwright a moment after the extension creates the tab
                if shop_tab:
                    print("agent opened a new tab:", shop_tab.url)
            audit = requests.get(f"{API}/projects/{pid}/audit?run_id={rid}").json()
            if not logged_in and shop_tab and any(e["kind"] == "user_turn" for e in audit):
                q = next(e for e in audit if e["kind"] == "user_turn")["payload"]["question"]
                print("YOUR TURN asked:", q[:120])
                banner = shop_tab.evaluate("document.getElementById('__operator_agent_host__')?.shadowRoot?.querySelector('.banner')?.innerText || ''")
                print("in-page banner:", banner.replace("\n", " | ")[:160])
                # play the user: log in inside the tab (the fixture shows the form either as a modal or on login.html)
                shop_tab.locator("#loginUser:visible").first.fill(TEST_USER)
                shop_tab.locator("#loginPass:visible").first.fill(TEST_PASS)
                boxes = shop_tab.evaluate("[...document.getElementById('visiionary-overlay-root')?.shadowRoot?.querySelectorAll('.visi-box.outline') || []].map(b => b.innerText)")
                print("redaction boxes around typed data:", boxes)
                shop_tab.locator("#loginBtn:visible").first.click()
                time.sleep(1)
                ui.bring_to_front()
                ui.get_by_role("button", name="I'm done — continue").first.click()
                logged_in = True
            if run["status"] in ("done", "partial", "failed", "stopped", "refused") and not run.get("active"):
                break
            time.sleep(2)
        rep = requests.get(f"{API}/projects/{pid}/runs/{rid}/report").json()
        print("run:", run["status"], "|", [(s["step_id"], s["tool"], s["status"]) for s in run["plan"]])
        print(rep["summary"][:400])
        cart = ctx.new_page()
        cart.goto("http://127.0.0.1:8765/cart.html")
        items = cart.locator(".cart-item").all_inner_texts()
        print("cart:", items)
        ctx.close()
    shutil.rmtree(profile, ignore_errors=True)
    assert shop_tab is not None, "the agent never opened its own tab"
    assert logged_in, "the agent never handed the login to the user"
    assert items, "nothing was added to the cart"
    print("BROWSER AGENT CHECK PASSED")


if __name__ == "__main__":
    sys.exit(main())
