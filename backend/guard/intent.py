"""Intent & feasibility gate, run on every goal before planning, and on the plan after planning.

assess(goal, project_id=None) -> {
  "verdict": "proceed" | "proceed_partial" | "clarify" | "refuse",
  "intent": {"label": "benign|harmful|unclear", "confidence", "category", "reasons", "source"},
  "feasibility": {"level": "full|partial|none", "supported", "unsupported": [{"need","reason","alternative"}],
                  "needs_approval", "missing_inputs", "questions"},
  "injection": {"score", "flagged"},
  "summary": str,
}
check_plan(plan) -> {"ok": bool, "issues": [{"step_id","problem"}]}

Intent is judged by the policy safety model (see llm.guard_check). If that model is unreachable the
intent is "unclear": the run may proceed, but every non-read step then requires human approval.
Feasibility combines simple capability rules with an LLM analysis against the live tool catalog.
"""
from __future__ import annotations

import re
from pathlib import Path

from backend import llm

# Capability limits of this agent (what it physically cannot or must not do on the user's behalf).
_LIMITS = [
    (r"\b(pay|payment|purchase|buy|checkout|transfer money|send money|upi)\b",
     "making payments or purchases", "I can research options and prepare everything; you complete the payment yourself"),
    (r"\b(call|phone call|ring|sms|text message|whatsapp)\b(?! (it|the file|function))",
     "phone calls or SMS", "I can draft the message text for you to send"),
    (r"\b(log ?in|sign ?in|password|credentials|create an account|sign up)\b",
     "signing in or creating accounts", "you sign in yourself; I work with what is accessible without credentials"),
    (r"\bcaptcha\b", "solving CAPTCHAs", "you complete the CAPTCHA step"),
    (r"\b(every (day|week|morning|monday)|daily at|remind me|schedule (it|this) (for|at))\b",
     "running on a recurring schedule", "I run it now; you can re-run the saved workflow later"),
    (r"\b(print|printer|fax)\b", "printing or faxing", "I prepare the document ready to print"),
    (r"\b(physically|in person|in real life|drive (me|to)|cook (a|me|dinner|lunch|breakfast))\b", "acting in the physical world", "I can plan or research it"),
]
_SEND = re.compile(r"\b(send|email them|mail them|submit|apply on|post (it|on))\b", re.I)
_PATH = re.compile(r"(?:[A-Za-z]:\\[^\s\"'<>|?*]+|~[\\/][^\s\"'<>|?*]+)")

_FEAS_SCHEMA = {"type": "object", "properties": {
    "level": {"enum": ["full", "partial", "none"]},
    "supported": {"type": "array", "items": {"type": "string"}},
    "unsupported": {"type": "array", "items": {"type": "object", "properties": {
        "need": {"type": "string"}, "reason": {"type": "string"}, "alternative": {"type": "string"}}}},
    "needs_approval": {"type": "array", "items": {"type": "string"}},
    "missing_inputs": {"type": "array", "items": {"type": "string"}},
    "questions": {"type": "array", "items": {"type": "string"}}}}


def _rule_feasibility(goal: str) -> dict:
    g = goal.lower()
    unsupported, needs_approval, missing = [], [], []
    for pat, need, alt in _LIMITS:
        if re.search(pat, g):
            unsupported.append({"need": need, "reason": "outside what this agent is allowed or able to do", "alternative": alt})
    if _SEND.search(goal):
        needs_approval.append("sending or submitting anything (prepared as drafts first; nothing is sent without your approval)")
    if re.search(r"\b(delete|remove|clean ?up|wipe)\b", g):
        needs_approval.append("deleting files (always via the Recycle Bin, after your approval)")
    for p in _PATH.findall(goal):
        p = p.rstrip(".,;:)")
        if re.search(r"\b(read|open|from|in|summari[sz]e|analy[sz]e|clean|organi[sz]e|move|delete)\b", g) and not Path(p).expanduser().exists():
            missing.append(f"path not found on this PC: {p}")
    return {"unsupported": unsupported, "needs_approval": needs_approval, "missing_inputs": missing}


def _merge_unsupported(a: list[dict], b: list[dict]) -> list[dict]:
    seen, out = set(), []
    for u in a + b:
        k = u.get("need", "").lower()[:40]
        if k and k not in seen:
            seen.add(k)
            out.append(u)
    return out


