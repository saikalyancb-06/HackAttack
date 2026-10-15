"""Mid-plan suggestions (C1). After a step, look in the KG for something that makes a later step redundant.

Returns {"sug_id", "run_id", "text", "patch"} or None.
patch ops: {"op": "skip", "step_id"} | {"op": "edit", "step_id", "args"} | {"op": "save_workflow", "name", "actions"}
"""
import hashlib

REUSE_SCORE = 0.8  # rag.similar score above which an existing artifact likely covers a step


def _sug(run_id: str, text: str, patch: dict) -> dict:
    sug_id = "sug_" + hashlib.sha1(f"{run_id}{patch}".encode()).hexdigest()[:8]
    return {"sug_id": sug_id, "run_id": run_id, "text": text, "patch": patch}


def suggest(project_id: str, run_id: str, step: dict) -> dict | None:
    from modules.kg import rag, store
    from modules.ledger import db

    later = [s for s in db.get_run(project_id, run_id)["steps"] if s["status"] == "pending"]
    for nxt in later:
        prod = nxt.get("produces") or {}
        if prod.get("type"):
            # Same artifact type already exists under a different key (e.g. yesterday's list)?
            others = [n for n in store.search_nodes(project_id, type="Artifact", text=prod["type"], limit=5)
                      if n.get("key") != prod.get("key") and n.get("source_step") != nxt["step_id"]]
            if others:
                o = others[0]
                return _sug(run_id, f"A {prod['type']} '{o['key']}' already exists in this project. Skip step {nxt['step_id']} ({nxt['description']}) and reuse it?",
                            {"op": "skip", "step_id": nxt["step_id"], "reuse_node": o["id"]})
        for hit in rag.similar(project_id, nxt["description"], k=3):
            node = store.get_node(project_id, hit["node_id"])
            if hit["score"] >= REUSE_SCORE and node and node.get("type") == "Artifact" and node.get("source_step") != nxt["step_id"]:
                return _sug(run_id, f"Step {nxt['step_id']} looks like earlier work ({node['key']}, match {hit['score']:.2f}). Skip it and reuse?",
                            {"op": "skip", "step_id": nxt["step_id"], "reuse_node": node["id"]})
    return None


if __name__ == "__main__":
    import sys
    import types

    run = {"steps": [{"step_id": "s2", "status": "pending", "description": "find internships", "produces": {"type": "internship_list", "key": "il_today"}}]}
    fake_db = types.SimpleNamespace(get_run=lambda p, r: run)
    fake_store = types.SimpleNamespace(
        search_nodes=lambda p, type=None, text=None, limit=20: [{"id": "n1", "key": "il_yesterday", "type": "Artifact", "source_step": "s9"}],
        get_node=lambda p, n: None)
    fake_rag = types.SimpleNamespace(similar=lambda p, t, k=5: [])
    sys.modules["modules.ledger.db"] = fake_db
    sys.modules["modules.kg.store"] = fake_store
    sys.modules["modules.kg.rag"] = fake_rag
    import modules.kg, modules.ledger
    modules.ledger.db, modules.kg.store, modules.kg.rag = fake_db, fake_store, fake_rag
    s = suggest("p", "r1", {})
    assert s and s["patch"] == {"op": "skip", "step_id": "s2", "reuse_node": "n1"}, s
    fake_store.search_nodes = lambda *a, **k: []
    assert suggest("p", "r1", {}) is None
    print("suggester ok")
