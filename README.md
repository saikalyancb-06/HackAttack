<div align="center">

# ⚛️ atom8

### Your autonomous AI operator: say what you want done, watch it get done, safely.

*HackAttack AMCEC · AI/ML Track 1 · Build Your Own Autonomous AI Operator*

`Groq gpt-oss-120b` · `MCP` · `FastAPI + SSE` · `Next.js` · `Chrome extension` · `SQLite + ChromaDB` · `Telegram`

</div>

---

## 🧭 Why atom8

One real task, like *"find three internships, compare them, save the best to Excel and draft an email for each"*,
means jumping between a browser, a spreadsheet, a document and email, doing every step by hand. Chat assistants
only *tell* you what to do. **atom8 does it**: it plans the steps, runs them across your apps, recovers when
something breaks, checks its own work, and stops to ask you before anything risky or personal.

| You get | Because atom8 |
|---|---|
| **Work actually done** | plans multi-step goals and runs them across the web, Excel, Word, PowerPoint, email drafts, files, the shell and desktop apps |
| **Only safe, possible work** | checks every goal's **intent** (harmful? refuse) and **feasibility** (what can't it do? offer the doable part) before acting |
| **No guessing on vague tasks** | asks short **clarifying questions** first ("which date? which airline?"), or you say *"your choice"* |
| **Your login, your hands** | on websites it works **live in a new tab you watch**; at a login/OTP/CAPTCHA it pauses and you type, never the agent |
| **Results you can trust** | re-reads every result from the source (rows, files, cart) before calling a step done; reports honestly |
| **Resilience** | retries, pauses on outages and resumes from the exact step, re-plans failed steps, never duplicates work |
| **Privacy by design** | personal data is masked before any LLM call, screenshots are blurred, 0 raw PII ever leaves the PC |
| **Control** | approvals for writes/deletes/commands, live pause/stop, full append-only audit, Telegram on your phone |

---

## 🏗️ Architecture

### 1. System overview

atom8 is three cooperating processes plus a browser extension, all on your PC. Only masked text (and blurred
screenshots) ever reaches the cloud LLM.

```mermaid
flowchart LR
    U([👤 You]) -->|goal / question| UI
    TG([📱 Telegram]) <-->|goals · progress · approvals| BE

    subgraph PC["Your PC"]
        UI["🖥️ atom8 UI<br/>Next.js · :3000"]
        BE["🧠 Operator backend<br/>FastAPI + SSE · :8000"]
        AS["🌐 Browser-agent planner<br/>FastAPI · :8080"]
        EXT["🧩 atom8 Chrome extension<br/>(new tab, live agent)"]
        MCP["📎 Office MCP server<br/>stdio · Excel/Word/PPT/Outlook"]
        DB[("🗄️ SQLite ledger + KG<br/>ChromaDB vectors")]
        APPS["🪟 Desktop apps<br/>UI Automation · vision"]
    end

    UI <-->|REST + live SSE events| BE
    UI <-->|window.postMessage bridge| EXT
    EXT <-->|sanitized page snapshot → next action| AS
    BE --> MCP
    BE --> APPS
    BE <--> DB
    BE -->|masked prompts only| LLM[(☁️ Groq LLMs<br/>gpt-oss-120b · safeguard · vision)]
    AS -->|masked prompts only| LLM
    BE -->|search / fetch| WEB[(🌍 Web)]
```

| Component | Path | Role |
|---|---|---|
| **UI** | `frontend/` | Claude-style chat: projects & threads, live plan cards, approvals, knowledge graph, audit, reports, light/dark |
| **Operator backend** | `backend/` | the brain: intent gate, planner, run loop, tools, verifier, ledger, knowledge graph, Telegram |
| **Browser-agent planner** | `server/` | decides the next click/type on a web page from a sanitized DOM snapshot; detects login/OTP/CAPTCHA walls |
| **Chrome extension** | `browser-extension/` | opens the agent's tab, observes + redacts the page on-device, executes actions, shows the border / "your turn" UI |
| **Office MCP server** | `mcp_servers/office_com/` | Excel / Word / PowerPoint / Outlook-draft tools over MCP (stdio), with an outage switch for demos |
| **Team contract modules** | `modules/` | the team's CONTRACTS.md layout (knowledge graph, explore loop, router, ...) |

### 2. What happens to one goal

