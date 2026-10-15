# CONTRACTS.md: Interfaces (frozen at 0:30)

> Every agent codes against this file exactly. Need a change? Ask C1. Do not invent fields.

## 1. Repo layout
```
modules/
  app.py              # FastAPI: routes + SSE                     (C1)
  llm.py              # complete(messages, json_schema=None, images=None) -> str|dict   (C1)
  agent/
    planner.py        # goal -> list[Step]                        (C1)
    loop.py           # run/resume a run                          (C1)
    router.py         # pick route per step                       (C1)
    verifier.py       # independent checks                        (C1)
    suggester.py      # mid-plan suggestions                      (C1)
    explore.py        # explore loop for unknown apps             (C2, phase 2)
  ledger/db.py        # runs, steps, audit                        (A2)
  ledger/retry.py     # with_retry()                              (A2)
  guard/pii.py        # mask/unmask                               (A2)
  guard/risk.py       # needs_approval()                          (A2)
  guard/safe_delete.py# recycle-bin delete                        (A2)
  guard/visual_pii.py # blur PII regions in screenshots           (A2)
  classify.py         # zero-shot classifier (ONNX)               (A1, phase 2)
  tools/mcp_registry.py # discover installed + registry MCPs, install on approval  (A1, phase 3)
  tools/shell.py      # run_command()                             (A2, phase 3)
  guard/command_guard.py # rules + Granite Guardian               (A2, phase 3)
  tools/research.py   # search() with cache fallback              (A2)
  tools/mcp_client.py # mcp-use wrapper: list_tools(), call()     (A2)
  kg/store.py         # nodes/edges in SQLite                     (A3)
  kg/rag.py           # ChromaDB index + ask()                    (A3)
  mcp_servers/office_com/server.py   # FastMCP over pywin32        (A1)
  pre-built-vision/   # friend's ONNX models (exists, read-only)
  __init__.py         # every package folder has one; imports are `from modules.kg import store`
workflows/<app>/<name>.yaml
frontend/             # Next.js                                   (C2)
data/projects/<project_id>/project.db, chroma/, files/
.env.example
```

## 2. Core types (Python dataclasses / JSON)

### Step
```json
{
  "run_id": "r_ab12", "step_id": "s3", "parent_id": null,
  "description": "Save shortlisted internships to Excel",
  "route": "workflow|com|windows|browser|shell|kg",
  "tool": "office.excel_upsert_rows",
  "args": {"...": "..."},
  "risk": "read|write|irreversible",
  "produces": {"type": "spreadsheet", "key": "internships_2026-10-15"},
  "input_hash": "sha1 of tool+args",
  "status": "pending|running|done|reused|failed|degraded|awaiting_approval|rejected|skipped",
  "attempts": 0, "output": null, "evidence": null, "error": null,
  "started_at": null, "ended_at": null
}
```
`produces` is what the KG check uses. `status=reused` means KG satisfied it.

### Run
`{run_id, project_id, thread_id, goal_masked, plan: [Step], status: running|paused|done|failed|partial, created_at, updated_at}`

## 3. Ledger (A2): `modules/ledger/db.py`
SQLite tables in `project.db`: `projects, threads, runs, steps, audit, approvals`.
```python
init_db(project_id) -> None
create_project(name) -> project_id
create_thread(project_id, title) -> thread_id
create_run(project_id, thread_id, goal_masked, plan: list[dict]) -> run_id
get_run(project_id, run_id) -> dict            # includes steps
update_step(project_id, run_id, step_id, **fields) -> None
next_pending_step(project_id, run_id) -> dict|None   # first not in done/reused/skipped
find_done_by_hash(project_id, input_hash) -> dict|None
audit(project_id, run_id, kind, payload: dict) -> None   # append-only; payload already masked
list_audit(project_id, run_id=None) -> list[dict]
create_approval(project_id, run_id, step_id, summary: dict) -> approval_id
resolve_approval(project_id, approval_id, decision: "approve|edit|reject", edited_args=None) -> None
```
`modules/ledger/retry.py`: `with_retry(fn, attempts=3, backoff=(1,2,4)) -> result`, raises `RetryExhausted(last_error)`.

