"""Windows desktop control through UI Automation (uiautomation). Used by agent/explore.py for apps without an API.

All functions are safe to call from worker threads. Element ids come from the latest ui_tree() call.
"""
from __future__ import annotations

import io
import json
import re
import os
import subprocess
import threading
import time

_elements: dict[int, object] = {}
_lock = threading.RLock()

APP_ALIASES = {
    "notepad": "notepad.exe", "paint": "mspaint.exe", "mspaint": "mspaint.exe", "calculator": "calc.exe",
    "calc": "calc.exe", "explorer": "explorer.exe", "file explorer": "explorer.exe", "settings": "ms-settings:",
    "wordpad": "write.exe", "cmd": "cmd.exe", "powershell": "powershell.exe", "task manager": "taskmgr.exe",
    "packet tracer": r"C:\Program Files\Cisco Packet Tracer 8.2.2\bin\PacketTracer.exe",
}

_INTERACTIVE = {"ButtonControl", "EditControl", "MenuItemControl", "ListItemControl", "TabItemControl", "CheckBoxControl",
                "RadioButtonControl", "ComboBoxControl", "HyperlinkControl", "TreeItemControl", "DocumentControl",
                "SplitButtonControl", "MenuBarControl", "SliderControl", "TextControl", "DataItemControl"}


def _auto():
    import uiautomation as auto  # type: ignore
    return auto


class _InThread:
    def __enter__(self):
        self.ctx = _auto().UIAutomationInitializerInThread(debug=False)
        self.ctx.__enter__()

    def __exit__(self, *a):
        self.ctx.__exit__(*a)
        return False


def available() -> bool:
    try:
        _auto()
        return os.name == "nt"
    except Exception:
        return False


_start_apps_cache: list[dict] | None = None


def start_apps() -> list[dict]:
    """Every app in the Start menu (Win32 + Store apps): [{"name", "appid"}] via Get-StartApps (cached)."""
    global _start_apps_cache
    if _start_apps_cache is None:
        try:
            p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                                "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
                               capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
            data = json.loads(p.stdout or "[]")
            data = data if isinstance(data, list) else [data]
            _start_apps_cache = [{"name": d.get("Name", ""), "appid": d.get("AppID", "")} for d in data if d.get("AppID")]
        except Exception:
            _start_apps_cache = []
    return _start_apps_cache


def _app_paths(exe: str) -> str | None:
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                v = winreg.QueryValue(k, None)
                if v and os.path.exists(v.strip('"')):
                    return v.strip('"')
        except OSError:
            continue
    return None


def resolve_app(name: str) -> dict:
    """Any app name -> how to start it: {"kind": "path|appid|uri|shell", "target", "display"}.
    Order: known aliases / paths / URIs -> Start menu (fuzzy) -> registered App Paths -> PATH -> shell."""
    import difflib
    import shutil
    raw = name.strip().strip('"')
    key = raw.lower()
    alias = APP_ALIASES.get(key)
    if alias:
        kind = "uri" if alias.endswith(":") else "path"
        return {"kind": kind, "target": alias, "display": raw}
    if raw.endswith(":") or re.match(r"^[a-z][a-z0-9+.-]*:(?!\\)", raw, re.I):
        return {"kind": "uri", "target": raw, "display": raw}
    if os.path.exists(raw):
        return {"kind": "path", "target": raw, "display": os.path.splitext(os.path.basename(raw))[0]}
    apps = start_apps()
    want = re.sub(r"\.exe$", "", key)
    filler = {"microsoft", "the", "app", "application", "windows", "program", "desktop", "ms"}
    w_tok = [t for t in re.findall(r"[a-z0-9]+", want) if t not in filler] or re.findall(r"[a-z0-9]+", want)

    def covers(tok: str, n_tok: list[str]) -> bool:
        if tok in n_tok or any(n.startswith(tok) and len(tok) >= 4 for n in n_tok):
            return True
        initials = "".join(n[0] for n in n_tok)
        return len(tok) >= 2 and tok in initials  # "vs" -> Visual Studio

    best, score = None, 0.0
    for a in apps:
        n = a["name"].lower()
        n_tok = [t for t in re.findall(r"[a-z0-9]+", n) if t not in filler] or re.findall(r"[a-z0-9]+", n)
        if " ".join(n_tok) == " ".join(w_tok):
            s = 1.0
        elif all(covers(t, n_tok) for t in w_tok):
            s = 0.95 - 0.02 * max(0, len(n_tok) - len(w_tok))  # prefer the shortest name that contains every word
        elif all(t in w_tok for t in n_tok):
            s = 0.9
        else:
            s = difflib.SequenceMatcher(None, " ".join(w_tok), " ".join(n_tok)).ratio()
        if re.search(r"uninstall|help|readme|documentation|release notes", n):
            s -= 0.3
        if s > score:
            best, score = a, s
    if best and score >= 0.72:
        return {"kind": "appid", "target": best["appid"], "display": best["name"]}
    exe = want if want.endswith(".exe") else want + ".exe"
    p = _app_paths(exe) or shutil.which(exe)
    if p:
        return {"kind": "path", "target": p, "display": raw}
    return {"kind": "shell", "target": raw, "display": raw}


