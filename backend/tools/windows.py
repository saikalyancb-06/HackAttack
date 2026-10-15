"""Windows desktop control through UI Automation (uiautomation). Used by agent/explore.py for apps without an API.

All functions are safe to call from worker threads. Element ids come from the latest ui_tree() call.
"""
from __future__ import annotations

import io
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


def launch_app(name: str, wait: float = 2.5, file: str | None = None) -> dict:
    """Start an app. `file` opens that document (editors work on a scratch file, never on the user's open tabs)."""
    target = APP_ALIASES.get(name.strip().lower(), name)
    if target.endswith(":"):
        os.startfile(target)  # URI handlers such as ms-settings:
    elif os.path.exists(target) or target.lower().endswith(".exe"):
        subprocess.Popen([target] + ([file] if file else []))
    else:
        os.startfile(target)
    time.sleep(wait)
    return {"launched": target, "foreground": foreground_window()}


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


def ui_tree(window_title: str | None = None, max_nodes: int = 120) -> dict:
    """Interactive elements of the given (or foreground) window: [{id,name,type,rect}]."""
    with _lock, _InThread():
        auto = _auto()
        win = None
        if window_title:
            for w in auto.GetRootControl().GetChildren():
                if window_title.lower() in (w.Name or "").lower():
                    win = w
                    break
        if win is None:
            fg = auto.GetForegroundControl()
            win = fg.GetTopLevelControl() if fg else None
        if win is None:
            raise LookupError("no window to inspect")
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


def _require_foreground(expect_window: str | None) -> None:
    """Refuse to send input unless the agent's own window is in front (never type into the user's other windows)."""
    if not expect_window:
        return
    fg = _auto().GetForegroundControl()
    top = fg.GetTopLevelControl() if fg else None
    title = (getattr(top, "Name", "") or "").lower()
    if expect_window.lower() not in title:
        raise PermissionError(f"foreground window is {title!r}, not the agent's window {expect_window!r}; input not sent")


def click(element_id: int | None = None, name: str | None = None, expect_window: str | None = None) -> dict:
    with _lock, _InThread():
        _require_foreground(expect_window)
        ctrl = _elements.get(int(element_id)) if element_id is not None else None
        if ctrl is None and name:
            fg = _auto().GetForegroundControl().GetTopLevelControl()
            ctrl = fg.Control(searchDepth=12, Name=name)
            if not ctrl.Exists(1):
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