def assess(goal: str, project_id: str | None = None) -> dict:
    goal = (goal or "").strip()
    rules = _rule_feasibility(goal)

    # 1) intent via the policy safety model
    intent = {"label": "unclear", "confidence": 0.0, "category": "", "reasons": [], "source": "none"}
    g = llm.guard_check(f"REQUEST: {goal}", project_id=project_id)
    if g.get("available"):
        intent = {"label": "benign" if g["safe"] else "harmful", "confidence": g.get("confidence", 0.8),
                  "category": g.get("category", ""), "reasons": [g.get("rationale", "")] if g.get("rationale") else [],
                  "source": "safety-model"}
    else:
        intent["reasons"] = ["safety model unavailable: every non-read step will require approval"]

    # 2) prompt injection in the goal itself
    score = llm.prompt_injection_score(goal)
    injection = {"score": score, "flagged": bool(score is not None and score > 0.9)}
    if injection["flagged"] and intent["label"] == "benign":
        intent["label"] = "unclear"
        intent["reasons"].append(f"text looks like a prompt-injection attempt (score {score:.2f})")

    # 3) feasibility vs the live tool catalog
    feas = {"level": "full", "supported": [], "unsupported": rules["unsupported"],
            "needs_approval": rules["needs_approval"], "missing_inputs": rules["missing_inputs"], "questions": []}
    if intent["label"] != "harmful" and llm.available():
        try:
            from backend.tools.catalog import catalog_for_prompt
            out = llm.complete([
                {"role": "system", "content":
                    "You check whether an autonomous Windows PC agent can achieve a user's goal with its tools. "
                    "Split the goal into concrete parts; mark each as supported (the tools can do it) or unsupported "
                    "(no tool can do it, e.g. payments, phone calls, physical actions, sites that need the user's login). "
                    "For unsupported parts give a practical alternative the agent CAN do. List actions that will need human "
                    "approval (sending, deleting, installing, running non-read commands). List inputs the user must provide "
                    "only if the goal truly cannot start without them (do not ask about details the agent can research or "
                    "sensibly default). level: full = all parts supported, partial = some, none = nothing useful possible.\n\n"
                    "TOOLS:\n" + catalog_for_prompt()},
                {"role": "user", "content": f"GOAL: {goal}"}],
                json_schema=_FEAS_SCHEMA, project_id=project_id, max_tokens=1500)
            feas["supported"] = [str(s) for s in out.get("supported", [])][:10]
            feas["unsupported"] = _merge_unsupported(rules["unsupported"],
                                                     [u for u in out.get("unsupported", []) if isinstance(u, dict)])
            feas["needs_approval"] = list(dict.fromkeys(rules["needs_approval"] + [str(x) for x in out.get("needs_approval", [])]))[:8]
            feas["missing_inputs"] = list(dict.fromkeys(rules["missing_inputs"] + [str(x) for x in out.get("missing_inputs", [])]))[:6]
            feas["questions"] = [str(q) for q in out.get("questions", [])][:4]
            feas["level"] = out.get("level", "full") if out.get("level") in ("full", "partial", "none") else "full"
        except Exception as e:
            feas["supported"] = ["(feasibility model unavailable; rule checks only)"]
            feas["error"] = str(e)[:200]
    if feas["unsupported"] and feas["level"] == "full":
        feas["level"] = "partial"
    if any("path not found" in m for m in feas["missing_inputs"]) and feas["level"] == "full":
        feas["level"] = "partial"

    # 4) verdict
    if intent["label"] == "harmful" and intent.get("confidence", 0) >= 0.5:
        verdict = "refuse"
    elif feas["level"] == "none":
        verdict = "clarify"
    elif feas["level"] == "partial" or intent["label"] == "unclear":
        verdict = "proceed_partial"
    else:
        verdict = "proceed"
    return {"verdict": verdict, "intent": intent, "feasibility": feas, "injection": injection,
            "summary": _summary(verdict, intent, feas)}


