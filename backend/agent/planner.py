"""goal -> list[Step]. LLM planner over the live tool catalog, with a template planner as fallback.

Step args may reference earlier outputs: "{{s2}}" (whole output), "{{s2.rows}}", and inside a `foreach`
step "{{item.Name}}". A string that is exactly one reference is replaced by the raw value.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from backend import llm
from backend.tools import catalog

_PLAN_SCHEMA = {"type": "object", "properties": {"steps": {"type": "array", "items": {"type": "object", "properties": {
    "id": {"type": "string"}, "description": {"type": "string"}, "tool": {"type": "string"}, "args": {"type": "object"},
    "foreach": {"type": "string"}, "produces": {"type": "object", "properties": {"type": {"type": "string"}, "key": {"type": "string"}}}},
    "required": ["id", "description", "tool", "args"]}}}, "required": ["steps"]}

_RULES = """Rules:
- References to earlier outputs: "{{s1}}", "{{s1.results}}", "{{s1.results.0.url}}"; inside a foreach step "{{item.url}}".
  To fetch several pages use ONE research.fetch step with "foreach": "{{sN.results}}" (or a list from llm.generate) and url "{{item.url}}".
- Use ONLY the tools listed. Use as few steps as needed (typically 3-9). Ids are s1, s2, ...
- Gather facts with research.search, then research.fetch the 2-4 most promising URLs when details (eligibility, deadlines, prices) matter.
- Time matters: it is YEAR_NOW now. Search for current / upcoming items (YEAR_NOW, YEAR_NEXT); never put past years in queries.
- Research well: run 2-3 research.search steps with different, specific queries (e.g. named official programs, the official
  careers site, "<topic> eligibility deadline"), then fetch the most promising official pages with ONE foreach research.fetch.
  Job boards (Indeed, LinkedIn, Glassdoor) block automated fetching: prefer company / official pages and use snippets otherwise.
- Draft emails only to addresses found in the data; otherwise the draft uses a placeholder recipient for the human to fill in.
- When an llm.generate step extracts facts from fetched pages, ALSO pass the search results ("inputs": {"search": "{{s1}}", "pages": "{{s3}}"})
  so it can fall back to snippets when pages are blocked. Do extraction + comparison + ranking in ONE llm.generate step when possible.
- Use llm.generate to extract / compare / rank / write. Give it a precise instruction, inputs as references, and a JSON schema
  (e.g. {"rows": [{"Name": "", "Link": "", ...}]} or {"emails": [{"to": "", "subject": "", "body": "", "key": ""}]}).
- Spreadsheets: office.excel_upsert_rows with a relative path like "internships.xlsx", a sheet name, a key_column that exists in every row
  (e.g. "Name"), and rows "{{sN.rows}}". After it, verification is automatic; do not add a separate read step.
- Emails: draft with office.outlook_create_draft using foreach over the generated emails ("foreach": "{{sN.emails}}",
  args {"to": "{{item.to}}", "subject": "{{item.subject}}", "body": "{{item.body}}"}). Never send unless the user explicitly asked to send;
  sending uses office.outlook_send_draft and will be approved by a human.
