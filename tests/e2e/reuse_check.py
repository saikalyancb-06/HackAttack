"""KG-first reuse: run a goal, save it as a workflow, run it again -> plan comes from the workflow and
finished read steps are reused from the knowledge graph instead of being executed again."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_scenarios import get, new_ctx, post, wait  # noqa: E402

GOAL = "Find the current stable version of Node.js and save the version number with the source link to node_version.txt"

if __name__ == "__main__":
    pid, tid = new_ctx("Reuse check")
    rid1 = post(f"/projects/{pid}/threads/{tid}/messages", {"text": GOAL, "mode": "task"})["run_id"]
    run1 = wait(pid, rid1)
    print("run 1:", run1["status"])
    sug = next(s for s in get(f"/projects/{pid}/suggestions?run_id={rid1}") if "workflow" in s["text"])
    saved = post(f"/projects/{pid}/suggestions/{sug['sug_id']}", {"accept": True})
    print("workflow saved:", saved.get("workflow"))
    rid2 = post(f"/projects/{pid}/threads/{tid}/messages", {"text": GOAL, "mode": "task"})["run_id"]
    run2 = wait(pid, rid2)
    plan_audit = next(a for a in get(f"/projects/{pid}/audit?run_id={rid2}") if a["kind"] == "plan")
    reused = [s["step_id"] for s in run2["plan"] if s["status"] == "reused"]
    print("run 2:", run2["status"], "| plan source:", plan_audit["payload"]["source"], "| reused steps:", reused)
    assert plan_audit["payload"]["source"].startswith("workflow:"), plan_audit
    assert reused, "expected at least one step reused from the knowledge graph"
    print(get(f"/projects/{pid}/runs/{rid2}/report")["summary"])
    print("REUSE CHECK PASSED")
