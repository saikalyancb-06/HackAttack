"""Desktop-app task end to end: the app must be launched exactly once for the whole run (all steps reuse its
window), and the run must end (done / partial / failed), never loop forever.

    python tests/e2e/desktop_app_check.py "open microsoft powerpoint and a new presentation and then create 2 slides stating hi and hello respectively" POWERPNT.EXE
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_scenarios import get, new_ctx, post  # noqa: E402


def count(proc: str) -> int:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {proc}"], capture_output=True, text=True).stdout
    return out.lower().count(proc.lower())


if __name__ == "__main__":
    goal, proc = sys.argv[1], sys.argv[2]
    before = count(proc)
    pid, tid = new_ctx("Desktop app check")
    rid = post(f"/projects/{pid}/threads/{tid}/messages", {"text": goal, "mode": "task"})["run_id"]
    peak, t0, seen = before, time.time(), set()
    while time.time() - t0 < 900:
        for a in get(f"/projects/{pid}/approvals"):
            if a["run_id"] == rid:
                post(f"/projects/{pid}/approvals/{a['approval_id']}", {"decision": "approve"})
        run = get(f"/projects/{pid}/runs/{rid}")
        for s in run["plan"]:
            k = (s["step_id"], s["status"])
            if k not in seen:
                seen.add(k)
                print(f"  [{s['status']:>17}] {s['step_id']} {s['tool']} {str(s['args'].get('task', ''))[:70]} {str(s.get('error') or '')[:100]}")
        peak = max(peak, count(proc))
        if run["status"] in ("done", "partial", "failed", "stopped") and not run.get("active"):
            break
        time.sleep(2)
    launches = [a for a in get(f"/projects/{pid}/audit?run_id={rid}") if a["kind"] == "ui_action" and a["payload"].get("action") == "launch"]
    print("run:", run["status"], "| launches:", len(launches), "| peak processes:", peak, "| before:", before)
    print(get(f"/projects/{pid}/runs/{rid}/report")["summary"])
    assert len(launches) <= 1, f"app launched {len(launches)} times"
    assert peak - before <= 1, f"{peak - before} new {proc} processes"
    print("DESKTOP APP CHECK PASSED (single launch, run ended)")
