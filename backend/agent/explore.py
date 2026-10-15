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
    "action": {"enum": ["click", "type", "shortcut", "wait", "none"]},
    "element_id": {"type": "integer"}, "text": {"type": "string"}, "keys": {"type": "string"},
    "expect": {"type": "string"}}}


class Desktop:
    """Default action backend (tools.windows). Tests pass a mock with the same methods."""

    def __init__(self):
        from backend.tools import windows
        self.w = windows

    def observe(self, window) -> dict:
        try:
            return self.w.ui_tree(window)
        except Exception as e:
            return {"window": "", "elements": [], "error": str(e)}

    def is_open(self, window) -> bool:
        return bool(window) and self.w.window_exists(window)

    def focus(self, window) -> None:
        self.w.focus(window)

    def act(self, a: dict, window: str | None = None) -> dict:
        kind = a.get("action")
        if kind == "click":
            return self.w.click(a.get("element_id"), a.get("text") or None, expect_window=window)
        if kind == "type":
            return self.w.type_text(a.get("text", ""), a.get("element_id"), expect_window=window)
        if kind == "shortcut":
            return self.w.shortcut(a.get("keys", ""), expect_window=window)
        if kind == "wait":
            import time
            time.sleep(1.5)
            return {"waited": 1.5}
        return {"noop": True}

    def launch(self, app: str, file: str | None = None) -> dict:
        return self.w.launch_app(app, file=file)


# app windows this agent opened, per run: later steps of the same run reuse them instead of launching again
_opened: dict[tuple[str, str], Any] = {}


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
           "\n\nThe app is ALREADY OPEN and in front; never try to open or launch it again. "
           "Choose exactly ONE next action. Use element ids from the UI list (click a field or placeholder, then type into it). "
           "Prefer well-known keyboard shortcuts when they are reliable (e.g. ctrl+n new, ctrl+m new slide, ctrl+s save). "
           "Do not repeat an action that produced 'no change'; try a different element or approach. "
           "Set done=true (action none) as soon as the task is complete. "
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


_NEEDS_SCREEN = re.compile(r"(?i)\b(play|game|chess|tic[\s-]?tac|sudoku|solitaire|minesweeper|draw|sketch|paint (a|an|the)|"
                           r"wait (for|until) (me|my|the user)|my (move|turn)|watch the screen|on (the )?screen)\b")
_VISION_SCHEMA = {"type": "object", "properties": {
    "thought": {"type": "string"}, "done": {"type": "boolean"},
    "action": {"enum": ["click", "type", "keys", "wait_for_change", "none"]},
    "x": {"type": "number"}, "y": {"type": "number"}, "text": {"type": "string"}, "keys": {"type": "string"}}}
_VISION_RULES = """You control a Windows app by looking at screenshots of it. Magenta ruler lines mark a 0-1000 coordinate grid
(x left->right, y top->bottom). Each turn return ONE action as JSON:
- {"action":"click","x":<0-1000>,"y":<0-1000>}: click the CENTER of the target.
- {"action":"type","text":"..."} / {"action":"keys","keys":"enter"}
- {"action":"wait_for_change"}: wait until something on screen changes (e.g. the other player / the user makes a move).
- {"done":true,"action":"none","thought":"<outcome>"} when the task is finished (e.g. game over: say who won).
Think about the state first in "thought". Never click the same thing twice in a row unless it had no effect.
Never close windows, delete, buy, send or submit forms."""


