"""browser.agent: run a web task live in a new tab of the user's own browser.

The backend can't drive the browser itself; the Operator page (localhost:3000) can, through the Operator browser
extension. So this step publishes a `browser_task` event, the page asks the extension to open a tab next to it and
run the task there (visible, with the user able to step in to log in), and the page streams the extension's
progress back to POST /projects/{pid}/runs/{rid}/steps/{sid}/browser. This function blocks until the
extension reports the end of the task, the run is stopped, or the timeout passes.
"""
from __future__ import annotations

import threading
import time

from backend.agent import events
from backend.ledger import db

_lock = threading.Lock()
_pending: dict[tuple[str, str, str], dict] = {}   # (pid, rid, sid) -> {"event": Event, "result": dict|None, "state": dict}

NOT_CONNECTED = ("The Operator browser extension is not connected. Load it in Chrome (chrome://extensions → "
                 "Load unpacked → E:\\hackattackamc\\browser-extension) and keep the Operator page open, then retry.")


def run(project_id: str, run_id: str, step: dict, args: dict, should_stop=lambda: False, timeout_s: int = 1800) -> dict:
    sid = step.get("step_id", "")
    key = (project_id, run_id, sid)
    slot = {"event": threading.Event(), "result": None, "state": {}, "started": False}
    with _lock:
        _pending[key] = slot
    task = str(args.get("task") or step.get("description") or "").strip()
    start_url = str(args.get("start_url") or "").strip()
    events.publish(project_id, "browser_task", {"run_id": run_id, "step_id": sid, "task": task, "start_url": start_url})
    db.audit(project_id, run_id, "browser_task", {"step_id": sid, "task": task, "start_url": start_url})
    try:
        deadline = time.time() + timeout_s
        ack_deadline = time.time() + 25  # the page must confirm the tab was opened
        while time.time() < deadline:
            if slot["event"].wait(0.5):
                break
            if should_stop():
                events.publish(project_id, "browser_control", {"run_id": run_id, "step_id": sid, "command": "stop"})
                raise RuntimeError("stopped by user")
            if not slot["started"] and time.time() > ack_deadline:
                raise ConnectionError(NOT_CONNECTED)
        else:
            raise TimeoutError(f"the browser task did not finish within {timeout_s // 60} minutes")
        res = slot["result"] or {}
        if res.get("outcome") == "UNAVAILABLE":
            raise ConnectionError(res.get("reason") or NOT_CONNECTED)
        return res
    finally:
        with _lock:
            _pending.pop(key, None)


def report(project_id: str, run_id: str, step_id: str, body: dict) -> dict:
    """Progress from the page/extension: kind = started | state | end."""
    with _lock:
        slot = _pending.get((project_id, run_id, step_id))
    if not slot:
        return {"ok": False, "error": "no browser task is waiting for this step"}
    kind = body.get("kind")
    if kind == "started":
        slot["started"] = True
        db.update_step(project_id, run_id, step_id, evidence={"browser": "running", "tab_url": body.get("url")})
        events.publish(project_id, "step_update", {"run_id": run_id, "step": db.get_step(project_id, run_id, step_id)})
    elif kind == "state":
        slot["started"] = True
        st = body.get("state") or {}
        slot["state"] = st
        if body.get("log"):
            events.publish(project_id, "log", {"run_id": run_id, "text": db.mask_text(project_id, f"[browser] {body['log']}")[:400]})
            if str(body["log"]).startswith(("[ACT]", "[YOUR TURN]", "[RESUMED]", "[GOAL", "[HALT]", "[ACTION FAILED]")):
                db.audit(project_id, run_id, "browser_action", {"step_id": step_id, "text": str(body["log"])[:300],
                                                               "url": st.get("currentUrl")})
        if st.get("waitingForUser") and not slot.get("asked"):
            slot["asked"] = True
            db.audit(project_id, run_id, "user_turn", {"step_id": step_id, "question": st["waitingForUser"].get("question", "")})
        elif not st.get("waitingForUser"):
            slot["asked"] = False
    elif kind == "end":
        slot["started"] = True
        res = {k: body.get(k) for k in ("outcome", "status", "steps", "finalUrl", "finalTitle", "reason", "redactedCount", "history")}
        slot["result"] = db.mask_value(project_id, res)
        slot["event"].set()
    return {"ok": True}


def pending(project_id: str) -> list[dict]:
    """Browser tasks waiting for a page to pick them up (a page that reconnects re-dispatches these)."""
    with _lock:
        return [{"run_id": r, "step_id": s, "started": v["started"]} for (p, r, s), v in _pending.items() if p == project_id]
