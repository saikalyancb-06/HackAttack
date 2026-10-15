"""Knowledge graph: nodes/edges in data/projects/<pid>/project.db (stdlib sqlite3, WAL).

Every string in key/props is masked with the project PII vault before storage, so the
graph and the vector index only ever hold placeholders like <EMAIL_1>.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from backend.ledger import db

NODE_TYPES = ("Task", "Step", "Artifact", "Entity", "Fact", "Workflow")
RELS = ("PRODUCED", "USED", "ABOUT", "PART_OF", "DERIVED_FROM")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes(id TEXT PRIMARY KEY, project_id TEXT, type TEXT, key TEXT, props_json TEXT,
    source_step TEXT, created_at TEXT, valid_until TEXT, hash TEXT, UNIQUE(project_id, type, key));
CREATE TABLE IF NOT EXISTS edges(src TEXT, dst TEXT, rel TEXT, props_json TEXT, created_at TEXT, UNIQUE(src, dst, rel));
"""


def _conn(project_id: str):
    c = db.connect(project_id)
    c.executescript(_SCHEMA)
    return c


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(r) -> dict:
    return {"id": r["id"], "type": r["type"], "key": r["key"], "props": json.loads(r["props_json"] or "{}"),
            "source_step": r["source_step"], "created_at": r["created_at"], "valid_until": r["valid_until"], "hash": r["hash"]}


def upsert_node(project_id: str, type: str, key: str, props: dict, valid_until: str | None = None,
                source_step: str | None = None) -> str:
    if type not in NODE_TYPES:
        raise ValueError(f"unknown node type {type!r}")
    key = db.mask_text(project_id, str(key))
    props = db.mask_value(project_id, props or {})
    node_id = "n_" + hashlib.sha1(f"{type}|{key}".encode()).hexdigest()[:12]
    with _conn(project_id) as c:
        old = c.execute("SELECT props_json FROM nodes WHERE project_id=? AND type=? AND key=?", (project_id, type, key)).fetchone()
        merged = {**(json.loads(old["props_json"]) if old else {}), **props}
        h = hashlib.sha1(json.dumps(merged, sort_keys=True, default=str).encode()).hexdigest()[:12]
        if old:
            c.execute("UPDATE nodes SET props_json=?, valid_until=?, hash=?, source_step=COALESCE(?, source_step) "
                      "WHERE project_id=? AND type=? AND key=?",
                      (json.dumps(merged, default=str), valid_until, h, source_step, project_id, type, key))
        else:
            c.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?,?)",
                      (node_id, project_id, type, key, json.dumps(merged, default=str), source_step, _now_iso(), valid_until, h))
    node = get_node(project_id, node_id)
    try:
        from backend.kg import rag
        rag.index_node(project_id, node)
    except Exception as e:  # the graph is the source of truth; a failed vector index must be visible, not fatal
        db.audit(project_id, None, "kg_index_error", {"node_id": node_id, "error": str(e)[:300]})
    return node_id


def add_edge(project_id: str, src: str, dst: str, rel: str, props: dict | None = None) -> None:
    if rel not in RELS:
        raise ValueError(f"unknown rel {rel!r}")
    with _conn(project_id) as c:
        c.execute("INSERT OR IGNORE INTO edges VALUES(?,?,?,?,?)",
                  (src, dst, rel, json.dumps(db.mask_value(project_id, props or {})), _now_iso()))


def get_node(project_id: str, node_id: str) -> dict | None:
    with _conn(project_id) as c:
        r = c.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
    return _row(r) if r else None


def find_valid_artifact(project_id: str, type: str, key: str | None = None, filters: dict | None = None,
                        now: str | None = None) -> dict | None:
    now = now or _now_iso()
    q, args = "SELECT * FROM nodes WHERE project_id=? AND type=?", [project_id, type]
    if key is not None:
        q += " AND key=?"
        args.append(db.mask_text(project_id, str(key)))
    q += " AND (valid_until IS NULL OR valid_until >= ?) ORDER BY created_at DESC"
    args.append(now)
    with _conn(project_id) as c:
        rows = [_row(r) for r in c.execute(q, args)]
    filters = db.mask_value(project_id, filters or {})
    for n in rows:
        if all(n["props"].get(k) == v for k, v in filters.items()):
            return n
    return None