class _Win:
    """A visible top-level window (plain Win32: robust while apps open/close windows, unlike cached UIA objects)."""

    def __init__(self, hwnd: int, title: str):
        self.NativeWindowHandle, self.Name = hwnd, title


def _top_windows() -> list[_Win]:
    import win32gui
    out: list[_Win] = []

    def cb(h, _):
        try:
            if win32gui.IsWindowVisible(h) and not win32gui.IsIconic(h):
                t = win32gui.GetWindowText(h)
                if t:
                    out.append(_Win(h, t))
        except Exception:
            pass
        return True
    win32gui.EnumWindows(cb, None)
    return out


def _fg_hwnd() -> int:
    import win32gui
    return win32gui.GetForegroundWindow()


def _bring_front(hwnd: int) -> None:
    """SetForegroundWindow only works for the foreground process; an Alt tap lifts that restriction."""
    import win32con
    import win32gui
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        _auto().SendKeys("{Alt}", waitTime=0.05)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        try:
            _auto().ControlFromHandle(hwnd).SetActive()
        except Exception:
            pass


def _tokens(display: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9]+", display.lower()) if w not in ("microsoft", "the", "app", "windows") and len(w) > 2]
    return words[:2] or [display.lower()]


def launch_app(name: str, wait: float = 30.0, file: str | None = None) -> dict:
    """Start any installed app ONCE and wait until its window really exists (slow apps show a splash first).
    Returns {"launched", "window", "target": {"handle", "token"}} where target identifies the window from now on."""
    spec = resolve_app(name)
    with _lock, _InThread():
        before = {w.NativeWindowHandle for w in _top_windows()}
    if spec["kind"] == "appid":
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{spec['target']}"])
    elif spec["kind"] == "path" and spec["target"].lower().endswith(".exe"):
        subprocess.Popen([spec["target"]] + ([file] if file else []))
    else:
        os.startfile(spec["target"])
    token = _tokens(spec["display"])[0]
    deadline, found = time.time() + wait, None
    while time.time() < deadline and found is None:
        time.sleep(0.6)
        with _lock, _InThread():
            wins = _top_windows()
            new = [w for w in wins if w.NativeWindowHandle not in before]
            match = [w for w in new if token in w.Name.lower()] or new or [w for w in wins if token in w.Name.lower()]
            if match:
                found = {"handle": match[0].NativeWindowHandle, "token": token, "title": match[0].Name}
    if found is None:
        raise TimeoutError(f"{spec['display']} did not open a window within {int(wait)}s (tried {spec['kind']}: {spec['target']})")
    time.sleep(1.5)  # let the first screen render before reading it
    focus(found)
    return {"launched": spec["display"], "via": spec["kind"], "window": found["title"], "target": found}


def locate(target: dict | str | None):
    """The live window for a target {"handle","token"} (or a title fragment). Follows the app when its window changes
    (e.g. PowerPoint's start screen is replaced by the presentation window): prefer the remembered handle while it is
    still visible, else the foreground window if it belongs to the app, else the newest matching window."""
    if not target:
        h = _fg_hwnd()
        return _auto().ControlFromHandle(h) if h else None
    if isinstance(target, str):
        target = {"handle": None, "token": target.lower()}
    wins = _top_windows()
    tok = (target.get("token") or "").lower()
    hit = next((w for w in wins if w.NativeWindowHandle == target.get("handle")), None)
    if hit is None or (tok and tok not in hit.Name.lower()):
        fg = _fg_hwnd()
        cands = [w for w in wins if tok and tok in w.Name.lower()]
        hit = next((w for w in cands if w.NativeWindowHandle == fg), None) or (cands[0] if cands else hit)
    if hit is None:
        return None
    target["handle"] = hit.NativeWindowHandle
    return _auto().ControlFromHandle(hit.NativeWindowHandle)


def focus(target) -> bool:
    with _lock, _InThread():
        w = locate(target)
        if not w:
            return False
        _bring_front(w.NativeWindowHandle)
        return True


def window_exists(target) -> bool:
    with _lock, _InThread():
        return locate(target) is not None


def foreground_window() -> dict:
    with _lock, _InThread():
        c = _auto().GetForegroundControl()
        top = c.GetTopLevelControl() if c else None
        return {"title": getattr(top, "Name", ""), "class": getattr(top, "ClassName", "")} if top else {}


def list_windows() -> dict:
    with _lock, _InThread():
        root = _auto().GetRootControl()
        wins = [{"title": w.Name, "class": w.ClassName} for w in root.GetChildren() if w.Name]
    return {"windows": wins[:60]}


