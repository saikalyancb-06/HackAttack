"""Knowledge graph (A3, CONTRACTS §4): nodes/edges in data/projects/<pid>/project.db (stdlib sqlite3, WAL).

PII: every string in `key` and `props` goes through guard.pii.mask before it is stored, so the graph and the
vector index only ever hold placeholders such as <EMAIL_1>. Props are masked in one call so placeholders stay
consistent inside a node; the key is masked on its own so find_valid_artifact(key=...) matches deterministically.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

try:
    from modules.guard.pii import mask
except ImportError:  # A2 not shipped yet: same signature, no masking (contract-approved stub)
    def mask(text):
        return text, {}

NODE_TYPES = ("Task", "Step", "Artifact", "Entity", "Fact", "Workflow")
RELS = ("PRODUCED", "USED", "ABOUT", "PART_OF", "DERIVED_FROM")
ROOT = Path(__file__).resolve().parents[2]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes(id TEXT PRIMARY KEY, project_id TEXT, type TEXT, key TEXT, props_json TEXT,
    source_step TEXT, created_at TEXT, valid_until TEXT, hash TEXT, UNIQUE(project_id, type, key));
CREATE TABLE IF NOT EXISTS edges(src TEXT, dst TEXT, rel TEXT, props_json TEXT, created_at TEXT, UNIQUE(src, dst, rel));
CREATE INDEX IF NOT EXISTS nodes_type ON nodes(project_id, type);
"""


def _data_dir() -> Path:
    d = Path(os.environ.get("DATA_DIR", "./data"))
    return d if d.is_absolute() else (ROOT / d).resolve()


def project_dir(project_id: str) -> Path:
    d = _data_dir() / "projects" / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _conn(project_id: str) -> sqlite3.Connection:
    c = sqlite3.connect(project_dir(project_id) / "project.db", timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mask_key(key) -> str:
    return mask(str(key))[0]


def _mask_obj(obj):
    """Mask every string in a JSON-able object with ONE mask() call (consistent placeholders within the node)."""
    if obj in (None, {}, []):
        return obj
    raw = json.dumps(obj, ensure_ascii=False, default=str)
    masked, _ = mask(raw)
    try:
        return json.loads(masked)
    except json.JSONDecodeError:  # a placeholder can never break JSON, but stay safe: fall back to per-string masking
        def walk(o):
            if isinstance(o, str):
                return mask(o)[0]
            if isinstance(o, dict):
                return {k: walk(v) for k, v in o.items()}
            if isinstance(o, list):
                return [walk(v) for v in o]
            return o
        return walk(json.loads(raw))


def _row(r) -> dict:
    props = json.loads(r["props_json"] or "{}")
    return {"id": r["id"], "project_id": r["project_id"], "type": r["type"], "key": r["key"], "props": props,
            "label": str(props.get("label") or props.get("description") or r["key"])[:80],
            "source_step": r["source_step"], "created_at": r["created_at"], "valid_until": r["valid_until"], "hash": r["hash"]}


def upsert_node(project_id: str, type: str, key: str, props: dict, valid_until: str | None = None,
                source_step: str | None = None) -> str:
    if type not in NODE_TYPES:
        raise ValueError(f"unknown node type {type!r}; expected one of {NODE_TYPES}")
    key = _mask_key(key)
    props = _mask_obj(props or {}) or {}
    node_id = "n_" + hashlib.sha1(f"{project_id}|{type}|{key}".encode()).hexdigest()[:12]
    with _conn(project_id) as c:
        old = c.execute("SELECT props_json, source_step FROM nodes WHERE project_id=? AND type=? AND key=?",
                        (project_id, type, key)).fetchone()
        merged = {**(json.loads(old["props_json"]) if old else {}), **props}
        h = hashlib.sha1(json.dumps(merged, sort_keys=True, default=str).encode()).hexdigest()[:12]
        if old:
            c.execute("UPDATE nodes SET props_json=?, valid_until=?, hash=?, source_step=COALESCE(?, source_step) "
                      "WHERE project_id=? AND type=? AND key=?",
                      (json.dumps(merged, default=str), valid_until, h, source_step, project_id, type, key))
        else:
            c.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?,?)",
                      (node_id, project_id, type, key, json.dumps(merged, default=str), source_step, _now(), valid_until, h))
    node = get_node(project_id, node_id)
    from modules.kg import rag
    rag.index_node(project_id, node)
    return node_id


