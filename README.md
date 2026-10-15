# Operator: an autonomous AI operator for your PC

HackAttack AMCEC, AI/ML Track 1: *Build Your Own Autonomous AI Operator*.

You type a goal in plain language. Operator checks whether the intent is good and the request is feasible, plans the
steps, carries them out across the web, Excel, Word, email drafts, files, the shell and desktop apps (through MCP),
recovers from failures without repeating finished work, verifies every result, and asks you before anything risky.

```
goal ──► intent & feasibility gate ──► planner (LLM over the live tool catalog, template fallback)
              │ refuse / partial                     │
              ▼                                      ▼
         honest answer          per step: KG-first reuse ─► approval gate ─► execute (retry, MCP) ─► verify ─► record
                                              ▲                                     │ failure
                                              └──── self-healing re-plan ◄──────────┘ (outage → pause + auto-resume)
```

## What it does (judging criteria → feature)

| Criterion | How Operator covers it |
|---|---|
| Planning & autonomous multi-step execution | `agent/planner.py` turns a goal into steps over a tool catalog. Steps pass data to each other (`{{s2.rows}}`), can loop (`foreach`) and run unattended in `agent/loop.py`. |
| MCP integration | Office automation is an MCP server (`mcp_servers/office_com/server.py`, stdio) used through `tools/mcp_client.py`. MCP servers installed on the PC (Claude Desktop, Cursor, VS Code) are discovered, the official MCP registry is searched, and new servers install only after approval (`tools/mcp_registry.py`). Every connected server's tools appear to the planner automatically. |
| Reliability, error recovery, state persistence | SQLite ledger with WAL keeps runs, steps, outputs and audit. Each tool call retries 3× with backoff. If a tool is down (Scenario B), the run pauses with progress saved and resumes on its own when the tool returns. Excel upserts by key, so there are no duplicates, and drafts are tracked per item. If a step fails for another reason, the agent re-plans that step and the rest with a different approach (2 tries). If the server restarts, the run resumes from the last finished step. |
| Task completion & verification | `agent/verifier.py` re-reads results from the source of truth: Excel rows by key plus a duplicate check, document text, `.eml` drafts, files. A step is done only when its check passes. The final report lists done, reused, degraded, failed and unresolved steps; it never claims success that didn't happen. |
| Safety, human oversight, auditability | **Intent & feasibility gate** (`guard/intent.py`): gpt-oss-safeguard policy model, Llama Prompt Guard, capability rules and LLM feasibility analysis. Harmful goals are refused; partly feasible goals are reduced to the feasible part with alternatives. **Approvals** for sends, deletes (Recycle Bin only), non-read shell commands (rules plus safety model), protected paths and MCP installs. **PII**: everything sent to the LLM, ledger, audit or KG is masked (`<EMAIL_1>`) through a local vault, and screenshots are OCR-blurred: 0 raw PII to the LLM. The audit log is append-only. Drafts never use an email address the LLM invented. |
| Innovation | Knowledge graph and RAG memory (`kg/`): verified results are reused across runs (KG-first), and you can ask the project questions. Successful plans can be saved as workflows. Untrusted web content is checked for prompt injection. Groq key pool rotation. |
| UX & demo | Next.js UI in the Claude/Codex desktop style, dark and light themes. Live plan via SSE, approval cards, knowledge graph, audit timeline, report, Tools/MCP tab and a "Simulate Excel outage" switch. |

## Run it

Requirements: Windows, Python 3.11+ (3.13 tested), Node 20+, Microsoft Excel/Word (openpyxl/python-docx fallbacks exist), Groq API key(s).

```bash
copy .env.example .env        # put your GROQ_API_KEY1..3 in it
pip install fastapi uvicorn openai mcp chromadb openpyxl python-docx ddgs beautifulsoup4 playwright pywin32 send2trash uiautomation pytesseract pymupdf pyyaml
python -m uvicorn backend.app:app --port 8000
cd frontend && npm install && npm run build && npm run start   # http://localhost:3000
```

Without any API key the agent still works: it uses template planning and heuristic extraction, and every write needs approval because intent can't be verified.

## Tests

```bash
python -m pytest tests/test_selfchecks.py -q       # every module's self-check + unit checks
python tests/e2e/run_scenarios.py A               # Scenario A end to end (real web, Excel, drafts)
python tests/e2e/run_scenarios.py B               # Scenario B: outage mid-run -> pause -> auto-resume, no dupes
python tests/e2e/run_scenarios.py general         # unrelated goals: files, shell (approval), research -> Word
python tests/e2e/run_scenarios.py safety          # harmful goal refused; payment goal reduced to feasible part
python tests/e2e/reuse_check.py                   # same goal twice: plan from saved workflow, steps reused from the KG
```

## Layout

```
backend/app.py                FastAPI + SSE (docs/CONTRACTS.md §8)
backend/llm.py                Groq client: masking boundary, key rotation, model fallback, safety + prompt-guard models
backend/guard/                pii (vault masking) · intent (intent & feasibility) · risk · command_guard · safe_delete · visual_pii
backend/ledger/               db (runs/steps/audit/approvals/suggestions/vault) · retry
backend/kg/                   store (SQLite graph) · rag (ChromaDB, local hashing embeddings)
backend/tools/                catalog · office · research · browser · files · shell · windows · mcp_client · mcp_registry
backend/agent/                planner · loop · verifier · suggester · think (llm.generate) · explore · workflows · events
mcp_servers/office_com/       Office COM MCP server (Excel, Word, drafts, outage switch)
frontend/                     Next.js UI
privacy/, ml/, models/        on-device PII detection / redaction models (see reports/)
```

## Where this build differs from docs/CONTRACTS.md (for the team)

- **LLM: Groq only, no local models.** `gpt-oss-120b` plans and reasons, `gpt-oss-safeguard-20b` replaces Granite Guardian (intent + command checks), `llama-prompt-guard-2-86m` flags prompt injection. Embeddings are local feature hashing (no model download).
- **Extra SSE events:** `assessment` (intent & feasibility result), `report` (final report), `hello` (sent on connect; the UI refetches state).
- **Extra statuses:** run `refused` / `needs_input` / `stopped`; routes `think` (`llm.generate`) and `mcp` (tools from other MCP servers).
- **`backend/guard/intent.py`** (new): the intent & feasibility gate in front of the planner.
- **Desktop apps:** `tools/windows.py` (UI Automation) replaces windows-mcp, which isn't installed. If windows-mcp is installed from the Tools tab, its tools show up as `mcp.windows-mcp.*`. Editors work on a scratch file, and input is sent only while the agent's own window is in front.
- **Zero-shot classifier:** cue words plus a fast-LLM fallback instead of the ONNX DeBERTa model (no download needed). Same contract: `label=None` below threshold.
- **Self-healing:** after a non-outage failure the planner re-plans the remaining work (at most twice per run), recorded as `replan` in the audit log.
