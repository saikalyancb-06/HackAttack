"""Saved workflows: a finished run's plan stored as YAML under workflows/<app>/ and as a Workflow node in the KG.
match(goal) finds a saved workflow by embedding similarity so a repeated goal skips planning (KG-first).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from backend import config
from backend.kg import rag, store
from backend.ledger import db

WF_DIR = config.ROOT / "workflows"
_THRESHOLD = 0.82


def _app_of(steps: list[dict]) -> str:
    tools = [s["tool"] for s in steps]
    if any(t.startswith("office.excel") for t in tools):
        return "excel"
    if any(t.startswith("office.") for t in tools):
        return "office"
    if any(t.startswith("files.") for t in tools):
        return "files"
    return "general"


def save(project_id: str, run_id: str, name: str | None = None) -> str:
    run = db.get_run(project_id, run_id)
    steps = [{k: s.get(k) for k in ("step_id", "description", "tool", "args", "foreach", "produces", "route", "risk") if s.get(k) is not None}
             for s in run["plan"] if s["status"] in ("done", "reused", "degraded")]
    name = re.sub(r"[^a-z0-9]+", "_", (name or run["goal_masked"]).lower()).strip("_")[:50] or run_id
    doc = {"name": name, "app": _app_of(steps), "intent": run["goal_masked"], "risk": max((s.get("risk", "read") for s in steps),
           key=["read", "write", "irreversible"].index), "steps": steps, "approved": True, "source_run": run_id}
    path = WF_DIR / doc["app"] / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    store.upsert_node(project_id, "Workflow", name, {"label": name[:60], "intent": run["goal_masked"], "path": str(path),
                                                      "steps": len(steps)})
    db.audit(project_id, run_id, "workflow_saved", {"name": name, "path": str(path)})
    return str(path)


def match(project_id: str, goal: str) -> dict | None:
    hits = [h for h in rag.similar(project_id, f"Workflow {goal}", k=5)]
    for h in hits:
        node = store.get_node(project_id, h["node_id"])
        if not node or node["type"] != "Workflow":
            continue
        score = _cos_goal(goal, node["props"].get("intent", ""))
        if score >= _THRESHOLD:
            p = Path(node["props"].get("path", ""))
            if p.exists():
                doc = yaml.safe_load(p.read_text(encoding="utf-8"))
                steps = [{**s, "status": "pending"} for s in doc.get("steps", [])]
                for s in steps:
                    s.pop("output", None)
                return {"name": doc["name"], "steps": steps, "score": score}
    return None


def _cos_goal(a: str, b: str) -> float:
    va, vb = rag.embed(a), rag.embed(b)
    return sum(x * y for x, y in zip(va, vb))
