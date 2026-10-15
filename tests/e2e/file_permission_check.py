"""Feature: the agent asks before writing on the user's device.

1. Deny the first write -> the step is rejected, the file is NOT created.
2. Run again, choose "Allow all writes this run" on the first card -> later writes in that run go through
   without asking again, and the files exist.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_scenarios import get, new_ctx, post  # noqa: E402

API_WAIT = 240


def wait_for(pid, rid, pred, timeout=API_WAIT):
    t0 = time.time()
    while time.time() - t0 < timeout:
        run = get(f"/projects/{pid}/runs/{rid}")
        aps = [a for a in get(f"/projects/{pid}/approvals") if a["run_id"] == rid]
        out = pred(run, aps)
        if out:
            return out
        time.sleep(1.5)
    raise TimeoutError(f"{rid}: {run['status']} {[s['status'] for s in run['plan']]}")


if __name__ == "__main__":
    pid, tid = new_ctx("File permission check")
    goal = "Write a file perm_a.txt containing the word alpha, then write a second file perm_b.txt containing the word beta"

    # 1) deny
    rid = post(f"/projects/{pid}/threads/{tid}/messages", {"text": goal, "mode": "task"})["run_id"]
    ap = wait_for(pid, rid, lambda r, a: next((x for x in a if x["kind"] == "file_write"), None))
    print("permission card:", ap["summary"]["operation"], [t["path"] for t in ap["summary"]["targets"]])
    target = Path(ap["summary"]["targets"][0]["path"])
    assert not target.exists(), "file written before permission"
    def deny_all(r, a):  # every write in this run asks separately; deny each one
        for x in a:
            post(f"/projects/{pid}/approvals/{x['approval_id']}", {"decision": "reject"})
        return r if r["status"] in ("done", "partial", "failed") and not r.get("active") and not a else None
    run = wait_for(pid, rid, deny_all)
    print("after deny:", run["status"], [(s["step_id"], s["status"]) for s in run["plan"]])
    assert all(s["status"] in ("rejected", "skipped") for s in run["plan"]) and not target.exists()
    assert not list(target.parent.glob("perm_*.txt")), "a denied file was written anyway"

    # 2) allow all writes for the run
    rid = post(f"/projects/{pid}/threads/{tid}/messages", {"text": goal, "mode": "task"})["run_id"]
    ap = wait_for(pid, rid, lambda r, a: next((x for x in a if x["kind"] == "file_write"), None))
    post(f"/projects/{pid}/approvals/{ap['approval_id']}", {"decision": "approve", "scope": "run"})
    asked = []

    def finished(r, a):
        asked.extend(x["approval_id"] for x in a if x["kind"] == "file_write" and x["approval_id"] not in asked)
        for x in a:  # anything else (shouldn't happen) is approved so the test can finish
            post(f"/projects/{pid}/approvals/{x['approval_id']}", {"decision": "approve"})
        return r if r["status"] in ("done", "partial", "failed") and not r.get("active") else None
    run = wait_for(pid, rid, finished)
    print("after allow-all:", run["status"], [(s["step_id"], s["tool"], s["status"]) for s in run["plan"]], "| extra prompts:", len(asked))
    assert run["status"] == "done" and not asked, (run["status"], asked)
    files = list((Path(target).parent).glob("perm_*.txt"))
    print("files:", [f.name for f in files])
    assert len(files) >= 2
    print("FILE PERMISSION CHECK PASSED")