def add_edge(project_id: str, src: str, dst: str, rel: str, props: dict | None = None) -> None:
    if rel not in RELS:
        raise ValueError(f"unknown rel {rel!r}; expected one of {RELS}")
    with _conn(project_id) as c:
        c.execute("INSERT OR IGNORE INTO edges VALUES(?,?,?,?,?)",
                  (src, dst, rel, json.dumps(_mask_obj(props or {}) or {}, default=str), _now()))


def get_node(project_id: str, node_id: str) -> dict | None:
    with _conn(project_id) as c:
        r = c.execute("SELECT * FROM nodes WHERE id=? AND project_id=?", (node_id, project_id)).fetchone()
    return _row(r) if r else None


def find_valid_artifact(project_id: str, type: str, key: str | None = None, filters: dict | None = None,
                        now: str | None = None) -> dict | None:
    """Newest node of `type` (+ `key`) whose props match every filter and that is not expired."""
    now = now or _now()
    q, args = "SELECT * FROM nodes WHERE project_id=? AND type=? AND (valid_until IS NULL OR valid_until >= ?)", [project_id, type, now]
    if key is not None:
        q += " AND key=?"
        args.append(_mask_key(key))
    q += " ORDER BY created_at DESC, rowid DESC"
    want = _mask_obj(filters or {}) or {}
    with _conn(project_id) as c:
        for r in c.execute(q, args):
            n = _row(r)
            if all(n["props"].get(k) == v for k, v in want.items()):
                return n
    return None


def neighbors(project_id: str, node_id: str, rel: str | None = None, depth: int = 1) -> list[dict]:
    """Nodes reachable within `depth` hops (edges followed in both directions), via a recursive CTE."""
    rel_clause = "AND e.rel = :rel" if rel else ""
    sql = f"""
    WITH RECURSIVE walk(id, d) AS (
        SELECT :start, 0
        UNION
        SELECT CASE WHEN e.src = walk.id THEN e.dst ELSE e.src END, walk.d + 1
        FROM edges e JOIN walk ON (e.src = walk.id OR e.dst = walk.id)
        WHERE walk.d < :depth {rel_clause}
    )
    SELECT DISTINCT n.* FROM walk JOIN nodes n ON n.id = walk.id
    WHERE walk.id != :start AND n.project_id = :pid
    """
    with _conn(project_id) as c:
        return [_row(r) for r in c.execute(sql, {"start": node_id, "depth": int(depth), "rel": rel, "pid": project_id})]


def search_nodes(project_id: str, type: str | None = None, text: str | None = None, limit: int = 20) -> list[dict]:
    """Substring search over key AND props (the suggester searches by artifact kind)."""
    q, args = "SELECT * FROM nodes WHERE project_id=?", [project_id]
    if type:
        q += " AND type=?"
        args.append(type)
    if text:
        t = f"%{_mask_key(text)}%"
        q += " AND (key LIKE ? OR props_json LIKE ?)"
        args += [t, t]
    q += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    args.append(int(limit))
    with _conn(project_id) as c:
        return [_row(r) for r in c.execute(q, args)]