```mermaid
flowchart TD
    G["💬 Goal typed in UI or Telegram"] --> M["🔒 Mask PII → placeholders (&lt;EMAIL_1&gt;)"]
    M --> C{"Task or question?"}
    C -- question --> Q["🧠 Answer from project knowledge (RAG)<br/>→ web search if not known"]
    C -- task --> I{"🛡️ Intent & feasibility gate"}
    I -- harmful --> R["⛔ Refuse with reason"]
    I -- partly possible --> P1["⚠️ Do the feasible part,<br/>offer alternatives"]
    I -- ok --> V{"Missing details only you can decide?"}
    P1 --> V
    V -- yes --> CQ["❔ Questions card<br/>(answer or 'your choice')"] --> PL
    V -- no --> PL["📋 Plan: steps over the live tool catalog<br/>(or reuse a saved workflow)"]
    PL --> LOOP

    subgraph LOOP["🔁 Run loop, per step"]
        direction TB
        K{"♻️ Valid result already<br/>in knowledge graph?"} -- yes --> RU["reuse, skip work"]
        K -- no --> A{"🔐 Needs approval?<br/>file write · delete · command · send"}
        A -- yes --> AP["wait for Approve / Edit / Reject"]
        A -- no --> X
        AP --> X["⚙️ Execute tool (retry ×3)"]
        X --> VF{"✔️ Verify from source"}
        VF -- ok --> KG["record result + knowledge"]
        VF -- tool down --> OUT["⏸️ pause, keep progress,<br/>auto-resume when back"]
        VF -- failed --> RP["🩹 re-plan remaining steps"]
    end

    LOOP --> ANS["✨ Here's what you asked for<br/>+ honest report + privacy line"]
```

### 3. Website tasks: live in a new tab, you log in yourself

```mermaid
sequenceDiagram
    autonumber
    actor U as You
    participant UI as atom8 UI (:3000)
    participant BE as Backend (:8000)
    participant EXT as Extension
    participant TAB as New agent tab
    participant AS as Browser planner (:8080)

    U->>UI: "Add the best-selling pen to my cart on <site>"
    UI->>BE: POST message
    BE->>BE: intent ✓ · questions? · plan = [browser.agent]
    BE-->>UI: SSE browser_task
    UI->>EXT: START_TASK (postMessage bridge)
    EXT->>TAB: open new tab next to atom8 (same window)
    Note over TAB: 🟠 glowing border · your input paused · Stop button
    loop until goal verified (max 20 steps)
        EXT->>TAB: snapshot DOM + screenshot (PII redacted on-device)
        EXT->>AS: sanitized page → plan
        AS-->>EXT: next action (click / type / search / ask_user)
        EXT->>TAB: perform action
        EXT-->>UI: live progress → BE (audit)
    end
    AS-->>EXT: ask_user: "please sign in"
    Note over TAB: 🔵 Your turn banner · field boxed "Type here"<br/>typed data boxed 🔒 "redacted from agent"
    U->>TAB: types ID + password (agent never sees them)
    U->>UI: "I'm done — continue"
    EXT->>TAB: resume → Add to cart → verify cart
    EXT-->>UI: end (outcome, final page)
    UI->>BE: step result → verify → report
```

### 4. Inside the backend

```mermaid
flowchart LR
    subgraph API["app.py · REST + SSE"]
    end
    subgraph Guard["guard/"]
        PII["pii: vault masking"]
        INT["intent: harm + feasibility<br/>+ clarifying questions"]
        RISK["risk + command_guard"]
        VIS["visual_pii: OCR + ONNX blur"]
        DEL["safe_delete → Recycle Bin"]
    end
    subgraph Agent["agent/"]
        PLN["planner (+ repair)"]
        LP["loop: run / pause / resume / stop"]
        THK["think: extract · rank · write"]
        VER["verifier"]
        SUG["suggester"]
        EXP["explore: desktop UIA + vision"]
        BB["browser_bridge"]
        WF["workflows (learned plans)"]
    end
    subgraph Tools["tools/"]
        CAT["catalog (tool specs)"]
        OFF["office (COM)"]
        RES["research (search/fetch)"]
        FS["files · shell"]
        WIN["windows · desktop"]
        MC["mcp_client · mcp_registry"]
    end
    subgraph Memory
        LED["ledger/db: runs · steps · audit<br/>approvals · vault"]
        KGS["kg/store + rag"]
    end
    API --> LP --> PLN
    LP --> CAT --> OFF & RES & FS & WIN & MC & BB & EXP & THK
    LP --> VER & SUG
    LP --> LED & KGS
    PLN --> INT
    LP --> RISK
    THK & PLN --> LLMX["llm.py: mask → Groq<br/>(key rotation, fallback chain)"]
    LLMX --> PII
```

### 5. Safety & privacy layers

