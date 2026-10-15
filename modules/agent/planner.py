"""Goal -> list[Step] (C1). Input goal is already PII-masked.

Steps may reference earlier outputs in args: "{{s2}}" (whole output) or "{{s2.rows}}" (a field).
loop.py resolves these just before execution.
"""
import datetime as dt
import hashlib
import json

from modules import llm
from modules.agent.router import INTERNAL, load_workflows

INTERNAL_TOOLS = """- research.search {query}: web search, returns {results: [{title,url,snippet}]}
- llm.generate {prompt, inputs: [step_ids], schema}: reason/compare/write text over earlier outputs; returns JSON matching schema
- kg.ask {question}: answer from project knowledge
- shell.run_command {cmd}: PowerShell command (last resort)
- windows.explore {app, goal}: operate any Windows app by looking at its UI"""

SCHEMA = {
    "type": "object",
    "properties": {"steps": {"type": "array", "items": {"type": "object", "properties": {
        "description": {"type": "string"}, "tool": {"type": "string"}, "args": {"type": "object"},
        "risk": {"enum": ["read", "write", "irreversible"]},
        "produces": {"type": ["object", "null"], "properties": {"type": {"type": "string"}, "key": {"type": "string"}}},
    }}}},
}

RULES = """Rules:
- Use the fewest steps. Prefer a saved workflow, then an MCP tool, then internal tools.
- Pass data between steps with "{{sN}}" or "{{sN.field}}" in args (steps are numbered s1, s2, ...).
- For spreadsheets of web items, rows MUST include "link"; use key_column "key" (the system fills key = hash of link).
- Excel files go under "files/" relative paths, e.g. "files/internships.xlsx", sheet "Sheet1".
- risk: read = only reads; write = creates/modifies; irreversible = sends, deletes, submits, pays, overwrites. Email SENDING is irreversible; drafting is write.
- Never send email unless the goal explicitly says send. Draft instead.
- produces: what the step leaves behind, {type, key}. Use a stable key that includes today's date when the result goes stale (e.g. "internships_cs_3rd_year_{today}"). null for steps that produce nothing reusable.
- Placeholders like <EMAIL_1> are masked personal data: copy them verbatim, never invent real values.
- If the goal works with a desktop app the user can see (Notepad, Excel on the desktop, Word, Paint, a web page or game already open
  in the browser...) or with the user's own files
  outside the project, make ONE windows.explore step whose description is the whole task in the user's words, with absolute file paths.
  It scripts the app itself (PowerShell/COM + UI), so do not split it into office.* or llm steps."""


def _kg_summary(project_id: str) -> str:
    from modules.kg import store

    try:
        nodes = store.search_nodes(project_id, type="Artifact", limit=20)
    except Exception:
        return "(knowledge graph unavailable)"
    return "\n".join(f"- {n.get('key')}: {json.dumps(n.get('props', {}))[:150]}" for n in nodes) or "(empty)"


def _tool_list() -> str:
    from modules.agent.router import _tools

    return "\n".join(f"- {t['name']} [{t.get('risk', '?')}]: {t.get('description', '')[:120]}" for t in _tools()) or "(no MCP tools connected)"


def input_hash(tool: str, args: dict) -> str:
    return hashlib.sha1((tool + json.dumps(args, sort_keys=True, default=str)).encode()).hexdigest()


def to_steps(run_id: str, raw: list[dict]) -> list[dict]:
    steps = []
    for i, s in enumerate(raw, 1):
        tool, args = s.get("tool") or "", s.get("args") or {}
        steps.append({
            "run_id": run_id, "step_id": f"s{i}", "parent_id": None,
            "description": s["description"], "route": INTERNAL.get(tool, "workflow" if "." in tool and tool.split(".")[0] in ("excel", "outlook", "word") else ""),
            "tool": tool, "args": args, "risk": s.get("risk") or "write", "produces": s.get("produces") or None,
            "input_hash": input_hash(tool, args), "status": "pending", "attempts": 0,
            "output": None, "evidence": None, "error": None, "started_at": None, "ended_at": None,
        })
    return steps


def plan(project_id: str, run_id: str, goal_masked: str) -> list[dict]:
    wfs = "\n".join(f"- {w['name']} [{w['risk']}]: {w['intent']} params={w['params']}" for w in load_workflows().values())
    today = dt.date.today().isoformat()
    prompt = f"""You are the planner of a desktop automation agent. Today is {today}.
Goal: {goal_masked}

Saved workflows (use the workflow name as "tool"):
{wfs}

MCP tools:
{_tool_list()}

Internal tools:
{INTERNAL_TOOLS}

Already in project knowledge (reuse instead of redoing):
{_kg_summary(project_id)}

{RULES.replace('{today}', today)}
Return {{"steps": [...]}}."""
    from modules.tools import desktop

    files = {k: v for k, v in desktop.find_files(goal_masked).items() if v}
    if files:
        prompt += f"\nFiles named in the goal (absolute paths on this PC): {json.dumps(files)}"
    out = llm.complete([{"role": "user", "content": prompt}], json_schema=SCHEMA)
    steps = to_steps(run_id, out.get("steps") or [])
    if not steps:
        raise ValueError("Planner returned no steps")
    return steps


if __name__ == "__main__":
    st = to_steps("r1", [{"description": "search", "tool": "research.search", "args": {"query": "x"}, "risk": "read"},
                         {"description": "save", "tool": "excel.upsert_rows", "args": {"rows": "{{s2.rows}}"}, "produces": {"type": "spreadsheet", "key": "k"}}])
    assert [s["step_id"] for s in st] == ["s1", "s2"] and st[0]["route"] == "browser" and st[1]["route"] == "workflow"
    assert st[0]["input_hash"] == input_hash("research.search", {"query": "x"}) and st[1]["risk"] == "write"
    print("planner ok")
