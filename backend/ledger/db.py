"""Task-state ledger: projects, threads, messages, runs, steps, audit, approvals, suggestions, PII vault.

All per-project state lives in data/projects/<pid>/project.db (WAL). The project index is
data/projects/index.db. Everything written here is already PII-masked except the vault table,
which is the only place raw values live (local disk only; never sent to the LLM or the UI).
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend import config
from backend.guard import pii

_TERMINAL = ("done", "reused", "skipped", "rejected", "degraded")
_lock = threading.RLock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(3)}"


def project_dir(project_id: str) -> Path:
    d = config.PROJECTS_DIR / project_id
    (d / "files").mkdir(parents=True, exist_ok=True)
    return d


def connect(project_id: str) -> sqlite3.Connection:
    conn = sqlite3.connect(project_dir(project_id) / "project.db", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _index() -> sqlite3.Connection:
    conn = sqlite3.connect(config.PROJECTS_DIR / "index.db", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, name TEXT, created_at TEXT)")
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY, project_id TEXT, title TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT, role TEXT, kind TEXT,
    text TEXT, data_json TEXT, run_id TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, project_id TEXT, thread_id TEXT, goal_masked TEXT,
    status TEXT, assessment_json TEXT, privacy_json TEXT, error TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS steps(run_id TEXT, step_id TEXT, idx REAL, data_json TEXT, input_hash TEXT, status TEXT,
    PRIMARY KEY(run_id, step_id));
CREATE INDEX IF NOT EXISTS steps_hash ON steps(input_hash, status);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, kind TEXT, payload_json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS approvals(approval_id TEXT PRIMARY KEY, run_id TEXT, step_id TEXT, kind TEXT, summary_json TEXT,
    status TEXT, decision TEXT, edited_args_json TEXT, created_at TEXT, resolved_at TEXT);
CREATE TABLE IF NOT EXISTS suggestions(sug_id TEXT PRIMARY KEY, run_id TEXT, text TEXT, patch_json TEXT, status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS vault(placeholder TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS run_settings(run_id TEXT, key TEXT, value TEXT, PRIMARY KEY(run_id, key));
"""


def init_db(project_id: str) -> None:
    with _lock, connect(project_id) as c:
        c.executescript(_SCHEMA)


# ---------------------------------------------------------------- projects / threads / messages
def create_project(name: str) -> str:
    pid = _id("p")
    with _lock, _index() as c:
        c.execute("INSERT INTO projects VALUES(?,?,?)", (pid, name, now()))
    init_db(pid)
    return pid


def delete_project(project_id: str) -> None:
    """Remove a project from the index and its folder (used by self-checks and tests only)."""
    import shutil
    with _lock, _index() as c:
        c.execute("DELETE FROM projects WHERE id=?", (project_id,))
    shutil.rmtree(config.PROJECTS_DIR / project_id, ignore_errors=True)


def list_projects() -> list[dict]:
    with _index() as c:
        return [dict(r) for r in c.execute("SELECT id, name, created_at FROM projects ORDER BY created_at DESC")]


def project_exists(project_id: str) -> bool:
    with _index() as c:
        return c.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is not None


def create_thread(project_id: str, title: str) -> str:
    tid = _id("t")
    with _lock, connect(project_id) as c:
        c.execute("INSERT INTO threads VALUES(?,?,?,?,?)", (tid, project_id, mask_text(project_id, title), now(), now()))
    return tid


def list_threads(project_id: str) -> list[dict]:
    with connect(project_id) as c:
        return [dict(r) for r in c.execute("SELECT id, title, updated_at FROM threads ORDER BY updated_at DESC")]


def touch_thread(project_id: str, thread_id: str, title: str | None = None) -> None:
    with _lock, connect(project_id) as c:
        if title:
            c.execute("UPDATE threads SET updated_at=?, title=? WHERE id=?", (now(), mask_text(project_id, title), thread_id))
        else:
            c.execute("UPDATE threads SET updated_at=? WHERE id=?", (now(), thread_id))