def focus_window(title_contains: str) -> dict:
    with _lock, _InThread():
        for w in _auto().GetRootControl().GetChildren():
            if title_contains.lower() in (w.Name or "").lower():
                w.SetActive()
                w.SetFocus()
                return {"focused": w.Name}
    raise LookupError(f"no window title contains {title_contains!r}")


def ui_tree(window_title=None, max_nodes: int = 160) -> dict:
    """Interactive elements of the agent's window (a target dict, a title fragment, or the foreground window if None).
    If a specific window is requested but gone, this raises instead of silently reading some other window."""
    with _lock, _InThread():
        auto = _auto()
        win = locate(window_title)
        if win is None:
            raise LookupError(f"the app window ({window_title}) is not open")
        _elements.clear()
        out = []
        for ctrl, depth in auto.WalkControl(win, includeTop=False, maxDepth=12):
            if len(out) >= max_nodes:
                break
            if ctrl.ControlTypeName not in _INTERACTIVE:
                continue
            try:
                r = ctrl.BoundingRectangle
                if r.width() <= 0 or r.height() <= 0:
                    continue
                eid = len(_elements) + 1
                _elements[eid] = ctrl
                el = {"id": eid, "name": (ctrl.Name or "")[:80], "type": ctrl.ControlTypeName.replace("Control", ""),
                      "rect": [r.left, r.top, r.width(), r.height()]}
                if el["type"] in ("Edit", "Document"):
                    el["value"] = _value_of(ctrl)[:300]
                out.append(el)
            except Exception:
                continue
        return {"window": win.Name, "elements": out}


def _value_of(ctrl) -> str:
    try:
        return ctrl.GetValuePattern().Value or ""
    except Exception:
        pass
    try:
        return ctrl.GetTextPattern().DocumentRange.GetText(2000) or ""
    except Exception:
        return ""


def _require_foreground(expect_window) -> None:
    """Refuse to send input unless the agent's own window is in front (never type into the user's other windows).
    If it is merely behind another window, bring it forward once and check again."""
    if not expect_window:
        return
    title = ""
    for attempt in range(2):
        win = locate(expect_window)
        if win is None:
            raise PermissionError(f"the agent's window ({expect_window}) is gone; input not sent")
        fg = _fg_hwnd()
        if fg == win.NativeWindowHandle:
            return
        try:
            import win32gui
            title = win32gui.GetWindowText(fg)
        except Exception:
            pass
        if attempt == 0:
            _bring_front(win.NativeWindowHandle)
            time.sleep(0.4)
    raise PermissionError(f"foreground window is {title!r}, not the agent's window; input not sent")


def click(element_id: int | None = None, name: str | None = None, expect_window: str | None = None) -> dict:
    with _lock, _InThread():
        _require_foreground(expect_window)
        ctrl = _elements.get(int(element_id)) if element_id is not None else None
        if ctrl is None and name:
            fg = locate(expect_window)
            ctrl = fg.Control(searchDepth=12, Name=name) if fg else None
            if ctrl is not None and not ctrl.Exists(1):
                ctrl = None
        if ctrl is None:
            raise LookupError(f"element {element_id or name!r} not found; call ui_tree first")
        try:
            ctrl.GetInvokePattern().Invoke()
        except Exception:
            ctrl.Click(simulateMove=False)
        return {"clicked": getattr(ctrl, "Name", "")}


def type_text(text: str, element_id: int | None = None, expect_window: str | None = None) -> dict:
    with _lock, _InThread():
        auto = _auto()
        _require_foreground(expect_window)
        if element_id is not None and int(element_id) in _elements:
            ctrl = _elements[int(element_id)]
            try:
                ctrl.GetValuePattern().SetValue(text)
                return {"typed": len(text), "via": "value"}
            except Exception:
                ctrl.Click(simulateMove=False)
        auto.SendKeys(text.replace("{", "{{").replace("}", "}}"), interval=0.01, waitTime=0.1)
        return {"typed": len(text), "via": "keys"}


def shortcut(keys: str, expect_window: str | None = None) -> dict:
    """keys like 'ctrl+s', 'alt+f4', 'enter'."""
    m = {"ctrl": "{Ctrl}", "alt": "{Alt}", "shift": "{Shift}", "win": "{Win}", "enter": "{Enter}", "tab": "{Tab}",
         "esc": "{Esc}", "escape": "{Esc}", "f4": "{F4}", "delete": "{Delete}", "backspace": "{Back}"}
    seq = "".join(m.get(k.strip().lower(), k.strip()) for k in keys.split("+"))
    with _lock, _InThread():
        _require_foreground(expect_window)
        _auto().SendKeys(seq, waitTime=0.2)
    return {"sent": keys}


def screenshot() -> bytes:
    from PIL import ImageGrab
    img = ImageGrab.grab()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


if __name__ == "__main__":
    assert available(), "uiautomation not importable"
    wins = list_windows()["windows"]
    assert isinstance(wins, list)
    assert shortcut.__doc__
    print(f"tools.windows self-check OK ({len(wins)} top-level windows visible)")
