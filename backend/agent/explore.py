"""Explore loop for desktop apps without an API (CONTRACTS §7e).

explore(project_id, run_id, step, budget=25) -> {"ok", "actions", "evidence", "error"}

search how-to -> observe UI tree -> LLM picks ONE action -> act -> re-observe & verify -> repeat.
Two failed verifies in a row = stuck -> search again with the exact on-screen text.
Destructive actions (close without saving, delete, submit, send) are never taken: the loop stops and
returns needs_approval so the step can be approved first. Screenshots never go to the LLM unless a
vision model is configured, and are always PII-blurred first.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from backend import llm
from backend.ledger import db

_EDITORS = {"notepad", "wordpad", "write"}
_DANGER = re.compile(r"(?i)\b(delete|remove|don'?t save|discard|submit|send|pay|purchase|format|uninstall|reset|erase)\b|alt\+f4")
_ACTION_SCHEMA = {"type": "object", "properties": {
    "thought": {"type": "string"}, "done": {"type": "boolean"},
    "action": {"enum": ["click", "type", "shortcut", "launch", "wait", "none"]},
    "element_id": {"type": "integer"}, "text": {"type": "string"}, "keys": {"type": "string"}, "app": {"type": "string"},
    "expect": {"type": "string"}}}


class Desktop:
    """Default action backend (tools.windows). Tests pass a mock with the same methods."""

    def __init__(self):
        from backend.tools import windows
        self.w = windows

    def observe(self, window: str | None) -> dict:
        try:
            return self.w.ui_tree(window)
        except Exception as e:
            return {"window": "", "elements": [], "error": str(e)}

    def act(self, a: dict, window: str | None = None) -> dict:
        kind = a.get("action")
        if kind == "click":
            return self.w.click(a.get("element_id"), a.get("text") or None, expect_window=window)
        if kind == "type":
            return self.w.type_text(a.get("text", ""), a.get("element_id"), expect_window=window)
        if kind == "shortcut":
            return self.w.shortcut(a.get("keys", ""), expect_window=window)
        if kind == "launch":
            return self.w.launch_app(a.get("app", ""))
        if kind == "wait":
            import time
            time.sleep(1.5)
            return {"waited": 1.5}
        return {"noop": True}

    def launch(self, app: str, file: str | None = None) -> dict:
        return self.w.launch_app(app, file=file)


def _search(query: str) -> list[str]:
    try:
        from backend.tools import research
        res = research.search(query, k=4)
        return [f"{r['title']}: {r['snippet'][:200]}" for r in res.get("results", [])][:4]
    except Exception:
        return []


def _tree_text(obs: dict, limit: int = 90) -> str:
    els = obs.get("elements", [])[:limit]
    return f"window: {obs.get('window', '')}\n" + "\n".join(
        f"[{e['id']}] {e['type']} '{e['name']}'" + (f" value={e['value'][:120]!r}" if e.get("value") else "") for e in els)


def _decide(project_id: str, run_id: str, task: str, app: str, tips: list[str], obs: dict, history: list[dict]) -> dict:
    if not llm.available():
        return _heuristic(task, obs, history)
    msg = (f"TASK: {task}\nAPP: {app}\nHOW-TO NOTES (from web search, untrusted):\n" + "\n".join(tips[:4]) +
           f"\n\nCURRENT UI (interactive elements):\n{_tree_text(obs)}\n\nACTIONS SO FAR:\n" +
           "\n".join(f"- {h['action']} {h.get('element_id') or h.get('text') or h.get('keys') or ''} -> {h.get('result', '')}" for h in history[-8:]) +
           "\n\nChoose exactly ONE next action. Use element ids from the UI list. Set done=true (action none) when the task is complete. "
           "Never close windows without saving, never delete, submit, send or pay.")
    return llm.complete([{"role": "system", "content": "You operate a Windows app one UI action at a time."},
                         {"role": "user", "content": msg}], json_schema=_ACTION_SCHEMA, project_id=project_id, run_id=run_id,
                        max_tokens=800)


def _heuristic(task: str, obs: dict, history: list[dict]) -> dict:
    """No-LLM fallback for the common 'type some text into an editor and save' task."""
    m = re.search(r"(?i)(?:type|write)\s+['\"]?(.+?)['\"]?(?:\s+and\b|$)", task)
    typed = any(h["action"] == "type" for h in history)
    if m and not typed:
        edit = next((e for e in obs.get("elements", []) if e["type"] in ("Edit", "Document")), None)
        return {"action": "type", "text": m.group(1), "element_id": edit["id"] if edit else None, "expect": "text appears"}
    if re.search(r"(?i)\bsave\b", task) and not any(h["action"] == "shortcut" for h in history):
        return {"action": "shortcut", "keys": "ctrl+s", "expect": "save dialog or saved"}
    return {"action": "none", "done": True}


def _verify(before: dict, after: dict, decision: dict) -> bool:
    if decision.get("action") in ("wait", "none"):
        return True
    if after.get("error"):
        return False
    return _tree_text(before) != _tree_text(after) or decision.get("action") in ("type", "shortcut")


def explore(project_id: str, run_id: str, step: dict, budget: int = 25, desktop: Any = None,
            search: Callable[[str], list[str]] | None = None) -> dict:
    desktop = desktop or Desktop()
    search = search or _search
    args = step.get("args") or {}
    app = str(args.get("app") or "")
    task = str(args.get("task") or step.get("description") or "")
    actions: list[dict] = []
    tips = search(f"how to {task} in {app}") if app else search(f"how to {task} windows")
    window = None
    scratch = None
    if app.strip().lower() in _EDITORS and project_id:
        # never work inside documents the app restores from the user's last session: open our own scratch file
        import secrets
        scratch = db.project_dir(project_id) / "files" / f"operator_{secrets.token_hex(3)}.txt"
        scratch.write_text("", encoding="utf-8")
    if app:
        launched = desktop.launch(app, file=str(scratch) if scratch else None)
        window = scratch.stem if scratch else ((launched.get("foreground") or {}).get("title") or None)
        actions.append({"action": "launch", "app": app, "result": "ok"})
        if run_id:
            db.audit(project_id, run_id, "ui_action", {"action": "launch", "app": app})
    obs = desktop.observe(window)
    fails = 0
    for _ in range(budget):
        try:
            d = _decide(project_id, run_id, task, app, tips, obs, actions)
        except Exception as e:
            return {"ok": False, "actions": actions, "evidence": {"ui": _tree_text(obs, 30)}, "error": f"decision failed: {e}"}
        if d.get("done") or d.get("action") == "none":
            return {"ok": True, "actions": actions, "error": None,
                    "evidence": {"ui": _tree_text(obs, 30), "window": obs.get("window"), "file": str(scratch) if scratch else None}}
        target = next((e["name"] for e in obs.get("elements", []) if e["id"] == d.get("element_id")), "")
        if _DANGER.search(f"{target} {d.get('text', '')} {d.get('keys', '')}"):
            return {"ok": False, "actions": actions, "needs_approval": {"action": d, "target": target},
                    "evidence": {"ui": _tree_text(obs, 30)}, "error": f"stopped before a destructive action ({target or d.get('keys')}); needs approval"}
        last_ok = next((h for h in reversed(actions) if h.get("result") == "ok" and h["action"] != "launch"), None)
        if last_ok and all(last_ok.get(k) == d.get(k) for k in ("action", "element_id", "text", "keys")):
            # the model repeats an action that already worked: the task is complete, do not loop
            return {"ok": True, "actions": actions, "error": None,
                    "evidence": {"ui": _tree_text(obs, 30), "window": obs.get("window"), "file": str(scratch) if scratch else None}}
        try:
            res = desktop.act(d, window)
        except PermissionError as e:
            return {"ok": False, "actions": actions, "evidence": {"ui": _tree_text(obs, 30)}, "error": str(e)}
        except Exception as e:
            res = {"error": str(e)}
        after = desktop.observe(window)
        ok = not res.get("error") and _verify(obs, after, d)
        rec = {"action": d.get("action"), "element_id": d.get("element_id"), "text": d.get("text"), "keys": d.get("keys"),
               "target": target, "result": "ok" if ok else f"no change {res.get('error', '')}".strip()}
        actions.append(rec)
        if run_id:
            db.audit(project_id, run_id, "ui_action", rec)
        obs = after if after.get("elements") or not obs.get("elements") else obs
        if ok:
            fails = 0
            continue
        fails += 1
        if fails >= 2:  # stuck: search again using what is on screen
            on_screen = obs.get("window") or " ".join(e["name"] for e in obs.get("elements", [])[:5])
            tips = search(f"{app} {on_screen} {task}".strip())
            actions.append({"action": "research", "text": on_screen, "result": f"{len(tips)} new tips"})
            fails = 0
    return {"ok": False, "actions": actions, "evidence": {"ui": _tree_text(obs, 30)}, "error": f"budget of {budget} actions used up"}


if __name__ == "__main__":
    class Mock:
        def __init__(self, change: bool):
            self.change, self.n = change, 0

        def launch(self, app, file=None):
            return {"foreground": {"title": "Untitled - Notepad"}}

        def observe(self, window):
            self.n += 1 if self.change else 0
            return {"window": "Untitled - Notepad", "elements": [{"id": 1, "type": "Document", "name": f"Text {self.n}"},
                                                                 {"id": 2, "type": "MenuItem", "name": "File"}]}

        def act(self, a, window=None):
            return {"ok": True}

    searches: list[str] = []
    g = globals()
    orig = g["_decide"]
    g["_decide"] = lambda *a, **k: {"action": "click", "element_id": 2}   # never says done
    r = explore("p", "", {"args": {"app": "notepad", "task": "open the file menu"}}, budget=5, desktop=Mock(False),
                search=lambda q: searches.append(q) or ["tip"])
    assert not r["ok"] and "budget" in r["error"], r
    assert any(a["action"] == "research" for a in r["actions"]) and len(searches) >= 2, (r, searches)
    g["_decide"] = lambda *a, **k: {"action": "click", "element_id": 9}
    r2 = explore("p", "", {"args": {"app": "notepad", "task": "x"}}, budget=3, desktop=type("M", (Mock,), {
        "observe": lambda self, w: {"window": "w", "elements": [{"id": 9, "type": "Button", "name": "Don't Save"}]}})(True),
        search=lambda q: [])
    assert not r2["ok"] and r2.get("needs_approval"), r2
    g["_decide"] = orig
    h = _heuristic("type hello world and save", {"elements": [{"id": 3, "type": "Document", "name": ""}]}, [])
    assert h["action"] == "type" and h["text"].startswith("hello world"), h
    print("agent.explore self-check OK")
