"""Vector index over KG nodes + ask() (A3, CONTRACTS §4).

ChromaDB persistent client at data/projects/<pid>/chroma, one collection "nodes". Embeddings are computed
locally with feature hashing (no model download, no network, deterministic), so indexing never blocks a run.
Node text is already PII-masked by store.upsert_node; ask() masks the question before similar()/llm.complete
and unmasks only the final answer for display.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading

try:
    from modules.guard.pii import mask, unmask
except ImportError:  # A2 stub (same signatures)
    def mask(text):
        return text, {}

    def unmask(text_or_obj, mapping):
        return text_or_obj

import chromadb

DIM = 512
_clients: dict = {}
_lock = threading.Lock()
_WORD = re.compile(r"[a-z0-9<>_]+")


def embed(text: str) -> list[float]:
    """Signed feature hashing over words, word bigrams and 4-char grams; L2-normalised."""
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
    """type + key + props flattened."""
    def flat(o, prefix=""):
        if isinstance(o, dict):
            return " ".join(flat(v, f"{k} ") for k, v in o.items())
        if isinstance(o, list):
            return " ".join(flat(v, prefix) for v in o[:50])
        return f"{prefix}{o}"
    return f"{node.get('type', '')} {node.get('key', '')} {flat(node.get('props') or {})}"[:4000]


def _collection(project_id: str):
    from modules.kg.store import project_dir
    with _lock:
        if project_id not in _clients:
            client = chromadb.PersistentClient(path=str(project_dir(project_id) / "chroma"))
            _clients[project_id] = client.get_or_create_collection("nodes", embedding_function=None,
                                                                   metadata={"hnsw:space": "cosine"})
        return _clients[project_id]


def index_node(project_id: str, node: dict) -> None:
    text = mask(node_text(node))[0]  # store already masked; masking again is a no-op safety net
    _collection(project_id).upsert(ids=[node["id"]], documents=[text], embeddings=[embed(text)],
                                   metadatas=[{"type": node.get("type", "")}])


def similar(project_id: str, text: str, k: int = 5) -> list[dict]:
    col = _collection(project_id)
    n = col.count()
    if n == 0 or not text:
        return []
    res = col.query(query_embeddings=[embed(mask(text)[0])], n_results=min(int(k), n))
    return [{"node_id": i, "score": round(1.0 - d, 4), "text": t}
            for i, d, t in zip(res["ids"][0], res["distances"][0], res["documents"][0])]


def _debug_documents(project_id: str) -> list[str]:
    return _collection(project_id).get()["documents"] or []


def ask(project_id: str, question: str) -> dict:
    """similar(k=5) -> expand each hit with neighbors(depth=1) -> context -> llm.complete -> answer + source ids."""
    from modules.kg import store
    q_masked, mapping = mask(question or "")
    hits = [h for h in similar(project_id, q_masked, k=5) if h["score"] > 0.05]
    context, sources = [], []
    for h in hits:
        node = store.get_node(project_id, h["node_id"])
        if not node:
            continue
        sources.append(node["id"])
        context.append(f"[{node['id']}] {node_text(node)}")
        for nb in store.neighbors(project_id, node["id"], depth=1)[:4]:
            context.append(f"  related [{nb['id']}] {node_text(nb)[:400]}")
    if not context:
        return {"answer": "I don't have anything in this project's knowledge that answers that yet.", "sources": []}
    messages = [
        {"role": "system", "content": "Answer the question using ONLY the project knowledge below. Answer in short plain text "
                                      "(no markdown); mention file or item names, not node ids. If the knowledge does not "
                                      "contain the answer, say so. Placeholders like <EMAIL_1> are masked personal data: keep them."},
        {"role": "user", "content": "Knowledge:\n" + "\n".join(context)[:9000] + f"\n\nQuestion: {q_masked}"},
    ]
    try:
        from modules import llm
        answer = llm.complete(messages)
        if isinstance(answer, dict):
            answer = answer.get("answer") or json.dumps(answer)
    except Exception:  # no LLM (offline / not shipped yet): still answer with the best matching knowledge
        lines = [c for c in context if not c.startswith("  ")][:3]
        answer = "Most relevant project knowledge:\n" + "\n".join(f"- {l[:300]}" for l in lines)
    return {"answer": unmask(answer, mapping), "sources": sources}


if __name__ == "__main__":
    import os
    import shutil
    import sys
    import tempfile
    import time
    import types

    os.environ["DATA_DIR"] = tempfile.mkdtemp()
    t0 = time.time()
    from modules.kg import store
    pid = "p_ragcheck"
    try:
        a = store.upsert_node(pid, "Artifact", "internships.xlsx#Shortlist", {"label": "Internship shortlist spreadsheet", "rows": 3})
        store.upsert_node(pid, "Fact", "weather", {"text": "It rained in Pune on Monday"})
        e = store.upsert_node(pid, "Entity", "Google STEP", {"deadline": "30 Nov 2026"})
        store.add_edge(pid, a, e, "ABOUT")
        hits = similar(pid, "internship spreadsheet", k=2)
        assert hits and hits[0]["node_id"] == a, hits
        # ask() with a mocked LLM (self-checks never call the network): context must include the neighbor
        seen = {}

        def fake_complete(messages, json_schema=None, images=None, **kw):
            seen["prompt"] = messages[-1]["content"]
            return "The shortlist is in internships.xlsx."
        sys.modules["modules.llm"] = types.SimpleNamespace(complete=fake_complete)
        out = ask(pid, "where is the internship spreadsheet? mail me at test@x.com")
        assert out["sources"] and a in out["sources"] and "internships.xlsx" in out["answer"], out
        assert "Google STEP" in seen["prompt"]  # neighbor expansion
        try:
            from modules.guard.pii import mask as _m  # noqa: F401
            assert "test@x.com" not in seen["prompt"], seen["prompt"]
        except ImportError:
            pass
        # no LLM at all -> graceful answer from knowledge
        sys.modules["modules.llm"] = types.SimpleNamespace(complete=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
        assert ask(pid, "internship spreadsheet")["sources"]
        assert ask(pid, "")["sources"] == []
        assert time.time() - t0 < 10
        print(f"kg.rag self-check OK ({time.time() - t0:.1f}s)")
    finally:
        _clients.clear()
        shutil.rmtree(os.environ["DATA_DIR"], ignore_errors=True)
