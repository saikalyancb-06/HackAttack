# PLAN.md: 6-Hour Execution Plan

## Team (5 agents)
| Agent | Model | Owns | Brief |
|---|---|---|---|
| **C1** (Samrat) | Claude | Contracts, agent core (planner, KG-first rule, router, verifier, suggester), integration (calls explore loop) | `briefs/C1-core.md` |
| **C2** (friend) | Claude | Entire UI (Next.js), then **explore loop** (`agent/explore.py`) | `briefs/C2-ui.md` |
| **A1** (Samrat) | Agy 3.8 Flash | Office COM MCP server (Excel/Word/Outlook) + outage toggle, then **zero-shot classifier**, then **MCP discovery + install dialog** | `briefs/A1-office-mcp.md` |
| **A2** (Samrat) | Agy 3.8 Flash | Ledger, retry, audit, guard (PII, risk, safe delete, visual PII blur), then Windows-MCP + research tool, then **shell tool + command guard (rules + Granite Guardian)** | `briefs/A2-ledger-guard.md` |
| **A3** (friend) | Agy 3.8 Flash | Knowledge graph (SQLite nodes/edges), ChromaDB, RAG retrieval | `briefs/A3-knowledge.md` |

Rule: Agy gets well-specified plumbing; Claude gets design, UI, and reasoning code. Agy does not do UI or prompt design.

## Timeline
| Time | Milestone | Who |
|---|---|---|
| 0:00–0:30 | Repo skeleton + `.env.example` + contracts frozen. Everyone installs deps. | C1 (others: setup) |
| 0:30–2:30 | Parallel build against `CONTRACTS.md`. Each module ships its own `__main__` self-check. | All |
| 2:30–3:30 | **Integration #1: Scenario A end-to-end in UI.** | C1 + C2, Agy on fixes |
| 3:30–4:30 | **Scenario B** + KG reuse demo + delete approval. | C1, A1, A2 |
| 4:30–5:30 | Exploration on non-Office app, mid-plan suggestions, Q&A chat, report view. MCP discovery + install dialog (A1), shell + command guard (A2). | C1, A1, A2, A3, C2 |
| 5:30–6:00 | **Code freeze.** Rehearse demo (PURPOSE §9) twice. Fix only demo blockers. | All |

## Checkpoints (stop and sync)
- **0:30**: contracts frozen. Changes after this only via C1, announced to all.
- **2:30**: every module's self-check passes.
- **3:30**: Scenario A demoable.
- **4:30**: Scenario B demoable.

## Cut order (if behind)
Tauri wrapper → MCP registry search (keep installed-config scan) → Granite Guardian (keep rule-based command guard, all non-read commands need approval) → exploration polish → Presidio → visual PII (then never send screenshots to LLM; UI tree only) → zero-shot classifier (LLM does routing) → ChromaDB (keep SQLite graph lookups) → mid-plan suggestions.
**Never cut:** Scenario A, Scenario B, KG-first reuse, approval gate + safe delete, verifier.

## Open questions (answer before 0:30)
1. LLM provider + key for the agent (`LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`).
2. Outlook desktop installed? If not, drafts = `.eml` files.
3. Exploration demo app: Packet Tracer installed? Else Paint/Settings.
4. Search API key (Tavily)? If not, cached results only.
5. **ONNX models (friend):** `heavy_visual_pii_detector`, `regularized_visual_pii_detector`, `browser_agent_detector` are byte-identical files; confirm the right ones. For each: input shape + preprocessing, output format (boxes / mask / scores), labels. What does `browser_agent_detector` detect?
6. Download the zero-shot model and export/convert to ONNX once on Wi-Fi before the event (or use `optimum` export), so the demo needs no download.
7. **Before the event, on good Wi-Fi:** install Ollama and `ollama pull granite3-guardian:2b` (~1.6 GB). Check it answers in < 2 s on the demo laptop; if slower, Guardian only checks commands, not every web page.
8. Prepare one "better MCP" demo case: a server that is in the registry but not installed, small, and installs offline-safe (pre-cache the package).