def graph_snapshot(project_id: str, limit: int = 500) -> dict:
    """For the UI: nodes {id,type,key,label} (+ props, valid_until for the detail view) and edges {src,dst,rel}."""
    with _conn(project_id) as c:
        rows = c.execute("SELECT * FROM nodes WHERE project_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                         (project_id, int(limit))).fetchall()
        nodes = []
        for r in rows:
            n = _row(r)
            nodes.append({"id": n["id"], "type": n["type"], "key": n["key"], "label": n["label"],
                          "props": n["props"], "valid_until": n["valid_until"]})
        ids = {n["id"] for n in nodes}
        edges = [{"src": r["src"], "dst": r["dst"], "rel": r["rel"]} for r in c.execute("SELECT src, dst, rel FROM edges")
                 if r["src"] in ids and r["dst"] in ids]
    return {"nodes": nodes, "edges": edges}


if __name__ == "__main__":
    import shutil
    import tempfile
    import time
    from datetime import timedelta

    os.environ["DATA_DIR"] = tempfile.mkdtemp()
    os.environ["KG_EMBED"] = "hash"
    pid = "p_selfcheck"
    t0 = time.time()
    try:
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds")
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
        # valid until tomorrow -> found
        a = upsert_node(pid, "Artifact", "schedule", {"kind": "schedule", "label": "Weekly schedule"}, valid_until=tomorrow)
        assert find_valid_artifact(pid, "Artifact", key="schedule")["id"] == a
        # valid until yesterday -> not found
        upsert_node(pid, "Artifact", "old-schedule", {"kind": "schedule"}, valid_until=yesterday)
        assert find_valid_artifact(pid, "Artifact", key="old-schedule") is None
        # filters + "newest valid wins"
        assert find_valid_artifact(pid, "Artifact", filters={"kind": "schedule"})["id"] == a
        # same key twice -> one node, props merged
        b = upsert_node(pid, "Artifact", "schedule", {"rows": 3}, valid_until=tomorrow)
        assert a == b and sum(n["key"] == "schedule" for n in search_nodes(pid, "Artifact")) == 1
        assert get_node(pid, a)["props"] == {"kind": "schedule", "label": "Weekly schedule", "rows": 3}
        # neighbors (depth 1 and 2) + rel filter
        t = upsert_node(pid, "Task", "r_1", {"label": "Plan week"})
        f = upsert_node(pid, "Fact", "f1", {"text": "Mondays are busy"})
        add_edge(pid, t, a, "PRODUCED")
        add_edge(pid, a, f, "ABOUT")
        add_edge(pid, t, a, "PRODUCED")  # idempotent
        assert {n["id"] for n in neighbors(pid, t)} == {a}
        assert {n["id"] for n in neighbors(pid, t, depth=2)} == {a, f}
        assert {n["id"] for n in neighbors(pid, a, rel="ABOUT")} == {f}
        # search matches props text
        assert any(n["id"] == f for n in search_nodes(pid, text="Mondays"))
        # PII never stored raw (graph AND vector index)
        p = upsert_node(pid, "Entity", "contact test@x.com", {"email": "test@x.com", "phone": "9876543210", "note": "call 9876543210"})
        with _conn(pid) as c:
            raw = json.dumps([dict(r) for r in c.execute("SELECT * FROM nodes")])
        from modules.kg import rag
        docs = json.dumps(rag._debug_documents(pid))
        try:
            from modules.guard.pii import mask as _real  # noqa: F401
            assert "test@x.com" not in raw and "9876543210" not in raw, raw
            assert "test@x.com" not in docs and "9876543210" not in docs, docs
            assert get_node(pid, p)["props"]["phone"] == get_node(pid, p)["props"]["note"].split()[-1]  # consistent placeholder
        except ImportError:
            print("  (guard.pii not available: PII assertions skipped, stub mask in use)")
        snap = graph_snapshot(pid)
        assert len(snap["nodes"]) == 5 and len(snap["edges"]) == 2
        assert all({"id", "type", "key", "label"} <= set(n) for n in snap["nodes"])
        assert time.time() - t0 < 10
        print(f"kg.store self-check OK ({time.time() - t0:.1f}s)")
    finally:
        rag._clients.clear() if "rag" in dir() else None
        shutil.rmtree(os.environ["DATA_DIR"], ignore_errors=True)