def _summary(verdict: str, intent: dict, feas: dict) -> str:
    if verdict == "refuse":
        why = intent["reasons"][0] if intent["reasons"] else intent.get("category", "")
        return f"I won't do this: it looks harmful ({intent.get('category') or 'policy'}). {why}".strip()
    parts = []
    if verdict == "clarify":
        parts.append("I can't do this with the tools I have.")
    elif verdict == "proceed_partial":
        parts.append("I can do part of this.")
    else:
        parts.append("Looks safe and doable.")
    for u in feas["unsupported"][:3]:
        parts.append(f"Not possible: {u['need']} -> {u.get('alternative', '')}".rstrip(" ->"))
    if feas["needs_approval"]:
        parts.append("I'll ask before: " + "; ".join(feas["needs_approval"][:3]))
    for m in feas["missing_inputs"][:2]:
        parts.append(f"Missing: {m}")
    return " ".join(parts)


_CLARIFY_SCHEMA = {"type": "object", "properties": {
    "needs_details": {"type": "boolean"},
    "questions": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "question": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"}}}}}}}


def clarifying_questions(goal: str, project_id: str | None = None) -> list[dict]:
    """Questions to ask BEFORE acting when a real-world task is missing details only the user can decide
    (e.g. booking: date, time, airline; buying: size, budget). [] when the task is clear enough or the LLM is off."""
    if not llm.available() or not goal.strip():
        return []
    if re.search(r"(?i)\b(your (choice|call|pick)|you (decide|choose|pick)|up to you|anything (is )?fine|any (one|of them)|"
                 r"whatever you (think|like|want)|surprise me|no preference|doesn'?t matter|don'?t care)\b", goal):
        return []  # the user delegated the remaining choices
    try:
        out = llm.complete([
            {"role": "system", "content":
                "You decide whether an autonomous agent must ask the user for details BEFORE it starts a task.\n"
                "Ask ONLY when the task commits to something in the real world (booking, buying, ordering, reserving, "
                "applying, sending, scheduling, signing up) AND a choice that changes the outcome is missing and cannot be "
                "sensibly defaulted: e.g. travel date / time window / passengers / airline or class; product size, colour, "
                "budget or brand; recipient. Never ask about anything the goal already states, never ask for passwords, "
                "OTPs, card numbers or other secrets, never ask for research, summaries, file or office tasks.\n"
                "If the user already wrote 'your choice', 'anything', 'you decide' or similar, ask nothing.\n"
                "At most 4 short questions. For each give 2-5 concrete example options. ids: q1, q2, ...\n"
                'Return {"needs_details": bool, "questions": [{"id","question","options"}]}.'},
            {"role": "user", "content": f"TASK: {goal}"}],
            json_schema=_CLARIFY_SCHEMA, project_id=project_id, max_tokens=900)
    except Exception:
        return []
    if not isinstance(out, dict) or not out.get("needs_details"):
        return []
    qs = []
    for i, q in enumerate(out.get("questions") or [], 1):
        if isinstance(q, dict) and str(q.get("question", "")).strip():
            qs.append({"id": f"q{i}", "question": str(q["question"]).strip()[:200],
                       "options": [str(o)[:60] for o in (q.get("options") or []) if str(o).strip()][:5]})
    return qs[:4]


def check_plan(plan: list[dict]) -> dict:
    """Per-step feasibility: every tool exists and required args are present (references allowed)."""
    from backend.tools.catalog import all_tools
    tools = all_tools()
    issues = []
    for s in plan:
        t = tools.get(s.get("tool", ""))
        if not t:
            issues.append({"step_id": s.get("step_id"), "problem": f"no tool named {s.get('tool')!r}"})
            continue
        args = s.get("args") or {}
        for req in t["required"]:
            if req not in args or args[req] in (None, ""):
                issues.append({"step_id": s.get("step_id"), "problem": f"missing argument {req!r} for {s['tool']}"})
    return {"ok": not issues, "issues": issues}


if __name__ == "__main__":
    r = _rule_feasibility("Find a laptop under 50k and buy it, then call the seller")
    needs = {u["need"] for u in r["unsupported"]}
    assert "making payments or purchases" in needs and "phone calls or SMS" in needs, needs
    r = _rule_feasibility(r"Summarize Z:\nope\missing.docx and email them")
    assert r["missing_inputs"] and r["needs_approval"], r
    assert not _rule_feasibility("Find three internships and save them to a spreadsheet")["unsupported"]
    a = assess("Find three internships for a third-year CS student, save to a spreadsheet and draft emails")
    assert a["verdict"] in ("proceed", "proceed_partial"), a
    print("guard.intent self-check OK:", a["verdict"], "|", a["summary"][:160])