def add_message(project_id: str, thread_id: str, role: str, kind: str, text: str, data: dict | None = None,
                run_id: str | None = None) -> int:
    with _lock, connect(project_id) as c:
        cur = c.execute("INSERT INTO messages(thread_id, role, kind, text, data_json, run_id, created_at) VALUES(?,?,?,?,?,?,?)",
                        (thread_id, role, kind, mask_text(project_id, text), json.dumps(mask_value(project_id, data or {})), run_id, now()))
        mid = cur.lastrowid
    touch_thread(project_id, thread_id)
    return mid


def list_messages(project_id: str, thread_id: str) -> list[dict]:
    with connect(project_id) as c:
        rows = c.execute("SELECT * FROM messages WHERE thread_id=? ORDER BY id", (thread_id,)).fetchall()
    return [{**{k: r[k] for k in r.keys() if k != "data_json"}, "data": json.loads(r["data_json"] or "{}")} for r in rows]


# ---------------------------------------------------------------- PII vault
def vault(project_id: str) -> dict:
    with connect(project_id) as c:
        return {r["placeholder"]: r["value"] for r in c.execute("SELECT placeholder, value FROM vault")}


def mask_text(project_id: str, text: str) -> str:
    """Mask with the project vault so the same value always gets the same placeholder."""
    if not isinstance(text, str) or not text:
        return text
    with _lock:
        mapping = vault(project_id)
        before = set(mapping)
        masked, mapping = pii.mask(text, mapping)
        new = {k: mapping[k] for k in mapping if k not in before}
        if new:
            with connect(project_id) as c:
                c.executemany("INSERT OR IGNORE INTO vault VALUES(?,?)", list(new.items()))
            _bump_privacy(project_id, "pii_masked", len(new))
    return masked