def interactive(project_id: str, run_id: str, task: str, window: Any, max_turns: int = 40) -> dict:
    """Vision loop (ported from the team's loop._interactive): look -> one decision -> act -> wait for the screen to
    settle. Waiting for the user costs no LLM calls (pixel diff only)."""
    import time
    from backend.tools import desktop as dk
    from backend.tools import windows as w
    title = window.get("token") if isinstance(window, dict) else window
    if window:
        w.focus(window)
        time.sleep(0.4)
    rect = dk.content_rect(title)
    history, actions, calls = [], [], 0
    for turn in range(1, max_turns + 1):
        png = dk.ruled_png(dk.grab(rect))
        msg = f"Task: {task}\nWhat happened so far:\n" + ("\n".join(history[-12:]) or "(nothing yet)")
        d = llm.complete([{"role": "system", "content": _VISION_RULES}, {"role": "user", "content": msg}],
                         json_schema=_VISION_SCHEMA, images=[png], project_id=project_id, run_id=run_id, max_tokens=900)
        calls += 1
        act = d.get("action") or ("none" if d.get("done") else "wait_for_change")
        if d.get("done") or act == "none":
            return {"ok": True, "actions": actions, "error": None,
                    "evidence": {"outcome": str(d.get("thought", ""))[:300], "turns": turn, "vision_calls": calls}}
        if _DANGER.search(f"{d.get('text', '')} {d.get('keys', '')}"):
            return {"ok": False, "actions": actions, "evidence": {}, "error": "stopped before a destructive action; needs approval"}
        try:
            if window:
                w.focus(window)
            if act == "click":
                res = dk.click_at(rect, float(d["x"]), float(d["y"]))
                dk.wait_change(rect, timeout=4)
            elif act == "type":
                res = dk.type_text(str(d.get("text", "")))
            elif act == "keys":
                res = dk.keys(str(d.get("keys", "")))
            else:
                res = {"changed": dk.wait_change(rect, timeout=300)}
            ok = True
        except Exception as e:
            res, ok = {"error": f"{type(e).__name__}: {e}"}, False
        label = f"{act} " + (f"({d.get('x')},{d.get('y')})" if act == "click" else str(d.get("text") or d.get("keys") or ""))
        rec = {"action": act, **{k: d.get(k) for k in ("x", "y", "text", "keys")}, "result": "ok" if ok else str(res.get("error"))[:200]}
        actions.append(rec)
        history.append(f"{turn}. {str(d.get('thought', ''))[:200]} -> {label} -> {rec['result']}")
        if run_id:
            db.audit(project_id, run_id, "ui_action", {**rec, "mode": "vision"})
    return {"ok": False, "actions": actions, "evidence": {"vision_calls": calls}, "error": f"stopped after {max_turns} turns"}


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
        key = (run_id or project_id or "", app.strip().lower())
        prev = _opened.get(key)
        if prev is not None and not scratch and desktop.is_open(prev):
            window = prev  # an earlier step of this run already opened it: continue in that window
            desktop.focus(window)
            actions.append({"action": "reuse_window", "app": app, "result": "ok"})
        else:
            try:
                launched = desktop.launch(app, file=str(scratch) if scratch else None)
            except Exception as e:
                return {"ok": False, "actions": actions, "evidence": {}, "error": f"could not open {app}: {e}"}
            window = launched.get("target") or (scratch.stem if scratch else (launched.get("foreground") or {}).get("title"))
            _opened[key] = window
            actions.append({"action": "launch", "app": app, "result": "ok", "window": launched.get("window")})
            if run_id:
                db.audit(project_id, run_id, "ui_action", {"action": "launch", "app": app, "via": launched.get("via"),
                                                           "window": launched.get("window")})
    obs = desktop.observe(window)
    for _ in range(8):  # slow apps: wait until the first screen has something to act on
        if len(obs.get("elements", [])) >= 3 or obs.get("error"):
            break
        import time
        time.sleep(1.5)
        obs = desktop.observe(window)
    # Screen-reactive tasks (games, drawing, waiting for the user's move) or apps whose UI tree is empty (canvas,
    # games): switch to the vision loop. Needs a vision model; screenshots are PII-blurred inside llm.complete.
    if isinstance(desktop, Desktop) and llm.available() and (_NEEDS_SCREEN.search(task) or len(obs.get("elements", [])) < 3):
        try:
            return interactive(project_id, run_id, task, window)
        except Exception as e:
            actions.append({"action": "vision_mode", "result": f"unavailable: {e}"[:200]})
    fails = 0
    streak = 0  # consecutive actions with no visible effect
    for _ in range(budget):
        try:
            d = _decide(project_id, run_id, task, app, tips, obs, actions)
        except Exception as e:
            return {"ok": False, "actions": actions, "evidence": {"ui": _tree_text(obs, 30)}, "error": f"decision failed: {e}"}
        if d.get("done") or d.get("action") == "none":
            effective = [a for a in actions if a.get("result") == "ok" and a["action"] not in ("reuse_window", "research")]
            if not effective:  # the model says "done" but nothing it did had any effect: that is not success
                return {"ok": False, "actions": actions, "evidence": {"ui": _tree_text(obs, 30)},
                        "error": "no action took effect in the app (nothing was clicked or typed successfully)"}
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
            fails = streak = 0
            continue
        fails += 1
        streak += 1
        if streak >= 4:  # really stuck: stop honestly instead of hammering the app
            return {"ok": False, "actions": actions, "evidence": {"ui": _tree_text(obs, 30)},
                    "error": f"stuck: the last {streak} actions had no visible effect in {app or 'the app'}"}
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
            self.launches = getattr(self, "launches", 0) + 1
            return {"foreground": {"title": "Untitled - Notepad"}}

        def is_open(self, window):
            return True

        def focus(self, window):
            pass

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
    assert not r["ok"] and ("budget" in r["error"] or "stuck" in r["error"]), r
    assert any(a["action"] == "research" for a in r["actions"]) and len(searches) >= 2, (r, searches)
    g["_decide"] = lambda *a, **k: {"action": "click", "element_id": 9}
    r2 = explore("p", "", {"args": {"app": "notepad", "task": "x"}}, budget=3, desktop=type("M", (Mock,), {
        "observe": lambda self, w: {"window": "w", "elements": [{"id": 9, "type": "Button", "name": "Don't Save"}]}})(True),
        search=lambda q: [])
    assert not r2["ok"] and r2.get("needs_approval"), r2
    g["_decide"] = orig
    h = _heuristic("type hello world and save", {"elements": [{"id": 3, "type": "Document", "name": ""}]}, [])
    assert h["action"] == "type" and h["text"].startswith("hello world"), h
    # reuse: a second step of the same run must not launch the app again
    m = Mock(True)
    g["_decide"] = lambda *a, **k: {"action": "none", "done": True}
    explore("p_reuse", "", {"args": {"app": "calculator", "task": "a"}}, desktop=m, search=lambda q: [])
    explore("p_reuse", "", {"args": {"app": "calculator", "task": "b"}}, desktop=m, search=lambda q: [])
    assert m.launches == 1, m.launches
    # stuck: actions with no effect stop the loop well before the budget
    g["_decide"] = lambda *a, **k: {"action": "click", "element_id": 2}
    r3 = explore("p", "", {"args": {"app": "calculator", "task": "x"}}, budget=25, desktop=Mock(False), search=lambda q: [])
    assert not r3["ok"] and "stuck" in r3["error"] and len([a for a in r3["actions"] if a["action"] == "click"]) <= 4, r3
    g["_decide"] = orig
    print("agent.explore self-check OK")
