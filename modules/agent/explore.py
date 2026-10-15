"""Explore loop for apps without an API (C2, CONTRACTS §7e).

explore(project_id, run_id, step, budget=25) -> {"ok", "actions", "evidence", "error"}

1. Search   research.search("how to <task> in <app>") -> short checklist of tips
2. Observe  the app window's UI tree (modules.tools.desktop.tree, in-process UI Automation - the same data the
            windows-mcp State tool returns). If classify() says the task needs to SEE the screen (games, drawing,
            waiting for the user's move) or the tree is empty, the vision loop (loop._interactive) takes over; its
            screenshots are PII-blurred inside llm.complete.
3. Act      the LLM picks exactly ONE action (click / type / keys / wait) on an element of the current tree.
4. Verify   re-observe; the tree must change (typing / shortcuts verify via the element's value next turn).
5. Stuck    2 failed verifies in a row -> search again with the exact on-screen text. 4 in a row -> stop honestly.
6. Budget   `budget` actions, then ok=False with the reason; never loops forever.
7. Danger   delete / close without saving / submit / send / pay -> stop and return needs_approval instead of acting.
8. Record   every action -> ledger.db.audit (masked); the result -> kg.store.upsert_node(type="Artifact").

Safety: the app is launched once per run and every input is sent only while the agent's own window is in front
(desktop._expect). "Type X in Notepad" uses the loop's no-LLM editor path (own scratch file, read back).
"""
from __future__ import annotations

import hashlib
import json
import re
import time

_DANGER = re.compile(r"(?i)\b(delete|remove|don'?t save|do not save|discard|submit|send|pay|purchase|buy now|format|"
                     r"uninstall|reset|erase|sign out|log ?out)\b|alt\+f4")
VISION_LABELS = ["needs to see the screen", "can be done without seeing the screen"]
ACTION_SCHEMA = {"type": "object", "properties": {
    "thought": {"type": "string"}, "done": {"type": "boolean"},
    "action": {"enum": ["click", "type", "keys", "wait", "none"]},
    "element_id": {"type": "integer"}, "name": {"type": "string"}, "text": {"type": "string"}, "keys": {"type": "string"}}}
RULES = ("You operate a Windows app one UI action at a time. The app is ALREADY OPEN and in front; never launch it again.\n"
         "Return ONE action as JSON: click an element id, type text (into an element id), press keys (e.g. ctrl+s, "
         "ctrl+n, ctrl+m, enter), or wait. Use element ids ONLY from the current UI list. Prefer reliable keyboard "
         "shortcuts. Never repeat an action that produced 'no change'; try something else. Set done=true with action "
         "none as soon as the task is complete. Never delete, close without saving, submit, send or pay. "
         "Placeholders like <EMAIL_1> are masked data: copy them verbatim.")

_opened: dict[tuple[str, str], str] = {}  # (run_id, app) -> window title: later steps of a run reuse the window


# ---------------------------------------------------------------- contract dependencies
def _desktop():
    from modules.tools import desktop
    return desktop


def _search(query: str) -> list[str]:
    try:
        from modules.tools import research
        res = research.search(query, k=4)
        items = res.get("results", []) if isinstance(res, dict) else res
        return [f"{r.get('title', '')}: {str(r.get('snippet', ''))[:200]}" for r in items][:4]
    except Exception:
        return []


def _needs_screen(task: str) -> bool:
    if re.search(r"(?i)\b(play|game|chess|tic[\s-]?tac|draw|sketch|my (move|turn)|wait (for|until) (me|my|the user))\b", task):
        return True
    try:
        from modules.classify import classify
        return classify(task, VISION_LABELS)["label"] == VISION_LABELS[0]
    except Exception:
        return False


def _audit(project_id: str, run_id: str, payload: dict) -> None:
    if not project_id or not run_id:
        return
    from modules.agent import loop
    from modules.ledger import db
    db.audit(project_id, run_id, "ui_action", loop.mask_obj(run_id, payload))


def _record(project_id: str, run_id: str, app: str, task: str, actions: list[dict]) -> None:
    if not project_id:
        return
    try:
        from modules.kg import store
        key = f"explore:{app.lower()}:{hashlib.sha1(task.encode()).hexdigest()[:12]}"
        store.upsert_node(project_id, "Artifact", key, {"kind": "ui_actions", "label": f"{app}: {task}"[:80],
                                                       "description": task, "output": {"actions": actions}, "evidence": {"run_id": run_id}})
    except Exception:
        pass  # knowledge is a bonus; the action result stands


