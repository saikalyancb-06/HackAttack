"""Ledger: runs, steps, audit, projects, threads, approvals (A2). CONTRACTS §3, §11."""

import datetime as dt
import json
import os
import sqlite3
import uuid
from typing import Any

DATA_DIR = os.getenv("DATA_DIR", "./data")
JSON_STEP_FIELDS = {"args", "produces", "output", "evidence"}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _index_db_path() -> str:
    return os.path.join(DATA_DIR, "index.db")


def _project_db_path(project_id: str) -> str:
    return os.path.join(DATA_DIR, "projects", project_id, "project.db")


def _connect(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_index_tables() -> None:
    with _connect(_index_db_path()) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );"""
        )
        conn.commit()


def init_db(project_id: str) -> None:
    _ensure_index_tables()
    with _connect(_project_db_path(project_id)) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                goal_masked TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS steps (
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                parent_id TEXT,
                description TEXT,
                route TEXT,
                tool TEXT,
                args TEXT,
                risk TEXT,
                produces TEXT,
                input_hash TEXT,
                status TEXT NOT NULL,
                attempts INTEGER DEFAULT 0,
                output TEXT,
                evidence TEXT,
                error TEXT,
                started_at TEXT,
                ended_at TEXT,
                step_order INTEGER NOT NULL,
                PRIMARY KEY (run_id, step_id)
            );"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                run_id TEXT,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS approvals (
                approval_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                run_id TEXT,
                step_id TEXT,
                summary TEXT NOT NULL,
                decision TEXT NOT NULL DEFAULT 'pending',
                edited_args TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );"""
        )
        conn.commit()


def create_project(name: str) -> str:
    _ensure_index_tables()
    pid = f"p_{uuid.uuid4().hex[:8]}"
    t = now()
    with _connect(_index_db_path()) as conn:
        conn.execute(
            "INSERT INTO projects (id, name, created_at) VALUES (?, ?, ?)",
            (pid, name, t)
        )
        conn.commit()
    init_db(pid)
    return pid


def list_projects() -> list[dict]:
    _ensure_index_tables()
    with _connect(_index_db_path()) as conn:
        cur = conn.execute("SELECT id, name FROM projects ORDER BY created_at ASC")
        return [{"id": row["id"], "name": row["name"]} for row in cur.fetchall()]


def create_thread(project_id: str, title: str) -> str:
    _ensure_index_tables()
    tid = f"t_{uuid.uuid4().hex[:8]}"
    t = now()
    with _connect(_index_db_path()) as conn:
        conn.execute(
            "INSERT INTO threads (id, project_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (tid, project_id, title, t, t)
        )
        conn.commit()
    return tid


def list_threads(project_id: str) -> list[dict]:
    _ensure_index_tables()
    with _connect(_index_db_path()) as conn:
        cur = conn.execute(
            "SELECT id, title, updated_at FROM threads WHERE project_id = ? ORDER BY updated_at DESC",
            (project_id,)
        )
        return [{"id": row["id"], "title": row["title"], "updated_at": row["updated_at"]} for row in cur.fetchall()]


def _decode_step_row(row: sqlite3.Row | dict) -> dict:
    d = dict(row)
    d.pop("step_order", None)
    for field in JSON_STEP_FIELDS:
        val = d.get(field)
        if isinstance(val, str):
            try:
                d[field] = json.loads(val)
            except Exception:
                pass
    return d