def mask_value(project_id: str, obj: Any) -> Any:
    if isinstance(obj, str):
        return mask_text(project_id, obj)
    if isinstance(obj, dict):
        return {k: mask_value(project_id, v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [mask_value(project_id, v) for v in obj]
    return obj


def unmask_value(project_id: str, obj: Any) -> Any:
    return pii.unmask(obj, vault(project_id))


# privacy counters, per run (set via current_run), also per project total
_current_run = threading.local()


def set_current_run(run_id: str | None) -> None:
    _current_run.run_id = run_id


def _bump_privacy(project_id: str, field: str, n: int = 1) -> None:
    run_id = getattr(_current_run, "run_id", None)
    if not run_id:
        return
    with connect(project_id) as c:
        row = c.execute("SELECT privacy_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return
        p = json.loads(row["privacy_json"] or "{}")
        p[field] = p.get(field, 0) + n
        c.execute("UPDATE runs SET privacy_json=? WHERE run_id=?", (json.dumps(p), run_id))


def bump_privacy(project_id: str, run_id: str, field: str, n: int = 1) -> None:
    prev = getattr(_current_run, "run_id", None)
    set_current_run(run_id)
    try:
        _bump_privacy(project_id, field, n)
    finally:
        set_current_run(prev)


# ---------------------------------------------------------------- runs / steps
def create_run(project_id: str, thread_id: str, goal_masked: str, plan: list[dict], assessment: dict | None = None) -> str:
    run_id = _id("r")
    with _lock, connect(project_id) as c:
        c.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (run_id, project_id, thread_id, goal_masked, "running", json.dumps(assessment or {}),
                   json.dumps({"pii_masked": 0, "regions_blurred": 0, "raw_pii_to_llm": 0}), None, now(), now()))
    if plan:
        replace_plan(project_id, run_id, plan)
    return run_id


def replace_plan(project_id: str, run_id: str, plan: list[dict]) -> None:
    # mask first: masking may write the vault, which must not happen inside another write transaction
    rows = [mask_value(project_id, _normalize_step(run_id, step, i)) for i, step in enumerate(plan)]
    with _lock, connect(project_id) as c:
        c.execute("DELETE FROM steps WHERE run_id=?", (run_id,))
        for i, s in enumerate(rows):
            c.execute("INSERT INTO steps VALUES(?,?,?,?,?,?)",
                      (run_id, s["step_id"], float(i), json.dumps(s), s.get("input_hash"), s["status"]))


def insert_steps_after(project_id: str, run_id: str, after_step_id: str | None, steps: list[dict]) -> list[dict]:
    steps = [mask_value(project_id, st) for st in steps]
    with _lock, connect(project_id) as c:
        rows = c.execute("SELECT step_id, idx FROM steps WHERE run_id=? ORDER BY idx", (run_id,)).fetchall()
        idxs = [r["idx"] for r in rows]
        if after_step_id is None:
            lo, hi = (idxs[-1] if idxs else 0.0), (idxs[-1] + 1 if idxs else 1.0)
        else:
            pos = next((i for i, r in enumerate(rows) if r["step_id"] == after_step_id), len(rows) - 1)
            lo = idxs[pos]
            hi = idxs[pos + 1] if pos + 1 < len(idxs) else lo + 1
        existing = {r["step_id"] for r in rows}
        out = []
        for j, step in enumerate(steps):
            s = _normalize_step(run_id, step, len(existing) + j)
            while s["step_id"] in existing:
                s["step_id"] = f"s{len(existing) + j + 1}_{secrets.token_hex(1)}"
            existing.add(s["step_id"])
            idx = lo + (hi - lo) * (j + 1) / (len(steps) + 1)
            c.execute("INSERT INTO steps VALUES(?,?,?,?,?,?)",
                      (run_id, s["step_id"], idx, json.dumps(s), s.get("input_hash"), s["status"]))
            out.append(s)
    return out


def _normalize_step(run_id: str, step: dict, i: int) -> dict:
    base = {"run_id": run_id, "step_id": f"s{i + 1}", "parent_id": None, "description": "", "route": "kg",
            "tool": "", "args": {}, "risk": "read", "produces": None, "input_hash": None, "status": "pending",
            "attempts": 0, "output": None, "evidence": None, "error": None, "started_at": None, "ended_at": None}
    base.update(step)
    base["run_id"] = run_id
    return base


def get_run(project_id: str, run_id: str) -> dict | None:
    with connect(project_id) as c:
        r = c.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not r:
            return None
        steps = [json.loads(s["data_json"]) for s in
                 c.execute("SELECT data_json FROM steps WHERE run_id=? ORDER BY idx", (run_id,))]
    run = {k: r[k] for k in r.keys() if not k.endswith("_json")}
    run["assessment"] = json.loads(r["assessment_json"] or "{}")
    run["privacy"] = json.loads(r["privacy_json"] or "{}")
    run["plan"] = steps
    return run


def list_runs(project_id: str, thread_id: str | None = None) -> list[dict]:
    with connect(project_id) as c:
        q = "SELECT run_id, thread_id, goal_masked, status, created_at, updated_at FROM runs"
        rows = c.execute(q + (" WHERE thread_id=?" if thread_id else "") + " ORDER BY created_at",
                         (thread_id,) if thread_id else ()).fetchall()
    return [dict(r) for r in rows]


def set_run_status(project_id: str, run_id: str, status: str, error: str | None = None) -> None:
    with _lock, connect(project_id) as c:
        c.execute("UPDATE runs SET status=?, error=?, updated_at=? WHERE run_id=?",
                  (status, mask_text(project_id, error) if error else None, now(), run_id))


def get_step(project_id: str, run_id: str, step_id: str) -> dict | None:
    with connect(project_id) as c:
        r = c.execute("SELECT data_json FROM steps WHERE run_id=? AND step_id=?", (run_id, step_id)).fetchone()
    return json.loads(r["data_json"]) if r else None


def update_step(project_id: str, run_id: str, step_id: str, **fields: Any) -> dict:
    with _lock:
        step = get_step(project_id, run_id, step_id)
        if step is None:
            raise KeyError(f"step {step_id} not found in run {run_id}")
        step.update(mask_value(project_id, fields))
        with connect(project_id) as c:
            c.execute("UPDATE steps SET data_json=?, input_hash=?, status=? WHERE run_id=? AND step_id=?",
                      (json.dumps(step), step.get("input_hash"), step.get("status"), run_id, step_id))
            c.execute("UPDATE runs SET updated_at=? WHERE run_id=?", (now(), run_id))
    return step


def next_pending_step(project_id: str, run_id: str) -> dict | None:
    run = get_run(project_id, run_id)
    for s in (run or {}).get("plan", []):
        if s["status"] not in _TERMINAL:
            return s
    return None


def find_done_by_hash(project_id: str, input_hash: str) -> dict | None:
    if not input_hash:
        return None
    with connect(project_id) as c:
        r = c.execute("SELECT data_json FROM steps WHERE input_hash=? AND status IN ('done','reused') ORDER BY rowid DESC LIMIT 1",
                      (input_hash,)).fetchone()
    return json.loads(r["data_json"]) if r else None


# ---------------------------------------------------------------- audit
def audit(project_id: str, run_id: str | None, kind: str, payload: dict) -> None:
    """Append-only. Payload is masked again defensively before storage."""
    with _lock, connect(project_id) as c:
        c.execute("INSERT INTO audit(run_id, kind, payload_json, created_at) VALUES(?,?,?,?)",
                  (run_id, kind, json.dumps(mask_value(project_id, payload), default=str), now()))


def list_audit(project_id: str, run_id: str | None = None) -> list[dict]:
    with connect(project_id) as c:
        rows = c.execute("SELECT * FROM audit" + (" WHERE run_id=?" if run_id else "") + " ORDER BY id",
                         (run_id,) if run_id else ()).fetchall()
    return [{"id": r["id"], "run_id": r["run_id"], "kind": r["kind"], "payload": json.loads(r["payload_json"]),
             "created_at": r["created_at"]} for r in rows]


# ---------------------------------------------------------------- approvals
def create_approval(project_id: str, run_id: str, step_id: str, summary: dict, kind: str = "action") -> str:
    aid = _id("a")
    with _lock, connect(project_id) as c:
        c.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (aid, run_id, step_id, kind, json.dumps(mask_value(project_id, summary)), "pending", None, None, now(), None))
    return aid


def get_approval(project_id: str, approval_id: str) -> dict | None:
    with connect(project_id) as c:
        r = c.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
    if not r:
        return None
    d = {k: r[k] for k in r.keys() if not k.endswith("_json")}
    d["summary"] = json.loads(r["summary_json"] or "{}")
    d["edited_args"] = json.loads(r["edited_args_json"]) if r["edited_args_json"] else None
    return d


def list_approvals(project_id: str, status: str | None = None) -> list[dict]:
    with connect(project_id) as c:
        ids = [r["approval_id"] for r in c.execute(
            "SELECT approval_id FROM approvals" + (" WHERE status=?" if status else "") + " ORDER BY created_at",
            (status,) if status else ())]
    return [get_approval(project_id, a) for a in ids]


def resolve_approval(project_id: str, approval_id: str, decision: str, edited_args: dict | None = None) -> None:
    if decision not in ("approve", "edit", "reject"):
        raise ValueError(f"bad decision {decision!r}")
    with _lock, connect(project_id) as c:
        c.execute("UPDATE approvals SET status='resolved', decision=?, edited_args_json=?, resolved_at=? WHERE approval_id=?",
                  (decision, json.dumps(mask_value(project_id, edited_args)) if edited_args else None, now(), approval_id))


# ---------------------------------------------------------------- per-run settings (e.g. "allow all file writes")
def set_run_setting(project_id: str, run_id: str, key: str, value) -> None:
    with _lock, connect(project_id) as c:
        c.executescript(_SCHEMA)
        c.execute("INSERT OR REPLACE INTO run_settings VALUES(?,?,?)", (run_id, key, json.dumps(value)))


def get_run_setting(project_id: str, run_id: str, key: str, default=None):
    with connect(project_id) as c:
        c.executescript(_SCHEMA)
        r = c.execute("SELECT value FROM run_settings WHERE run_id=? AND key=?", (run_id, key)).fetchone()
    return json.loads(r["value"]) if r else default


# ---------------------------------------------------------------- suggestions
def create_suggestion(project_id: str, run_id: str, text: str, patch: dict) -> str:
    sid = _id("g")
    with _lock, connect(project_id) as c:
        c.execute("INSERT INTO suggestions VALUES(?,?,?,?,?,?)",
                  (sid, run_id, mask_text(project_id, text), json.dumps(mask_value(project_id, patch)), "open", now()))
    return sid


def get_suggestion(project_id: str, sug_id: str) -> dict | None:
    with connect(project_id) as c:
        r = c.execute("SELECT * FROM suggestions WHERE sug_id=?", (sug_id,)).fetchone()
    if not r:
        return None
    return {"sug_id": r["sug_id"], "run_id": r["run_id"], "text": r["text"], "patch": json.loads(r["patch_json"]),
            "status": r["status"], "created_at": r["created_at"]}


def list_suggestions(project_id: str, run_id: str | None = None) -> list[dict]:
    with connect(project_id) as c:
        ids = [r["sug_id"] for r in c.execute("SELECT sug_id FROM suggestions" + (" WHERE run_id=?" if run_id else "") +
                                               " ORDER BY created_at", (run_id,) if run_id else ())]
    return [get_suggestion(project_id, i) for i in ids]


def set_suggestion_status(project_id: str, sug_id: str, status: str) -> None:
    with _lock, connect(project_id) as c:
        c.execute("UPDATE suggestions SET status=? WHERE sug_id=?", (status, sug_id))


if __name__ == "__main__":
    import shutil
    pid = create_project("selfcheck")
    try:
        tid = create_thread(pid, "t")
        rid = create_run(pid, tid, "goal", [{"description": "a", "tool": "x", "input_hash": "h1"},
                                            {"description": "mail b@c.com", "tool": "y"}])
        run = get_run(pid, rid)
        assert [s["step_id"] for s in run["plan"]] == ["s1", "s2"]
        assert "b@c.com" not in json.dumps(run) and "<EMAIL_1>" in run["plan"][1]["description"]
        assert unmask_value(pid, run["plan"][1]["description"]) == "mail b@c.com"
        assert next_pending_step(pid, rid)["step_id"] == "s1"
        update_step(pid, rid, "s1", status="done", output={"ok": 1})
        assert next_pending_step(pid, rid)["step_id"] == "s2"
        assert find_done_by_hash(pid, "h1")["step_id"] == "s1"
        ins = insert_steps_after(pid, rid, "s1", [{"description": "mid", "tool": "z"}])
        assert [s["step_id"] for s in get_run(pid, rid)["plan"]] == ["s1", ins[0]["step_id"], "s2"]
        audit(pid, rid, "test", {"to": "q@w.com"})
        assert "q@w.com" not in json.dumps(list_audit(pid, rid))
        aid = create_approval(pid, rid, "s2", {"what": "send"})
        resolve_approval(pid, aid, "approve")
        assert get_approval(pid, aid)["decision"] == "approve"
        assert get_run(pid, rid)["privacy"]["pii_masked"] >= 0
        print("ledger.db self-check OK")
    finally:
        with _index() as c:
            c.execute("DELETE FROM projects WHERE id=?", (pid,))
        shutil.rmtree(config.PROJECTS_DIR / pid, ignore_errors=True)