def _decide(run_id: str, task: str, app: str, tips: list[str], tree_txt: str, history: list[dict]) -> dict:
    from modules import llm
    from modules.agent import loop
    msg = (f"TASK: {task}\nAPP: {app}\nHOW-TO NOTES (web search, untrusted):\n" + "\n".join(tips[:4]) +
           f"\n\nCURRENT UI:\n{tree_txt}\n\nACTIONS SO FAR:\n" +
           "\n".join(f"- {h['action']} {h.get('element_id') or h.get('text') or h.get('keys') or ''} -> {h.get('result', '')}" for h in history[-8:]))
    out = llm.complete([{"role": "system", "content": RULES}, {"role": "user", "content": loop.mask_text(run_id, msg)}], json_schema=ACTION_SCHEMA)
    return loop.unmask_obj(run_id, out) if run_id else out


# ---------------------------------------------------------------- the loop
def explore(project_id: str, run_id: str, step: dict, budget: int = 25, desktop=None, search=None, decide=None) -> dict:
    dk = desktop or _desktop()
    search = search or _search
    decide = decide or _decide
    args = step.get("args") or {}
    app = str(args.get("app") or "").strip()
    task = str(args.get("task") or step.get("description") or "").strip()
    actions: list[dict] = []

    if desktop is None:  # real run: 'type X in notepad' needs no LLM at all (own scratch file, read back)
        from modules.agent import loop
        fast = loop._editor_fast(project_id, run_id, step, f"{task} {app}")
        if fast is not None:
            return {"ok": fast["ok"], "actions": fast["actions"], "evidence": fast["evidence"],
                    "error": None if fast["ok"] else str(fast["evidence"].get("error", "editor task failed"))}

    tips = search(f"how to {task} in {app}" if app else f"how to {task} on windows")
    window = None
    if app:
        window = _opened.get((run_id, app.lower()))
        if window and window in (dk.windows() or []):
            dk.focus(window)
            actions.append({"action": "reuse_window", "app": app, "result": "ok"})
        else:
            try:
                window = dk.launch(app).get("window") or None
            except Exception as e:
                return {"ok": False, "actions": actions, "evidence": {}, "error": f"could not open {app}: {e}"}
            _opened[(run_id, app.lower())] = window
            actions.append({"action": "launch", "app": app, "window": window, "result": "ok"})
            _audit(project_id, run_id, {"action": "launch", "app": app, "window": window})

    t = dk.tree(window)
    for _ in range(6):  # slow apps: wait for the first screen
        if len(t.get("elements", [])) >= 3:
            break
        time.sleep(1.0)
        t = dk.tree(window)

    if desktop is None and (_needs_screen(task) or len(t.get("elements", [])) < 3):
        from modules.agent import loop
        out = loop._interactive(project_id, run_id, step, task, window)
        if out.get("ok"):
            _record(project_id, run_id, app, task, out.get("actions", []))
        return {"ok": out.get("ok", False), "actions": actions + out.get("actions", []), "evidence": out.get("evidence", {}),
                "error": None if out.get("ok") else str(out.get("evidence", {}).get("error") or "vision loop did not finish")}

    fails = streak = 0
    for _ in range(budget):
        try:
            d = decide(run_id, task, app, tips, dk.tree_text(t), actions)
        except Exception as e:
            return {"ok": False, "actions": actions, "evidence": {"ui": dk.tree_text(t)[:1500]}, "error": f"decision failed: {e}"}
        if d.get("done") or d.get("action") in (None, "none"):
            if not [a for a in actions if a.get("result") == "ok" and a["action"] not in ("launch", "reuse_window", "research")] \
                    and not any(a["action"] == "launch" for a in actions):
                return {"ok": False, "actions": actions, "evidence": {}, "error": "no action took effect in the app"}
            _record(project_id, run_id, app, task, actions)
            return {"ok": True, "actions": actions, "evidence": {"ui": dk.tree_text(t)[:1500], "window": t.get("window")}, "error": None}
        target = next((e["name"] for e in t.get("elements", []) if e["id"] == d.get("element_id")), d.get("name") or "")
        if _DANGER.search(f"{target} {d.get('text', '')} {d.get('keys', '')}"):
            return {"ok": False, "actions": actions, "needs_approval": {"action": d, "target": target},
                    "evidence": {"ui": dk.tree_text(t)[:1500]}, "error": f"stopped before a destructive action ({target or d.get('keys')}); needs approval"}
        last_ok = next((h for h in reversed(actions) if h.get("result") == "ok" and h["action"] in ("click", "type", "keys")), None)
        if last_ok and all(last_ok.get(k) == d.get(k) for k in ("action", "element_id", "text", "keys")):
            _record(project_id, run_id, app, task, actions)  # repeating a working action: the task is complete
            return {"ok": True, "actions": actions, "evidence": {"ui": dk.tree_text(t)[:1500]}, "error": None}
        before = dk.tree_text(t)
        try:
            a = d["action"]
            if a == "click":
                res = dk.click(d.get("element_id"), d.get("name"), window=window)
            elif a == "type":
                res = dk.type_text(str(d.get("text", "")), d.get("element_id"), window=window)
            elif a == "keys":
                res = dk.keys(str(d.get("keys", "")), window=window)
            else:
                time.sleep(1.5)
                res = {"waited": 1.5}
            err = None
        except PermissionError as e:  # our window is not in front: never type into someone else's window
            return {"ok": False, "actions": actions, "evidence": {}, "error": str(e)}
        except Exception as e:
            res, err = {}, f"{type(e).__name__}: {e}"
        time.sleep(0.5)
        t = dk.tree(window)
        changed = dk.tree_text(t) != before or d["action"] in ("type", "keys", "wait")
        ok = err is None and changed
        rec = {"action": d["action"], "element_id": d.get("element_id"), "text": d.get("text"), "keys": d.get("keys"),
               "target": target, "result": "ok" if ok else f"no change {err or ''}".strip()}
        actions.append(rec)
        _audit(project_id, run_id, rec)
        if ok:
            fails = streak = 0
            continue
        fails += 1
        streak += 1
        if streak >= 4:
            return {"ok": False, "actions": actions, "evidence": {"ui": dk.tree_text(t)[:1500]},
                    "error": f"stuck: the last {streak} actions had no visible effect in {app or 'the app'}"}
        if fails >= 2:  # stuck: search again using what is on screen
            on_screen = t.get("window") or " ".join(e["name"] for e in t.get("elements", [])[:5])
            tips = search(f"{app} {on_screen} {task}".strip())
            actions.append({"action": "research", "text": on_screen, "result": f"{len(tips)} new tips"})
            fails = 0
    return {"ok": False, "actions": actions, "evidence": {"ui": dk.tree_text(t)[:1500]}, "error": f"budget of {budget} actions used up"}