def create_run(project_id: str, thread_id: str, goal_masked: str, plan: list[dict]) -> str:
    init_db(project_id)
    run_id = f"r_{uuid.uuid4().hex[:8]}"
    t = now()
    with _connect(_project_db_path(project_id)) as conn:
        conn.execute(
            "INSERT INTO runs (run_id, project_id, thread_id, goal_masked, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, project_id, thread_id, goal_masked, "running", t, t)
        )
        for i, s in enumerate(plan):
            sid = s.get("step_id") or f"s{i+1}"
            args_val = json.dumps(s["args"]) if s.get("args") is not None and not isinstance(s["args"], str) else s.get("args")
            produces_val = json.dumps(s["produces"]) if s.get("produces") is not None and not isinstance(s["produces"], str) else s.get("produces")
            output_val = json.dumps(s["output"]) if s.get("output") is not None and not isinstance(s["output"], str) else s.get("output")
            evidence_val = json.dumps(s["evidence"]) if s.get("evidence") is not None and not isinstance(s["evidence"], str) else s.get("evidence")
            conn.execute(
                """INSERT INTO steps (
                    run_id, step_id, parent_id, description, route, tool, args,
                    risk, produces, input_hash, status, attempts, output, evidence,
                    error, started_at, ended_at, step_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    sid,
                    s.get("parent_id"),
                    s.get("description", ""),
                    s.get("route", ""),
                    s.get("tool", ""),
                    args_val,
                    s.get("risk", "write"),
                    produces_val,
                    s.get("input_hash", ""),
                    s.get("status", "pending"),
                    s.get("attempts", 0),
                    output_val,
                    evidence_val,
                    s.get("error"),
                    s.get("started_at"),
                    s.get("ended_at"),
                    i
                )
            )
        conn.commit()
    return run_id


def get_run(project_id: str, run_id: str) -> dict:
    init_db(project_id)
    with _connect(_project_db_path(project_id)) as conn:
        r_cur = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        r_row = r_cur.fetchone()
        if not r_row:
            raise KeyError(f"Run not found: {run_id}")
        run_dict = dict(r_row)

        s_cur = conn.execute("SELECT * FROM steps WHERE run_id = ? ORDER BY step_order ASC", (run_id,))
        steps = [_decode_step_row(row) for row in s_cur.fetchall()]
        run_dict["steps"] = steps
        run_dict["plan"] = steps
        return run_dict


def update_run(project_id: str, run_id: str, **fields) -> None:
    init_db(project_id)
    if not fields:
        return
    fields["updated_at"] = now()
    set_clauses = [f"{k} = ?" for k in fields]
    values = list(fields.values()) + [run_id]
    with _connect(_project_db_path(project_id)) as conn:
        conn.execute(f"UPDATE runs SET {', '.join(set_clauses)} WHERE run_id = ?", values)
        conn.commit()


def update_step(project_id: str, run_id: str, step_id: str, **fields) -> None:
    init_db(project_id)
    if not fields:
        return
    db_fields: dict[str, Any] = {}
    for k, v in fields.items():
        if k in JSON_STEP_FIELDS and v is not None and not isinstance(v, str):
            db_fields[k] = json.dumps(v)
        else:
            db_fields[k] = v

    set_clauses = [f"{k} = ?" for k in db_fields]
    values = list(db_fields.values()) + [run_id, step_id]
    with _connect(_project_db_path(project_id)) as conn:
        conn.execute(f"UPDATE steps SET {', '.join(set_clauses)} WHERE run_id = ? AND step_id = ?", values)
        conn.commit()


def next_pending_step(project_id: str, run_id: str) -> dict | None:
    init_db(project_id)
    with _connect(_project_db_path(project_id)) as conn:
        cur = conn.execute(
            """SELECT * FROM steps 
               WHERE run_id = ? AND status NOT IN ('done', 'reused', 'skipped', 'rejected') 
               ORDER BY step_order ASC LIMIT 1""",
            (run_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        return _decode_step_row(row)


def find_done_by_hash(project_id: str, input_hash: str) -> dict | None:
    init_db(project_id)
    with _connect(_project_db_path(project_id)) as conn:
        cur = conn.execute(
            """SELECT * FROM steps 
               WHERE input_hash = ? AND status IN ('done', 'reused') 
               ORDER BY ended_at DESC LIMIT 1""",
            (input_hash,)
        )
        row = cur.fetchone()
        if not row:
            return None
        return _decode_step_row(row)


def audit(project_id: str, run_id: str, kind: str, payload: dict) -> None:
    init_db(project_id)
    t = now()
    payload_str = json.dumps(payload, default=str)
    with _connect(_project_db_path(project_id)) as conn:
        conn.execute(
            "INSERT INTO audit (project_id, run_id, kind, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (project_id, run_id, kind, payload_str, t)
        )
        conn.commit()


def list_audit(project_id: str, run_id: str | None = None) -> list[dict]:
    init_db(project_id)
    with _connect(_project_db_path(project_id)) as conn:
        if run_id is not None:
            cur = conn.execute(
                "SELECT id, project_id, run_id, kind, payload, created_at FROM audit WHERE project_id = ? AND run_id = ? ORDER BY id ASC",
                (project_id, run_id)
            )
        else:
            cur = conn.execute(
                "SELECT id, project_id, run_id, kind, payload, created_at FROM audit WHERE project_id = ? ORDER BY id ASC",
                (project_id,)
            )
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"])
            except Exception:
                pass
            rows.append(d)
        return rows


def create_approval(project_id: str, run_id: str, step_id: str, summary: dict) -> str:
    init_db(project_id)
    aid = f"appr_{uuid.uuid4().hex[:8]}"
    t = now()
    summary_str = json.dumps(summary, default=str)
    with _connect(_project_db_path(project_id)) as conn:
        conn.execute(
            """INSERT INTO approvals (approval_id, project_id, run_id, step_id, summary, decision, created_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
            (aid, project_id, run_id, step_id, summary_str, t)
        )
        conn.commit()
    return aid


def resolve_approval(project_id: str, approval_id: str, decision: str, edited_args: dict | None = None) -> None:
    init_db(project_id)
    t = now()
    edited_str = json.dumps(edited_args) if edited_args is not None else None
    with _connect(_project_db_path(project_id)) as conn:
        conn.execute(
            """UPDATE approvals SET decision = ?, edited_args = ?, resolved_at = ?
               WHERE approval_id = ?""",
            (decision, edited_str, t, approval_id)
        )
        conn.commit()


def get_approval(project_id: str, approval_id: str) -> dict | None:
    init_db(project_id)
    with _connect(_project_db_path(project_id)) as conn:
        cur = conn.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,))
        row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["id"] = d["approval_id"]
        for field in ("summary", "edited_args"):
            if d.get(field):
                try:
                    d[field] = json.loads(d[field])
                except Exception:
                    pass
        return d