## 4. Knowledge graph (A3): `modules/kg/store.py`
Tables: `nodes(id, project_id, type, key, props_json, source_step, created_at, valid_until, hash)`, `edges(src, dst, rel, props_json, created_at)`.
Node types: `Task, Step, Artifact, Entity, Fact, Workflow`. Rels: `PRODUCED, USED, ABOUT, PART_OF, DERIVED_FROM`.
```python
upsert_node(project_id, type, key, props: dict, valid_until=None, source_step=None) -> node_id   # (type,key) unique
add_edge(project_id, src, dst, rel, props=None) -> None
get_node(project_id, node_id) -> dict|None
find_valid_artifact(project_id, type, key=None, filters: dict=None, now=None) -> dict|None   # respects valid_until
neighbors(project_id, node_id, rel=None, depth=1) -> list[dict]
search_nodes(project_id, type=None, text=None, limit=20) -> list[dict]
graph_snapshot(project_id, limit=500) -> {"nodes": [...], "edges": [...]}   # for UI
```
`modules/kg/rag.py`:
```python
index_node(project_id, node: dict) -> None          # called by upsert_node
similar(project_id, text, k=5) -> list[{"node_id","score","text"}]
ask(project_id, question) -> {"answer": str, "sources": [node_id]}   # graph + similar + llm.complete
```

## 5. Office COM MCP server (A1): `modules/mcp_servers/office_com/server.py`
FastMCP, stdio. Tool names and risk:
| Tool | Args | Returns | Risk |
|---|---|---|---|
| `excel_open` | path, visible=true | {workbook, sheets} | read |
| `excel_read_range` | path, sheet, range="A1:Z1000" | {rows: [[...]]} | read |
| `excel_upsert_rows` | path, sheet, key_column, rows: [dict] | {inserted, updated} | write |
| `excel_find_row` | path, sheet, key_column, key | {row: dict\|null} | read |
| `word_write_doc` | path, text | {path} | write |
| `outlook_create_draft` | to, subject, body | {draft_id} | write |
| `outlook_send_draft` | draft_id | {sent: true} | irreversible |
| `set_outage` | on: bool | {outage} | read |
If outage on, every `excel_*` tool raises `RuntimeError("Excel unavailable (simulated)")`.
No Outlook → `outlook_create_draft` writes `data/projects/<id>/files/drafts/<draft_id>.eml`.

## 6. Workflow YAML
```yaml
name: excel.upsert_rows
app: excel
intent: "Insert or update rows in an Excel sheet by key"
risk: write
params: {path: string, sheet: string, key_column: string, rows: list}
steps:
  - tool: office.excel_upsert_rows
    args: {path: "{path}", sheet: "{sheet}", key_column: "{key_column}", rows: "{rows}"}
verify:
  tool: office.excel_find_row
  expect: "every row key exists with matching values"
produces: {type: spreadsheet, key: "{path}#{sheet}"}
approved: true
```

## 7. Guard (A2)
```python
# guard/pii.py
mask(text) -> (masked_text, mapping: dict)      # <EMAIL_1>, <PHONE_1>, <AADHAAR_1>, <PAN_1>
unmask(text_or_obj, mapping) -> same type
# guard/risk.py
needs_approval(step: dict, protected_paths: list[str]) -> bool   # irreversible OR write touching protected path
# guard/safe_delete.py
safe_delete(path) -> {"recycled": path}         # send2trash; risk=irreversible always
# guard/visual_pii.py
redact_image(png_bytes) -> (png_bytes, regions: list[{"x","y","w","h","label","score"}])   # blur PII; call before ANY vision LLM call
```

## 7b. Classifier (A1 phase 2): `modules/classify.py`
```python
classify(text: str, labels: list[str], threshold=0.6) -> {"label": str|None, "score": float}   # label=None if below threshold → caller uses LLM
```
Zero-shot NLI over ONNX Runtime, CPU. Model loaded once (module-level). Callers and label sets:
| Caller | Labels |
|---|---|
| `app.py` message type | `["a task to perform", "a question about the project"]` |
| `mcp_client` tool risk | `["only reads data", "creates or modifies data", "deletes, sends, submits or pays"]` → read / write / irreversible |
| `router.py` first route | `["office document or spreadsheet", "website", "other desktop application", "files or shell"]` |
| `explore.py` | `["needs to see the screen", "can be done without seeing the screen"]` |
Workflow matching and fuzzy KG reuse use `kg.rag.similar` (embeddings), not this.