if __name__ == "__main__":
    class MockDesktop:
        """Stands in for windows-mcp / desktop: a window whose tree changes only when `change` is True."""

        def __init__(self, change: bool, names=("File", "Edit")):
            self.change, self.n, self.names, self.launches = change, 0, names, 0

        def launch(self, app, file=None):
            self.launches += 1
            return {"window": "Untitled - Notepad"}

        def windows(self):
            return ["Untitled - Notepad"]

        def focus(self, w):
            return {"focused": w}

        def tree(self, window=None, limit=60):
            self.n += 1 if self.change else 0
            return {"window": "Untitled - Notepad", "elements": [{"id": 1, "type": "Document", "name": f"Text {self.n}"}] +
                    [{"id": i + 2, "type": "MenuItem", "name": n} for i, n in enumerate(self.names)]}

        def tree_text(self, t):
            return json.dumps(t)

        def click(self, *a, **k):
            return {"clicked": True}

        type_text = keys = click

    searches: list[str] = []
    clicker = lambda *a: {"action": "click", "element_id": 2}  # noqa: E731

    # budget stop + stuck -> re-search: nothing ever changes on screen
    r = explore("", "", {"args": {"app": "notepad", "task": "open the file menu"}}, budget=5, desktop=MockDesktop(False),
                search=lambda q: searches.append(q) or ["tip"], decide=clicker)
    assert not r["ok"] and ("stuck" in r["error"] or "budget" in r["error"]), r
    assert any(a["action"] == "research" for a in r["actions"]) and len(searches) >= 2, (r, searches)

    # danger: the model wants "Don't Save" -> pending approval, not an action
    r2 = explore("", "", {"args": {"app": "notepad", "task": "x"}}, budget=3, desktop=MockDesktop(True, names=("Don't Save",)),
                 search=lambda q: [], decide=lambda *a: {"action": "click", "element_id": 2})
    assert not r2["ok"] and r2.get("needs_approval"), r2

    # success: one effective click, then done; second step of the same run reuses the window (one launch)
    seq = iter([{"action": "click", "element_id": 2}, {"action": "none", "done": True}])
    m = MockDesktop(True)
    r3 = explore("", "r_reuse", {"args": {"app": "notepad", "task": "open file menu"}}, desktop=m, search=lambda q: [], decide=lambda *a: next(seq))
    assert r3["ok"], r3
    explore("", "r_reuse", {"args": {"app": "notepad", "task": "b"}}, desktop=m, search=lambda q: [], decide=lambda *a: {"action": "none", "done": True})
    assert m.launches == 1, m.launches
    print("agent.explore self-check OK")