def neighbors(project_id: str, node_id: str, rel: str | None = None, depth: int = 1) -> list[dict]:
    rel_clause = "AND e.rel = :rel" if rel else ""
    sql = f"""
    WITH RECURSIVE walk(id, d) AS (
        SELECT :start, 0
        UNION
        SELECT CASE WHEN e.src = walk.id THEN e.dst ELSE e.src END, walk.d + 1
        FROM edges e JOIN walk ON (e.src = walk.id OR e.dst = walk.id)
        WHERE walk.d < :depth {rel_clause}
    )
    SELECT DISTINCT n.* FROM walk JOIN nodes n ON n.id = walk.id WHERE walk.id != :start
    """
    with _conn(project_id) as c:
        return [_row(r) for r in c.execute(sql, {"start": node_id, "depth": depth, "rel": rel})]


def search_nodes(project_id: str, type: str | None = None, text: str | None = None, limit: int = 20) -> list[dict]:
    q, args = "SELECT * FROM nodes WHERE project_id=?", [project_id]
    if type:
        q += " AND type=?"
        args.append(type)
    if text:
        q += " AND (key LIKE ? OR props_json LIKE ?)"
        t = f"%{db.mask_text(project_id, text)}%"
        args += [t, t]
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    with _conn(project_id) as c:
        return [_row(r) for r in c.execute(q, args)]


def graph_snapshot(project_id: str, limit: int = 500) -> dict:
    with _conn(project_id) as c:
        nodes = [{"id": r["id"], "type": r["type"], "key": r["key"],
                  "label": (json.loads(r["props_json"] or "{}").get("label") or r["key"])[:60],
                  "valid_until": r["valid_until"], "props": json.loads(r["props_json"] or "{}")}
                 for r in c.execute("SELECT * FROM nodes WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, limit))]
        ids = {n["id"] for n in nodes}
        edges = [{"src": r["src"], "dst": r["dst"], "rel": r["rel"]} for r in c.execute("SELECT * FROM edges")
                 if r["src"] in ids and r["dst"] in ids]
    return {"nodes": nodes, "edges": edges}


if __name__ == "__main__":
    import shutil
    from datetime import timedelta
    from backend import config
    pid = db.create_project("kg-selfcheck")
    try:
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds")
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
        a = upsert_node(pid, "Artifact", "schedule", {"kind": "schedule", "label": "Schedule"}, valid_until=tomorrow)
        assert find_valid_artifact(pid, "Artifact", "schedule")["id"] == a
        upsert_node(pid, "Artifact", "expired", {"kind": "schedule"}, valid_until=yesterday)
        assert find_valid_artifact(pid, "Artifact", "expired") is None
        b = upsert_node(pid, "Artifact", "schedule", {"extra": 1}, valid_until=tomorrow)
        assert a == b and sum(n["key"] == "schedule" for n in search_nodes(pid, "Artifact")) == 1
        assert get_node(pid, a)["props"]["extra"] == 1 and get_node(pid, a)["props"]["kind"] == "schedule"
        t = upsert_node(pid, "Task", "plan week", {"label": "Plan week"})
        f = upsert_node(pid, "Fact", "f1", {"text": "x"})
        add_edge(pid, t, a, "PRODUCED")
        add_edge(pid, a, f, "ABOUT")
        assert {n["id"] for n in neighbors(pid, t)} == {a}
        assert {n["id"] for n in neighbors(pid, t, depth=2)} == {a, f}
        assert find_valid_artifact(pid, "Artifact", filters={"kind": "schedule"})["id"] == a
        p = upsert_node(pid, "Entity", "contact", {"email": "test@x.com", "phone": "9876543210"})
        raw = json.dumps(get_node(pid, p))
        assert "test@x.com" not in raw and "9876543210" not in raw, raw
        from backend.kg import rag
        docs = rag.debug_documents(pid)
        assert docs and not any("test@x.com" in d or "9876543210" in d for d in docs)
        snap = graph_snapshot(pid)
        assert len(snap["nodes"]) == 5 and len(snap["edges"]) == 2
        print("kg.store self-check OK")
    finally:
        db.delete_project(pid)