- Files: relative paths go to the project folder; use absolute paths only when the user gave them. Deleting uses files.delete (Recycle Bin).
- Desktop apps without an API: windows.explore {app, task}.
- Shell only when no dedicated tool fits.
- Every step that creates a lasting result gets "produces": {"type": "<spreadsheet|document|drafts|file|dataset|answer>", "key": "<stable key>"}.
- Never include steps for things the agent cannot do (payments, calls, logins); the user was told separately.
- Output JSON only: {"steps": [...]}."""


def _rules() -> str:
    y = datetime.now().year
    return _RULES.replace("YEAR_NOW", str(y)).replace("YEAR_NEXT", str(y + 1))


def _hash(tool: str, args: dict) -> str:
    return hashlib.sha1(f"{tool}|{json.dumps(args, sort_keys=True, default=str)}".encode()).hexdigest()[:16]


def _finalize(steps: list[dict], start: int = 1, keep_ids: set | None = None) -> list[dict]:
    """Normalize planner output: ids s{start}.., references renumbered (ids in keep_ids are left alone)."""
    tools = catalog.all_tools()
    keep_ids = keep_ids or set()
    if isinstance(steps, dict):
        steps = steps.get("steps") or [steps]
    steps = [s for s in (steps or []) if isinstance(s, dict) and s.get("tool")]  # drop malformed entries
    for s in steps:
        if not isinstance(s.get("args"), dict):
            s["args"] = {}
    id_map = {}
    for i, s in enumerate(steps):
        raw_id = str(s.get("id") or f"s{start + i}")
        if raw_id not in keep_ids:
            id_map[raw_id] = f"s{start + i}"
    out = []
    for i, s in enumerate(steps):
        sid = f"s{start + i}"
        blob = json.dumps({"args": s.get("args") or {}, "foreach": s.get("foreach")})
        blob = re.sub(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)", lambda m: "{{" + id_map.get(m.group(1), m.group(1)), blob)
        fixed = json.loads(blob)
        tool = str(s.get("tool", "")).strip()
        spec = tools.get(tool, {})
        step = {"step_id": sid, "description": str(s.get("description", tool))[:300], "tool": tool,
                "args": fixed["args"], "route": spec.get("route", "kg"), "risk": spec.get("risk", "write"),
                "produces": s.get("produces") or None, "status": "pending"}
        if fixed.get("foreach"):
            step["foreach"] = fixed["foreach"]
        step["input_hash"] = _hash(tool, step["args"]) if "{{" not in json.dumps(step["args"]) else None
        out.append(step)
    return out


def plan(goal: str, project_id: str | None = None, assessment: dict | None = None, context: str = "") -> tuple[list[dict], str]:
    """Returns (steps, source) where source is 'llm' or 'template'."""
    if llm.available():
        notes = ""
        if assessment:
            f = assessment.get("feasibility", {})
            if f.get("unsupported"):
                notes = "Parts that are NOT possible (skip them, do the feasible alternative instead): " + \
                        "; ".join(f"{u.get('need')} -> {u.get('alternative', '')}" for u in f["unsupported"])
        today = datetime.now().strftime("%Y-%m-%d")
        for attempt in range(2):
            try:
                out = llm.complete([
                    {"role": "system", "content": f"You are the planner of an autonomous agent on a Windows PC. Today is {today}.\n"
                     f"TOOLS:\n{catalog.catalog_for_prompt()}\n\n{_rules()}"},
                    {"role": "user", "content": f"GOAL: {goal}\n{notes}\n{context}".strip()}],
                    json_schema=_PLAN_SCHEMA, project_id=project_id, max_tokens=4000,
                    reasoning_effort="medium" if attempt == 0 else "low")
                steps = _finalize(out.get("steps", []) if isinstance(out, dict) else out)
                from backend.guard.intent import check_plan
                chk = check_plan(steps)
                if steps and chk["ok"]:
                    return steps, "llm"
                if steps and attempt == 0:
                    context = "Your previous plan had problems, fix them: " + "; ".join(i["problem"] for i in chk["issues"])
                    continue
                if steps:
                    return [s for s in steps if s["tool"] in catalog.all_tools()], "llm"
            except Exception:
                continue
    return template_plan(goal), "template"


def repair(goal: str, plan: list[dict], failed: dict, error: str, project_id: str | None = None) -> list[dict]:
    """Re-plan after a step failed. Returns steps that replace the failed step and the remaining work;
    finished steps keep their ids and can be referenced as {{sN}}."""
    if not llm.available():
        return []

    def brief(s: dict) -> str:
        out = json.dumps(s.get("output"), ensure_ascii=False, default=str)[:700] if s.get("output") else ""
        return f"{s['step_id']} [{s['status']}] {s['tool']} args={json.dumps(s.get('args'), default=str)[:300]} output={out}"

    done = [s for s in plan if s["status"] in ("done", "reused", "degraded")]
    next_n = 1 + max(int(re.sub(r"\D", "", s["step_id"]) or 0) for s in plan)
    remaining = [s for s in plan if s["status"] in ("pending", "failed") and s["step_id"] != failed["step_id"]]
    system = ("You repair the plan of an autonomous agent after a step failed.\nTOOLS:\n" + catalog.catalog_for_prompt() +
              "\n\n" + _rules() + f"\nFinished steps can be referenced by their ids. New step ids MUST be s{next_n}, s{next_n + 1}, ... "
              "Return ONLY the new steps that replace the failed step and all remaining work. Change the approach (different "
              "query or source, use search snippets instead of blocked pages, a different tool) instead of repeating what failed.")
    user = (f"GOAL: {goal}\n\nFINISHED STEPS:\n" + "\n".join(brief(s) for s in done) +
            f"\n\nFAILED STEP:\n{brief(failed)}\nERROR: {error[:600]}\n\nREMAINING (not run yet):\n" +
            "\n".join(f"{s['step_id']} {s['tool']}: {s['description']}" for s in remaining))
    try:
        out = llm.complete([{"role": "system", "content": system}, {"role": "user", "content": user}],
                           json_schema=_PLAN_SCHEMA, project_id=project_id, max_tokens=4000)
    except Exception:
        return []
    steps = _finalize(out.get("steps", []) if isinstance(out, dict) else out, start=next_n, keep_ids={s["step_id"] for s in done})
    return [s for s in steps if s["tool"] in catalog.all_tools()]


# ---------------------------------------------------------------- template planner (no LLM)
def _slug(text: str, n: int = 4) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in
             {"the", "a", "an", "and", "for", "to", "of", "find", "me", "my", "save", "them", "in", "on", "with", "best", "each"}]
    return "_".join(words[:n]) or "results"


def template_plan(goal: str) -> list[dict]:
    g = goal.lower()
    steps: list[dict] = []
    m = re.search(r"\b(?:find|search(?: for)?|research|look up|compare|get)\s+(.+?)(?:,|\.|\band\b (?:save|put|write|draft|prepare|compare|rank|email)|$)", g)
    topic = (m.group(1) if m else goal)[:120].strip()
    wants_sheet = bool(re.search(r"spreadsheet|excel|sheet|xlsx|table", g))
    wants_doc = bool(re.search(r"\b(doc|document|report|word|summary|summari[sz]e)\b", g))
    wants_mail = bool(re.search(r"\b(e-?mails?|drafts?|mail)\b", g))
    wants_research = bool(m) or wants_sheet or wants_mail or "internship" in g
    path_m = re.search(r"([A-Za-z]:\\[^\s\"']+|~[\\/][^\s\"']+)", goal)

    if re.search(r"\b(list|show)\b.*\b(files|folder|directory)\b", g) and path_m:
        steps.append({"description": f"List files in {path_m.group(1)}", "tool": "files.list_dir", "args": {"path": path_m.group(1)}})
        steps.append({"description": "Summarize the folder contents", "tool": "llm.generate",
                      "args": {"instruction": goal, "inputs": "{{s1}}", "format": "text"}})
        return _finalize(steps)
    if re.search(r"\b(summari[sz]e|read)\b", g) and path_m and not wants_research:
        steps.append({"description": f"Read {path_m.group(1)}", "tool": "files.read_text", "args": {"path": path_m.group(1)}})
        steps.append({"description": "Summarize it", "tool": "llm.generate", "args": {"instruction": goal, "inputs": "{{s1}}", "format": "text"}})
        steps.append({"description": "Save the summary", "tool": "office.word_write_doc",
                      "args": {"path": "summary.docx", "text": "{{s2.text}}"}, "produces": {"type": "document", "key": "summary.docx"}})
        return _finalize(steps)
    app_m = re.search(r"\b(?:in|open|using)\s+(notepad|paint|calculator|settings|wordpad)\b", g)
    if app_m and not wants_research:
        steps.append({"description": f"Do it in {app_m.group(1)}", "tool": "windows.explore", "args": {"app": app_m.group(1), "task": goal}})
        return _finalize(steps)

    if wants_research:
        q = topic
        if "internship" in g:
            q = f"{topic} 2026 apply eligibility deadline"
        steps.append({"description": f"Search the web for {topic}", "tool": "research.search", "args": {"query": q, "k": 8},
                      "produces": {"type": "dataset", "key": f"search:{_slug(q, 8)}"}})
        steps.append({"description": "Extract, compare and rank the best matches", "tool": "llm.generate",
                      "args": {"instruction": f"From the search results, pick and rank the best matches for: {goal}. "
                               "Columns: Name, Organization, Link, Eligibility, Deadline, Summary, Rank.",
                               "inputs": "{{s1}}", "format": "json",
                               "schema": {"rows": [{"Name": "", "Organization": "", "Link": "", "Eligibility": "", "Deadline": "", "Summary": "", "Rank": 1}]}}})
        rows_ref = f"{{{{s{len(steps)}.rows}}}}"
        rows_step = len(steps)
        if wants_sheet or not (wants_doc or wants_mail):
            name = f"{_slug(topic)}.xlsx"
            steps.append({"description": f"Save the ranked list to {name}", "tool": "office.excel_upsert_rows",
                          "args": {"path": name, "sheet": "Shortlist", "key_column": "Name", "rows": rows_ref},
                          "produces": {"type": "spreadsheet", "key": f"{name}#Shortlist"}})
        if wants_doc:
            steps.append({"description": "Write a comparison report", "tool": "llm.generate",
                          "args": {"instruction": f"Write a concise comparison report for: {goal}", "inputs": rows_ref, "format": "text"}})
            steps.append({"description": "Save the report as a Word document", "tool": "office.word_write_doc",
                          "args": {"path": f"{_slug(topic)}_report.docx", "text": f"{{{{s{len(steps)}.text}}}}"},
                          "produces": {"type": "document", "key": f"{_slug(topic)}_report.docx"}})
        if wants_mail:
            steps.append({"description": "Write a personalized email for each match", "tool": "llm.generate",
                          "args": {"instruction": "Write one personalized application email per row (to, subject, body, key=row Name).",
                                   "inputs": f"{{{{s{rows_step}}}}}", "format": "json",
                                   "schema": {"emails": [{"to": "", "subject": "", "body": "", "key": ""}]}}})
            steps.append({"description": "Create the email drafts for review", "tool": "office.outlook_create_draft",
                          "foreach": f"{{{{s{len(steps)}.emails}}}}",
                          "args": {"to": "{{item.to}}", "subject": "{{item.subject}}", "body": "{{item.body}}"},
                          "produces": {"type": "drafts", "key": f"drafts:{_slug(topic)}"}})
        return _finalize(steps)

    steps.append({"description": "Answer from project knowledge", "tool": "kg.ask", "args": {"question": goal}})
    steps.append({"description": "Search the web", "tool": "research.search", "args": {"query": goal[:200]}})
    steps.append({"description": "Write the answer", "tool": "llm.generate", "args": {"instruction": goal, "inputs": ["{{s1}}", "{{s2}}"], "format": "text"}})
    return _finalize(steps)


if __name__ == "__main__":
    p = template_plan("Find three internship opportunities suitable for a third-year computer science student, compare their "
                      "eligibility criteria, save the best matches to a spreadsheet and prepare personalized draft emails for each opportunity.")
    tools = [s["tool"] for s in p]
    assert tools == ["research.search", "llm.generate", "office.excel_upsert_rows", "llm.generate", "office.outlook_create_draft"], tools
    assert p[2]["args"]["rows"] == "{{s2.rows}}" and p[4]["foreach"] == "{{s4.emails}}", p
    assert p[3]["args"]["inputs"] == "{{s2}}"
    f = _finalize([{"id": "a", "tool": "research.search", "args": {"query": "x"}}, {"id": "b", "tool": "llm.generate", "args": {"instruction": "y", "inputs": "{{a.results}}"}}])
    assert f[1]["args"]["inputs"] == "{{s1.results}}", f
    print("agent.planner self-check OK")
