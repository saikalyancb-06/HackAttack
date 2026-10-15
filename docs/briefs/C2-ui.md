# Brief C2 (Claude, friend): UI

**Owns:** `frontend/` (Next.js + Tailwind). Consumes CONTRACTS §8 only.

## Layout (Claude / Codex desktop style)
- **Left sidebar:** projects list (+ new), threads in the selected project (+ new).
- **Center:** chat thread. A goal message renders an inline **plan**: a list of step cards with status chip, route icon, tool, attempts, evidence (expand), and a "Reused from knowledge" badge for `reused`. Pause / Resume / Stop on the run. Pending steps are editable (PATCH).
- **Right panel (tabs):** Knowledge (graph via react-force-graph + artifact list), Approvals queue, Audit timeline, Report.
- **Approval card:** shows exactly what will happen (file/cells/recipient/body/path), with Approve / Edit (args JSON or form) / Reject. Red styling for deletes.
- **Approval kinds** (`approval_required.kind`): `action`, `delete` (red), `command` (show the exact command, shell, cwd, guard reasons, Guardian flag), `mcp_install` (**install dialog**: server name, description, repo link, exact install command; Install / Not now).
- **Tools tab** (right panel): connected MCP servers and their tools, "installed but unused", and registry suggestions.
- **Suggestion card:** inline in chat, Accept / Dismiss.
- **Top bar:** "Simulate outage" toggle (`POST /settings/outage`), global Stop.

## PII (judged: safety + auditability)
- Audit timeline and step cards show masked text as-is (`<EMAIL_1>`) with a small "PII masked" badge. Never try to unmask in the UI.
- Approval cards show the real values (the backend sends them unmasked only in `approval_required.summary`, since the user must see what will happen). Never store them in localStorage.
- Screenshot evidence: show the blurred image the backend sends, with a "N PII regions blurred" chip (from `redact_image` regions).
- Report tab: a "Privacy" line, e.g. "14 PII items masked, 3 screenshot regions blurred, 0 raw PII sent to LLM".

## Phase 2 (after UI is demoable, ~3:30): Explore loop
Owns `modules/agent/explore.py` per CONTRACTS §7e. Target app: Packet Tracer if installed, else Paint/Settings.
1. **Search**: `research.search("how to <step> in <app>")`, keep top steps as a short checklist.
2. **Observe**: windows-mcp UI tree (State tool). Screenshot only if `classify(..., ["needs to see the screen", ...])` says so; screenshots always go through `redact_image` (done inside `llm.complete`).
3. **Act**: LLM picks exactly one windows-mcp action (click/type/shortcut) from the current UI tree.
4. **Verify**: re-observe; LLM/classifier confirms the expected change.
5. **Stuck** (2 failed verifies): search again with exact on-screen text (dialog title, error).
6. Budget 25 actions → return `ok=False` with the reason; never loop forever.
7. Any action that deletes, closes without saving, or submits → return a pending approval instead of acting.
8. Write each successful action to audit and the final result to the KG (`upsert_node(type="Artifact"...)`).
9. Self-check: mock windows-mcp + research; assert budget stop and stuck → re-search path.

## Rules
- One SSE connection per project (`/projects/{pid}/events`); update state from events, refetch the run on reconnect.
- Mock the API with a local JSON fixture until 2:30 so you are never blocked on the backend.
- Dark + light theme. Must look polished for demo; this is judged.
