# GUIDELINES.md: Rules for All Agents

## Before coding
1. Read `PURPOSE.md`, `CONTRACTS.md`, and your brief in `briefs/`.
2. Only touch files your brief owns. Need something from another module? Use its contract; if missing, stub it locally and tell C1.

## Environment
- Python 3.11. One shared `.venv` at repo root on your machine (all modules run in one Python process).
- Create once: `python -m venv .venv`, then activate it (`.venv/Scripts/activate`). Install deps with `pip install <pkg>`.
- Keep a list of every package you install in `requirements-friend.txt` (one per line) and send it with your code so it can be merged.
- All Python code lives under `modules/`. Run from repo root: `python -m modules.kg.store` (self-check).
- Frontend lives in `frontend/` with its own `node_modules`.

## Code
- Python 3.11, type hints on public functions, no classes where a function works.
- Function names, args, and return shapes **exactly** as in `CONTRACTS.md`.
- No new dependencies beyond the brief without asking C1.
- Smallest working code. No speculative abstractions, no config for values that never change.
- Every module ends with an `if __name__ == "__main__":` self-check using `assert`s. It must run in under 10 s without network or LLM.
- Errors: raise with a clear message; never swallow exceptions silently. The agent loop decides retry/fallback.

## Safety (non-negotiable)
- Never log or send raw PII. Mask via `guard.pii.mask` before any LLM call or audit write.
- Never hard-delete. Use `guard.safe_delete.safe_delete`.
- Any tool that sends, deletes, overwrites, submits, or pays is `risk=irreversible`.
- Never commit `.env` or keys.

## Data
- All per-project state lives under `data/projects/<project_id>/`.
- SQLite: one connection per call (`sqlite3.connect(path)`), WAL mode on. No ORM.
- Timestamps: ISO 8601 UTC strings.
- Stable keys: `sha1(text).hexdigest()[:12]`.

## Do not interfere (strict)
- Edit, rename, move, delete, reformat or "fix" ONLY files your brief owns. Never touch another agent's files, even if they look broken or are still stubs. Report the problem to your human instead.
- No repo-wide actions: no global formatters/linters, no find-and-replace across the repo, no `git reset/checkout/stash/clean`, no deleting `data/` or `.venv`.
- Need another module before it's ready? Mock it inside your own file/self-check, never by editing theirs.
- Never kill processes, servers or terminals you didn't start.

## Coordination
- Contracts are frozen after 0:30. Changes go through C1 only.
- At each checkpoint in `PLAN.md`, report: done / blocked / what you need.
- Agy agents: do not do UI, styling, or prompt design. Hand those to C1/C2.