| Layer | Where | What it guarantees |
|---|---|---|
| **Intent gate** | `guard/intent.py` | harmful goals refused (gpt-oss-safeguard + prompt-injection model + capability rules) |
| **Feasibility** | `guard/intent.py` | impossible parts (payments, calls, physical actions) named, with what it *can* do instead |
| **Clarification** | `guard/intent.py` | real-world actions never run on guessed dates/sizes/airlines |
| **Approvals** | `agent/loop.py` | every file write on your PC, delete, non-read shell command, send, MCP install asks first |
| **PII masking** | `guard/pii.py`, `llm.py` | emails/phones/IDs/cards/secrets replaced by placeholders before any LLM call, log, or graph |
| **Screenshot redaction** | `guard/visual_pii.py`, extension | OCR + on-device ONNX blur before any vision call; red boxes on the page |
| **Your credentials** | extension + `server/` | the agent never types passwords/OTPs: it hands the page to you |
| **No hard deletes** | `guard/safe_delete.py` | deletes go to the Recycle Bin |
| **Audit** | `ledger/db.py` | append-only record of every plan, tool call, approval and LLM call (masked) |

---

## 🚀 Run it

**Needs:** Windows, Python 3.11+, Node 20+, Chrome, Microsoft Office (optional, has fallbacks), Groq API key(s).

```bash
copy .env.example .env                                   # add GROQ_API_KEY1..3 (+ optional TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED)
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements-local.txt
python -m uvicorn backend.app:app --port 8000            # 🧠 backend
python -m uvicorn server.app.main:app --port 8080        # 🌐 browser-agent planner
cd frontend && npm install && npm run build && npm start # 🖥️ UI → http://localhost:3000
```

**One-time browser setup:** `chrome://extensions` → Developer mode → **Load unpacked** → `browser-extension/`.
Open atom8 (localhost:3000) in that Chrome.

**Telegram (optional):** create a bot with @BotFather, put `TELEGRAM_BOT_TOKEN` and your chat id in
`TELEGRAM_ALLOWED`, restart the backend, then send goals from your phone.

## 🎬 Try these

| Goal | Shows |
|---|---|
| *Find three internships for a 3rd-year CS student, compare eligibility, save the best to a spreadsheet and draft emails* | multi-step planning, Excel via MCP, verified drafts |
| Same goal with **Simulate Excel outage** on | pause on failure, auto-resume, no duplicate rows |
| *On http://127.0.0.1:8765/index.html add the best-selling pen to the cart* (serve `tests/e2e/fixtures/shop`) | new tab, live clicks, login handoff, redaction boxes |
| *Book a flight from Goa to Delhi* | clarifying questions, feasible-part only (never pays) |
| *Open PowerPoint and make 2 slides: hi, hello* | visible Office editing, verified |
| *Write a keylogger for my roommate* | refused by the intent gate |

## 🧪 Tests

```bash
python -m pytest tests/test_selfchecks.py -q          # every module self-check
python tests/e2e/run_scenarios.py all                # Scenario A, Scenario B, general tasks, safety
python tests/e2e/browser_agent_check.py              # new tab + login handoff + cart (real Chromium + extension)
python -m pytest server/tests/test_operator_handoff.py
```

## 🗂️ Repository layout

```
frontend/                Next.js UI (atom8)
backend/                 Operator backend: app · llm · guard/ · agent/ · tools/ · ledger/ · kg/ · channels/telegram
server/                  browser-agent planner (page → next action, login/OTP/CAPTCHA detection)
browser-extension/       Chrome extension: on-device perception + redaction, actions, agent tab UI, atom8 bridge
mcp_servers/office_com/  Office MCP server
modules/                 team CONTRACTS.md modules (kg, explore, router, telegram, ...)
privacy/ ml/ models/     on-device PII detection / redaction models
docs/                    CONTRACTS, PLAN, GUIDELINES, briefs
tests/                   self-checks and end-to-end scenarios
```

<details>
<summary><b>Notes for the team: where this build differs from CONTRACTS.md</b></summary>

- **LLM:** Groq only (no local models). `gpt-oss-120b` plans/reasons, `gpt-oss-safeguard-20b` replaces Granite
  Guardian, `llama-prompt-guard-2-86m` flags prompt injection, `qwen3.8-27b` is the vision model; Cerebras /
  OpenRouter are used only if every Groq key fails. Embeddings are local feature hashing.
- **Extra SSE events:** `assessment`, `clarification`, `browser_task`, `browser_control`, `report`, `hello`.
- **Extra statuses:** run `refused` / `needs_input` / `stopped`; routes `think`, `mcp`, `browser`.
- **New:** `guard/intent.py` (intent, feasibility, clarifying questions), `agent/browser_bridge.py` (browser.agent step).
- **Learned workflows** are saved to `data/workflows/`; `workflows/` keeps the hand-written §6 YAML.
- Without any API key it still works: template planning, heuristic extraction, and every write needs approval.

</details>