## 7c. MCP discovery (A1 phase 3): `modules/tools/mcp_registry.py`
```python
scan_installed() -> list[{"name","command","args","env_keys","source"}]
    # sources: %APPDATA%\Claude\claude_desktop_config.json, ~/.cursor/mcp.json,
    # .vscode/mcp.json, data/mcp_servers.json (ours). env VALUES never returned.
search_registry(need: str, k=5) -> list[{"name","description","install","repo","score"}]
    # official MCP Registry API; cache to data/cache/registry/; offline → cache only
best_for(need: str) -> {"installed": dict|None, "better": dict|None}
    # better = registry hit clearly beating installed (embedding similarity vs need)
request_install(project_id, candidate) -> approval_id    # emits approval_required (kind="mcp_install")
install(candidate) -> {"ok": bool, "name"}               # ONLY after approval; appends to data/mcp_servers.json; mcp_client reloads
```

## 7d. Shell + command guard (A2 phase 3)
```python
# tools/shell.py
run_command(cmd: str, shell="powershell|cmd", cwd=None, timeout=60) -> {"exit_code","stdout","stderr","truncated"}
    # cwd defaults to data/projects/<pid>/files; output capped 20 KB; output PII-masked before return
# guard/command_guard.py
check(cmd: str, context: str) -> {"risk": "read|write|irreversible", "flagged": bool, "reasons": [str]}
    # 1) rules: read allowlist (dir, type, Get-ChildItem, Get-Content, where, git status, ping, ipconfig...);
    #    irreversible patterns (del, rm, Remove-Item, rmdir, format, reg delete, shutdown, Stop-Process,
    #    Set-ExecutionPolicy, iwr|iex, curl|sh, > on existing file, pip/npm/winget install); else write
    # 2) Granite Guardian via Ollama: harm + prompt-injection check on (context, cmd); timeout → flagged=true
    # approval needed if risk==irreversible OR flagged OR cwd/target in PROTECTED_PATHS
```
Shell is the "shell" rung of the capability ladder. Every command and its guard result is written to `audit`.

## 7e. Explore loop (C2 phase 2): `modules/agent/explore.py`
```python
explore(project_id, run_id, step: dict, budget=25) -> {"ok": bool, "actions": [dict], "evidence": dict, "error": str|None}
```
Called by `loop.py` when the router picks route `windows` and no workflow/COM tool fits. Uses only: `tools.research.search`, `tools.mcp_client.call` (windows-mcp tools), `classify`, `llm.complete` (images via `redact_image`), `ledger.db.audit`, `kg.store.upsert_node`. Each action = one windows-mcp call, audited. On success, returns `actions` so C1 can offer "save as workflow".

## 8. HTTP API (C1 serves, C2 consumes). Base `http://localhost:8000`
| Method | Path | Body / Query | Returns |
|---|---|---|---|
| GET | `/projects` | | `[{id,name}]` |
| POST | `/projects` | `{name}` | `{id}` |
| GET | `/projects/{pid}/threads` | | `[{id,title,updated_at}]` |
| POST | `/projects/{pid}/threads` | `{title}` | `{id}` |
| POST | `/projects/{pid}/threads/{tid}/messages` | `{text}` | `{run_id}` (goal) or `{answer,sources}` (question) |
| GET | `/projects/{pid}/runs/{rid}` | | Run |
| POST | `/projects/{pid}/runs/{rid}/pause` \| `/resume` \| `/stop` | | `{status}` |
| PATCH | `/projects/{pid}/runs/{rid}/steps/{sid}` | `{args?, description?}` (only pending steps) | Step |
| POST | `/projects/{pid}/approvals/{aid}` | `{decision, edited_args?}` | `{ok}` |
| POST | `/projects/{pid}/suggestions/{sug_id}` | `{accept: bool}` | `{ok}` |
| GET | `/projects/{pid}/graph` | | `{nodes,edges}` |
| GET | `/projects/{pid}/audit?run_id=` | | `[audit]` |
| GET | `/projects/{pid}/runs/{rid}/report` | | `{summary, done, reused, degraded, failed, privacy: {pii_masked, regions_blurred, raw_pii_to_llm: 0}}` |
| POST | `/settings/outage` | `{on}` | `{outage}` |
| GET | `/projects/{pid}/events` | SSE | events below |

### SSE events (`event:` name, `data:` JSON)
`plan` {run_id, steps} · `step_update` {run_id, step} · `approval_required` {approval_id, step_id, kind: "action|delete|command|mcp_install", summary} · `suggestion` {sug_id, run_id, text, patch} · `kg_update` {nodes, edges} · `log` {run_id, text} · `run_status` {run_id, status}

