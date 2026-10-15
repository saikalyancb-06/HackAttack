"""Mid-plan suggestions: small, concrete plan patches offered to the user (Accept / Dismiss).

Patch shapes:
  {"insert_after": "<step_id>", "steps": [Step-like dicts]}
  {"save_workflow": {"name": str}}
"""
from __future__ import annotations

from backend.agent import events
from backend.ledger import db


def _offer(project_id: str, run_id: str, text: str, patch: dict) -> str:
    for s in db.list_suggestions(project_id, run_id):
        if s["text"] == db.mask_text(project_id, text) and s["status"] == "open":
            return s["sug_id"]
    sid = db.create_suggestion(project_id, run_id, text, patch)
    events.publish(project_id, "suggestion", {"sug_id": sid, "run_id": run_id, "text": text, "patch": patch})
    return sid


def after_failure(project_id: str, run_id: str, step: dict, error: str) -> None:
    if step.get("tool") == "office.excel_upsert_rows" and ("unavailable" in error.lower() or "simulated" in error.lower()):
        name = str((step.get("args") or {}).get("path", "results.xlsx")).rsplit(".", 1)[0].split("\\")[-1].split("/")[-1]
        _offer(project_id, run_id,
               "Excel is unavailable. Save a CSV copy of these rows now? The spreadsheet step stays queued and will be "
               "retried automatically when Excel is back, without duplicates.",
               {"insert_after": None, "before": step["step_id"], "steps": [{
                   "description": f"Save a CSV backup ({name}_backup.csv) while Excel is down", "tool": "files.write_csv",
                   "args": {"path": f"{name}_backup.csv", "rows": step["args"].get("rows")},
                   "route": "shell", "risk": "write", "produces": {"type": "file", "key": f"{name}_backup.csv"}}]})


def after_step(project_id: str, run_id: str, step: dict, output: dict | None) -> None:
    if step.get("tool") == "research.search" and isinstance(output, dict) and len(output.get("results", [])) < 3:
        q = str((step.get("args") or {}).get("query", ""))
        _offer(project_id, run_id, f"Only {len(output.get('results', []))} results for \"{q}\". Add a broader search?",
               {"insert_after": step["step_id"], "steps": [{
                   "description": "Broader web search", "tool": "research.search",
                   "args": {"query": " ".join(q.split()[:4]), "k": 10}, "route": "browser", "risk": "read"}]})


def after_run(project_id: str, run_id: str, run: dict) -> None:
    done = [s for s in run["plan"] if s["status"] in ("done", "reused")]
    if run["status"] == "done" and len(done) >= 2:
        _offer(project_id, run_id, "Save this plan as a reusable workflow so similar goals run instantly next time?",
               {"save_workflow": {"name": (run.get("goal_masked") or "workflow")[:60]}})