if __name__ == "__main__":
    import shutil
    import tempfile
    import time

    t0 = time.time()
    orig_data_dir = os.environ.get("DATA_DIR")
    tmp_dir = tempfile.mkdtemp(prefix="ledger_test_")
    os.environ["DATA_DIR"] = tmp_dir
    DATA_DIR = tmp_dir

    try:
        pid = create_project("Test Project")
        assert pid.startswith("p_")
        projects = list_projects()
        assert len(projects) == 1
        assert projects[0]["name"] == "Test Project"

        tid = create_thread(pid, "Thread 1")
        assert tid.startswith("t_")
        threads = list_threads(pid)
        assert len(threads) == 1
        assert threads[0]["title"] == "Thread 1"

        plan = [
            {"step_id": "s1", "description": "Step 1", "tool": "tool1", "args": {"k": 1}, "status": "pending", "input_hash": "h1"},
            {"step_id": "s2", "description": "Step 2", "tool": "tool2", "args": {"k": 2}, "status": "pending", "input_hash": "h2"}
        ]
        rid = create_run(pid, tid, "Goal", plan)
        assert rid.startswith("r_")

        run = get_run(pid, rid)
        assert run["run_id"] == rid
        assert len(run["steps"]) == 2
        assert run["steps"][0]["run_id"] == rid
        assert run["steps"][0]["args"] == {"k": 1}

        nxt = next_pending_step(pid, rid)
        assert nxt is not None and nxt["step_id"] == "s1"

        update_step(pid, rid, "s1", status="done", output={"res": "ok"})
        nxt2 = next_pending_step(pid, rid)
        assert nxt2 is not None and nxt2["step_id"] == "s2"

        done = find_done_by_hash(pid, "h1")
        assert done is not None
        assert done["output"] == {"res": "ok"}

        audit(pid, rid, "test_event", {"info": "logged"})
        audits = list_audit(pid, rid)
        assert len(audits) == 1
        assert audits[0]["kind"] == "test_event"
        assert audits[0]["payload"] == {"info": "logged"}

        aid = create_approval(pid, rid, "s2", {"action": "delete"})
        appr = get_approval(pid, aid)
        assert appr is not None
        assert appr["decision"] == "pending"
        assert appr["summary"] == {"action": "delete"}

        resolve_approval(pid, aid, "approve")
        appr_resolved = get_approval(pid, aid)
        assert appr_resolved["decision"] == "approve"

        update_run(pid, rid, status="done")
        run_updated = get_run(pid, rid)
        assert run_updated["status"] == "done"

        print(f"ledger.db self-check PASSED in {time.time() - t0:.3f}s")
    finally:
        if orig_data_dir is not None:
            os.environ["DATA_DIR"] = orig_data_dir
        else:
            os.environ.pop("DATA_DIR", None)
        shutil.rmtree(tmp_dir, ignore_errors=True)
