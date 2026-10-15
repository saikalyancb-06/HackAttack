"""Vector index over KG nodes (ChromaDB persistent at data/projects/<pid>/chroma) + ask().

Embeddings are computed locally with feature hashing (no model download, no network, no LLM).
If chromadb is unavailable the same vectors are kept in a sqlite table, so retrieval never breaks.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading

from backend.ledger import db

DIM = 512
_clients: dict = {}
_lock = threading.Lock()
_WORD = re.compile(r"[a-z0-9<>_]+")

try:
    import chromadb  # type: ignore
    _HAVE_CHROMA = True
except Exception:  # pragma: no cover - depends on install
    chromadb = None
    _HAVE_CHROMA = False


def embed(text: str) -> list[float]:
    words = _WORD.findall((text or "").lower())
    feats = words + [f"{a} {b}" for a, b in zip(words, words[1:])]
    for w in words:
        if len(w) > 4:
            feats += [w[i:i + 4] for i in range(len(w) - 3)]
    v = [0.0] * DIM
    for f in feats:
        h = int(hashlib.md5(f.encode()).hexdigest(), 16)
        v[h % DIM] += 1.0 if (h >> 9) & 1 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def node_text(node: dict) -> str:
    def flat(o, prefix=""):
        if isinstance(o, dict):
            return " ".join(flat(v, f"{k} ") for k, v in o.items())
        if isinstance(o, list):
            return " ".join(flat(v, prefix) for v in o[:50])
        return f"{prefix}{o}"
    return f"{node.get('type', '')} {node.get('key', '')} {flat(node.get('props') or {})}"[:4000]


def _collection(project_id: str):
    with _lock:
        if project_id not in _clients:
            path = db.project_dir(project_id) / "chroma"
            client = chromadb.PersistentClient(path=str(path))
            _clients[project_id] = client.get_or_create_collection("nodes", embedding_function=None,
                                                                   metadata={"hnsw:space": "cosine"})
        return _clients[project_id]


def _fallback_conn(project_id: str):
    c = db.connect(project_id)
    c.execute("CREATE TABLE IF NOT EXISTS vectors(node_id TEXT PRIMARY KEY, text TEXT, vec_json TEXT)")
    return c


def index_node(project_id: str, node: dict) -> None:
    text = db.mask_text(project_id, node_text(node))  # already masked by upsert_node; defensive
    vec = embed(text)
    if _HAVE_CHROMA:
        _collection(project_id).upsert(ids=[node["id"]], documents=[text], embeddings=[vec],
                                       metadatas=[{"type": node.get("type", "")}])
    else:
        with _fallback_conn(project_id) as c:
            c.execute("INSERT OR REPLACE INTO vectors VALUES(?,?,?)", (node["id"], text, json.dumps(vec)))


def similar(project_id: str, text: str, k: int = 5) -> list[dict]:
    q = embed(db.mask_text(project_id, text))
    if _HAVE_CHROMA:
        col = _collection(project_id)
        n = col.count()
        if n == 0:
            return []
        res = col.query(query_embeddings=[q], n_results=min(k, n))
        return [{"node_id": i, "score": round(1.0 - d, 4), "text": t}
                for i, d, t in zip(res["ids"][0], res["distances"][0], res["documents"][0])]
    with _fallback_conn(project_id) as c:
        rows = c.execute("SELECT node_id, text, vec_json FROM vectors").fetchall()
    scored = [(sum(a * b for a, b in zip(q, json.loads(r["vec_json"]))), r) for r in rows]
    scored.sort(key=lambda x: -x[0])
    return [{"node_id": r["node_id"], "score": round(s, 4), "text": r["text"]} for s, r in scored[:k]]


def debug_documents(project_id: str) -> list[str]:
    if _HAVE_CHROMA:
        return _collection(project_id).get()["documents"] or []
    with _fallback_conn(project_id) as c:
        return [r["text"] for r in c.execute("SELECT text FROM vectors")]


def ask(project_id: str, question: str) -> dict:
    from backend.kg import store
    from backend import llm
    q_masked = db.mask_text(project_id, question)
    hits = [h for h in similar(project_id, q_masked, k=5) if h["score"] > 0.05]
    context, sources = [], []
    for h in hits:
        node = store.get_node(project_id, h["node_id"])
        if not node:
            continue
        sources.append(node["id"])
        context.append(f"[{node['id']}] {node_text(node)}")
        for nb in store.neighbors(project_id, node["id"], depth=1)[:3]:
            context.append(f"  related [{nb['id']}] {node_text(nb)[:400]}")
    if not context:
        return {"answer": "I don't have anything in this project's knowledge yet that answers that.", "sources": []}
    if llm.available():
        try:
            answer = llm.complete([
                {"role": "system", "content": "Answer the user's question using ONLY the project knowledge below. "
                 "Answer in short plain text (no markdown, no bold, no tables); mention file names, not node ids. "
                 "If the knowledge does not contain the answer, say so. "
                 "Placeholders like <EMAIL_1> are masked personal data: keep them as-is."},
                {"role": "user", "content": "Knowledge:\n" + "\n".join(context)[:9000] + f"\n\nQuestion: {q_masked}"},
            ], project_id=project_id)
            return {"answer": answer, "sources": sources}
        except Exception as e:
            db.audit(project_id, None, "llm_error", {"where": "rag.ask", "error": str(e)[:300]})
    lines = [c for c in context if not c.startswith("  ")][:3]
    return {"answer": "Most relevant project knowledge:\n" + "\n".join(f"- {l[:300]}" for l in lines), "sources": sources}


if __name__ == "__main__":
    import shutil
    from backend import config
    from backend.kg import store
    pid = db.create_project("rag-selfcheck")
    try:
        store.upsert_node(pid, "Artifact", "internships.xlsx#Shortlist", {"label": "Internship shortlist spreadsheet", "rows": 3})
        store.upsert_node(pid, "Fact", "weather", {"text": "It rained in Pune on Monday"})
        hits = similar(pid, "internship spreadsheet", k=2)
        assert hits and "Internship" in hits[0]["text"], hits
        store.upsert_node(pid, "Entity", "hr", {"email": "test@x.com", "phone": "9876543210"})
        assert not any("test@x.com" in d or "9876543210" in d for d in debug_documents(pid))
        out = ask(pid, "where is the internship spreadsheet?")
        assert out["sources"], out
        print(f"kg.rag self-check OK (chroma={_HAVE_CHROMA})")
    finally:
        _clients.pop(pid, None)
        db.delete_project(pid)
