# Brief A3 (Agy, friend): Knowledge Graph + RAG

**Owns:** `modules/kg/store.py`, `modules/kg/rag.py`.
**Deps:** stdlib `sqlite3`, `chromadb`. LLM only via `modules.llm.complete` (stub it until C1 ships).

## Tasks
1. `store.py`: tables and all functions in CONTRACTS §4, in `data/projects/<pid>/project.db`. `(project_id, type, key)` unique; `upsert_node` merges props and updates `valid_until`/`hash`.
2. `find_valid_artifact`: match type (+ key if given) + every filter on props; exclude rows where `valid_until < now`; return the newest.
3. `neighbors`: recursive CTE up to `depth`.
4. `graph_snapshot`: nodes `{id,type,key,label}`, edges `{src,dst,rel}` for the UI.
5. `rag.py`: persistent Chroma client at `data/projects/<pid>/chroma`, one collection `nodes`. `index_node` text = `type + key + props` flattened. `upsert_node` calls `index_node`.
6. `ask(question)`: `similar(k=5)` → expand each hit with `neighbors(depth=1)` → build context → `llm.complete` → answer + source node ids.
7. Self-check: upsert "schedule" artifact valid until tomorrow → found; valid until yesterday → not found; upsert same key twice → one node; neighbors works.

## PII (MUST)
- `upsert_node` runs every string in `props` and `key` through `guard.pii.mask` before storing. KG and Chroma hold placeholders only (`<EMAIL_1>`), never raw PII.
- `ask()` masks the question before `similar()` / `llm.complete`; unmask the answer only for display.
- Never log props or questions unmasked.
- Until A2 ships `guard/pii.py`, use a stub `mask(text) -> (text, {})` with the same signature.
- Self-check: upsert a node whose props contain `test@x.com` and `9876543210` → stored props and the Chroma document contain no raw email or phone.
