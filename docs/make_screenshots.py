"""Capture README screenshots of the running app (UI :3000 + backend :8000).   python docs/make_screenshots.py"""
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

API, UI = "http://localhost:8000", "http://localhost:3000"
OUT = Path(__file__).resolve().parent / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)


def find_run(pred):
    for p in requests.get(API + "/projects").json():
        for t in requests.get(f"{API}/projects/{p['id']}/threads").json():
            for r in requests.get(f"{API}/projects/{p['id']}/threads/{t['id']}/runs").json():
                if pred(r):
                    return p["id"], t["id"], r["run_id"]
    return None


with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)

    def open_thread(pid, tid):
        pg.goto(UI)
        pg.evaluate(f"localStorage.setItem('op-project', '{pid}')")
        pg.goto(UI)
        time.sleep(2.5)
        pg.evaluate(f"""() => {{ const b = [...document.querySelectorAll('.left-panel button')].find(x => x.innerText && x.innerText.length > 3 && x.getAttribute('data-tid') === null && x.innerText.includes(document.title) ); }}""")
        titles = requests.get(f"{API}/projects/{pid}/threads").json()
        title = next(t["title"] for t in titles if t["id"] == tid)
        pg.get_by_text(title[:25], exact=False).first.click()
        time.sleep(3)

    # 1. home
    pg.goto(UI)
    time.sleep(2)
    pg.get_by_role("button", name="New task").click()
    time.sleep(1)
    pg.screenshot(path=str(OUT / "01-home.png"))

    # 2. a finished research run with "Here's what you asked for" + the full report
    hit = find_run(lambda r: "resort" in (r["goal_masked"] or "").lower() and r["status"] in ("done", "partial")) \
        or find_run(lambda r: r["status"] == "done")
    if hit:
        pid, tid, rid = hit
        requests.get(f"{API}/projects/{pid}/runs/{rid}/report", timeout=120)
        open_thread(pid, tid)
        pg.mouse.wheel(0, 4000)
        time.sleep(1)
        pg.screenshot(path=str(OUT / "02-run-result.png"))
        pg.get_by_role("button", name="View full report").last.click()
        time.sleep(2)
        pg.screenshot(path=str(OUT / "03-report.png"))
        pg.keyboard.press("Escape")
        for tab, name in (("Knowledge", "04-knowledge.png"), ("Audit", "05-audit.png")):
            pg.locator("button.tab", has_text=tab).first.click()
            time.sleep(2.5)
            pg.screenshot(path=str(OUT / name))

    # 6. intent & feasibility (a refused or partly-feasible goal)
    hit = find_run(lambda r: r["status"] in ("refused", "partial") and "flight" in (r["goal_masked"] or "").lower()) \
        or find_run(lambda r: r["status"] == "refused")
    if hit:
        open_thread(hit[0], hit[1])
        pg.screenshot(path=str(OUT / "06-safety.png"))

    # 7. dark theme
    pg.get_by_role("button", name="Toggle theme").click()
    time.sleep(1)
    pg.screenshot(path=str(OUT / "07-dark.png"))
    pg.get_by_role("button", name="Toggle theme").click()
    b.close()
print("screenshots:", sorted(p.name for p in OUT.glob("*.png")))