## 9. Environment (`.env.example`)
```
LLM_PROVIDER=
LLM_MODEL=
LLM_API_KEY=
TAVILY_API_KEY=
DATA_DIR=./data
PROTECTED_PATHS=C:\Users\;D:\Important
CLASSIFIER_MODEL=MoritzLaurer/deberta-v3-xsmall-zeroshot-v1.1-all-33
VISUAL_PII_MODEL=modules/pre-built-vision/visual_pii_detector.onnx
OLLAMA_URL=http://localhost:11434
GUARDIAN_MODEL=granite3-guardian:2b
MCP_REGISTRY_URL=https://registry.modelcontextprotocol.io
```

## 10. Signatures added at skeleton time (C1)
```python
# tools/research.py (A2)
search(query: str, k=5) -> list[{"title","url","snippet"}]
# tools/mcp_client.py (A2)   tool names are "<server>.<tool>", e.g. "office.excel_upsert_rows"
list_tools() -> list[{"name","description","input_schema","risk"}]
call(tool: str, args: dict) -> dict          # raises on tool error
# agent/* (C1, internal)
planner.plan(project_id, run_id, goal_masked) -> list[Step]
loop.run(project_id, run_id) -> run status
router.route(project_id, step) -> {"route","tool","args"}
verifier.verify(project_id, step, output) -> {"ok": bool, "evidence": dict}
suggester.suggest(project_id, run_id, step) -> suggestion dict | None
```

## 11. Additions during C1 build (please implement)
**Ledger (A2), `ledger/db.py`, new functions:**
```python
list_projects() -> list[{"id","name"}]
list_threads(project_id) -> list[{"id","title","updated_at"}]
update_run(project_id, run_id, **fields) -> None     # at least status; also bumps updated_at
```
- `create_run` must stamp the new `run_id` onto every step (the planner passes `run_id=""`).
- `update_step` must accept any Step field, including `route`, `tool`, `args`, `input_hash`, `description`, `output`, `evidence` (dicts are stored as JSON).
- `get_run(...)["steps"]` are dicts in plan order, with JSON fields decoded.
- `list_audit` rows look like `{"kind", "payload": dict, ...}`, with `payload` decoded.

**PII (A2):** `mask()` mapping is `{"<EMAIL_1>": "raw value"}`, numbered from 1 on each call. The loop renumbers placeholders per run, so nothing else needs to.

**KG (A3):** the loop writes node types `Task` (key = run_id), `Step` (key = "run_id:step_id"), `Artifact` (key = `step.produces.key`, props `{kind: produces.type, description, output, evidence}`) and `Entity` (one per output row). The KG check is `find_valid_artifact(pid, "Artifact", key=produces.key)`. `search_nodes(text=...)` should also match `props` text, because the suggester searches by artifact kind.

**MCP registry (A1):** `request_install` must NOT emit SSE events. The loop calls `ledger.create_approval` itself and emits `approval_required` (kind `mcp_install`). The router only calls `best_for()`; `install()` runs after approval.

**Internal tool names** (not MCP; route in brackets): `llm.generate` [kg], `kg.ask` [kg], `kg.lookup` [kg], `research.search` [browser], `shell.run_command` [shell], `windows.explore` [windows → explore.explore].

**Workflow YAML (§6):** a step may set `foreach: <list param>`; its args can then use `{item.field}`.

**Env (§9):** `LLM_PROVIDER/LLM_MODEL/LLM_API_KEY` are replaced by `GROQ_API_KEY, CEREBRAS_API_KEY, OPENROUTER_API_KEY, GROQ_VISION_API_KEY, LOCAL_LLM_URL` (default `http://localhost:8091/v1`).
`llm.complete(..., task="plan"|"local")` gets an extra optional kwarg: "plan" uses Groq → Cerebras → OpenRouter; "local" uses llama-server, then that same chain; requests with images go to Groq `qwen/qwen3.8-27b` (`VISION_MODEL`) using `GROQ_VISION_API_KEY` (second Groq key, separate rate limit; falls back to `GROQ_API_KEY`). Gemini dropped (no key). Verified: this model accepts `image_url` data-URL PNGs.

**Approval-gated install (added):**
```python
ledger.db.get_approval(project_id, approval_id) -> dict|None      # (A2) includes decision
mcp_registry.install(candidate, project_id, approval_id) -> {"ok","name"}   # (A1) checks get_approval decision is approve/edit, else PermissionError
```
