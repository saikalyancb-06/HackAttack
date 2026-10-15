"""Fast desktop control (C1): Windows UI Automation in-process + PowerShell. Used by loop._explore.

No screenshots, no per-action LLM: callers read a compact element list of ONE window and act by element id.
"""
import glob
import os
import re
import subprocess
import threading
import time

try:  # physical pixels everywhere, so UIA rects, screenshots and clicks agree
    import ctypes

    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

APPS = {"notepad": "notepad.exe", "excel": "excel.exe", "word": "winword.exe", "paint": "mspaint.exe", "calculator": "calc.exe",
        "calc": "calc.exe", "explorer": "explorer.exe", "cmd": "cmd.exe", "powershell": "powershell.exe", "settings": "ms-settings:"}
INTERACTIVE = {"Button", "Edit", "Document", "MenuItem", "ListItem", "TabItem", "CheckBox", "RadioButton", "ComboBox",
               "Hyperlink", "TreeItem", "SplitButton", "DataItem", "Text"}
_lock = threading.RLock()
_els: dict[int, object] = {}


def _ua():
    import uiautomation as auto

    return auto


class _UI:
    """uiautomation needs COM initialised in every thread that uses it."""

    def __enter__(self):
        _lock.acquire()
        self.ctx = _ua().UIAutomationInitializerInThread(debug=False)
        self.ctx.__enter__()
        return _ua()

    def __exit__(self, *a):
        self.ctx.__exit__(*a)
        _lock.release()


def launch(app: str, file: str | None = None, wait_title: str | None = None, timeout: float = 10) -> dict:
    target = APPS.get(app.strip().lower(), app)
    if target.endswith(":"):
        os.startfile(target)
    else:
        subprocess.Popen([target] + ([file] if file else []))
    want = wait_title or (os.path.splitext(os.path.basename(file))[0] if file else None)
    end = time.time() + timeout
    while want and time.time() < end:  # wait until our window exists, then bring it to the front
        try:
            return {"launched": target, "window": focus(want)["focused"]}
        except LookupError:
            time.sleep(0.3)
    time.sleep(1.5 if not want else 0)
    return {"launched": target, "window": foreground()}


def foreground() -> str:
    with _UI() as auto:
        c = auto.GetForegroundControl()
        return (c.GetTopLevelControl().Name if c else "") or ""


def windows() -> list[str]:
    with _UI() as auto:
        return [w.Name for w in auto.GetRootControl().GetChildren() if w.Name][:40]


def focus(title_contains: str) -> dict:
    with _UI() as auto:
        for w in auto.GetRootControl().GetChildren():
            if title_contains.lower() in (w.Name or "").lower():
                w.SetActive()
                w.SetFocus()
                return {"focused": w.Name}
    raise LookupError(f"no window title contains {title_contains!r}")


def _window(auto, title: str | None):
    if title:
        for w in auto.GetRootControl().GetChildren():
            if title.lower() in (w.Name or "").lower():
                return w
    c = auto.GetForegroundControl()
    return c.GetTopLevelControl() if c else None


def _value(ctrl) -> str:
    for get in (lambda: ctrl.GetValuePattern().Value, lambda: ctrl.GetTextPattern().DocumentRange.GetText(4000)):
        try:
            v = get()
            if v:
                return v
        except Exception:
            pass
    return ""


def tree(window: str | None = None, limit: int = 60) -> dict:
    """Interactive elements of one window: {"window", "elements": [{id, type, name, value?}]}. Ids are valid until the next tree()."""
    with _UI() as auto:
        win = _window(auto, window)
        if win is None:
            return {"window": "", "elements": []}
        _els.clear()
        out = []
        for ctrl, _ in auto.WalkControl(win, includeTop=False, maxDepth=10):
            kind = ctrl.ControlTypeName.replace("Control", "")
            if kind not in INTERACTIVE or len(out) >= limit:
                continue
            try:
                r = ctrl.BoundingRectangle
                if r.width() <= 0 or r.height() <= 0:
                    continue
            except Exception:
                continue
            eid = len(_els) + 1
            _els[eid] = ctrl
            el = {"id": eid, "type": kind, "name": (ctrl.Name or "")[:60]}
            if kind in ("Edit", "Document"):
                el["value"] = _value(ctrl)[:200]
            out.append(el)
        return {"window": win.Name, "elements": out}


def tree_text(t: dict) -> str:
    return f"window: {t['window']}\n" + "\n".join(f"[{e['id']}] {e['type']} '{e['name']}'" + (f" value={e['value']!r}" if e.get("value") else "")
                                                for e in t["elements"])


def _expect(window: str | None, auto) -> None:
    """Never send input to a window that is not the agent's."""
    if window:
        top = (_window(auto, None).Name or "") if _window(auto, None) else ""
        if window.lower() not in top.lower():
            raise PermissionError(f"foreground is {top!r}, not {window!r}; input not sent")


def click(element_id: int | None = None, name: str | None = None, window: str | None = None) -> dict:
    with _UI() as auto:
        _expect(window, auto)
        ctrl = _els.get(int(element_id)) if element_id is not None else None
        if ctrl is None and name:
            ctrl = _window(auto, window).Control(searchDepth=10, Name=name)
            ctrl = ctrl if ctrl.Exists(1) else None
        if ctrl is None:
            raise LookupError(f"element {element_id or name!r} not found")
        try:
            ctrl.GetInvokePattern().Invoke()
        except Exception:
            ctrl.Click(simulateMove=False)
        return {"clicked": ctrl.Name}


def type_text(text: str, element_id: int | None = None, window: str | None = None) -> dict:
    with _UI() as auto:
        _expect(window, auto)
        ctrl = _els.get(int(element_id)) if element_id is not None else None
        if ctrl is not None:
            ctrl.SetFocus()
        auto.SetClipboardText(text)  # paste: instant and safe for any characters
        auto.SendKeys("{Ctrl}v", waitTime=0.2)
        return {"typed": len(text)}


KEYS = {"ctrl": "{Ctrl}", "alt": "{Alt}", "shift": "{Shift}", "win": "{Win}", "enter": "{Enter}", "tab": "{Tab}", "esc": "{Esc}"}


def keys(combo: str, window: str | None = None) -> dict:
    """'ctrl+s', 'enter', 'ctrl+shift+n'."""
    with _UI() as auto:
        _expect(window, auto)
        auto.SendKeys("".join(KEYS.get(k.strip().lower(), k.strip()) for k in combo.split("+")), waitTime=0.3)
        return {"sent": combo}


_WRAP = re.compile(r"""(?is)^\s*(?:powershell|pwsh)(?:\.exe)?\s+(?:-\w+\s+(?:bypass\s+)?)*-command\s+(?P<q>["'])(.*)(?P=q)\s*$""")


_COM = re.compile(r"(?i)(\$\w+)\s*=\s*\(?\s*New-Object\s+-ComObject\s+(?:Excel|Word)\.Application\s*\)?")


def powershell(cmd: str, timeout: int = 60) -> dict:
    """Run a PowerShell script. Sent base64-encoded, so quotes and $ never need escaping."""
    import base64

    m = _WRAP.match(cmd)
    if m:  # LLMs like to wrap the script in `powershell -Command "..."`
        cmd = m.group(2)
    # Office COM: a hidden "replace file?" dialog would block the script forever, so alerts are always off
    cmd = _COM.sub(lambda m: f"{m.group(0)}; {m.group(1)}.DisplayAlerts = $false", cmd)
    enc = base64.b64encode(cmd.encode("utf-16-le")).decode()
    p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
                       capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    return {"exit_code": p.returncode, "stdout": p.stdout[-4000:], "stderr": p.stderr[-2000:]}


def find_files(text: str, extra_dirs: list[str] = ()) -> dict:
    """Filenames mentioned in text -> absolute path (existing) or None."""
    home = os.path.expanduser("~")
    dirs = [*extra_dirs, os.getcwd(), os.path.join(home, "Desktop"), os.path.join(home, "Documents"), os.path.join(home, "Downloads"),
            *glob.glob(os.path.join(home, "OneDrive*", "Desktop")), *glob.glob(os.path.join(home, "OneDrive*", "Documents"))]
    out = {}
    for name in set(re.findall(r"[\w\-.]*[\w\-]\.(?:xlsx|xls|csv|docx|txt|pptx|pdf|md|json)\b", text, re.I)):
        name = name.strip()
        out[name] = next((os.path.join(d, name) for d in dirs if os.path.isfile(os.path.join(d, name))), None)
    return out



# ---------- screen: for tasks that must watch and react (games, waiting for the user) ----------
def content_rect(title: str | None) -> tuple[int, int, int, int]:
    """Screen rect (physical px) of the window's page/document area, else the whole window."""
    with _UI() as auto:
        win = _window(auto, title)
        if win is None:
            raise LookupError(f"no window {title!r}")
        doc = win.DocumentControl(searchDepth=12)
        r = doc.BoundingRectangle if doc.Exists(0.5) and doc.BoundingRectangle.width() > 200 else win.BoundingRectangle
        return r.left, r.top, r.right, r.bottom


def grab(rect):
    from PIL import ImageGrab

    return ImageGrab.grab(bbox=rect, all_screens=True).convert("RGB")


def ruled_png(img, width: int = 1200) -> bytes:
    """Downscale and draw a 0-1000 coordinate ruler so a vision model can answer click points as [x, y] in 0..1000."""
    import io

    from PIL import ImageDraw

    im = img.resize((width, max(1, int(img.height * width / img.width))))
    d = ImageDraw.Draw(im)
    for k in range(100, 1000, 100):
        x, y = im.width * k // 1000, im.height * k // 1000
        d.line([(x, 0), (x, im.height)], fill=(255, 0, 255), width=1)
        d.line([(0, y), (im.width, y)], fill=(255, 0, 255), width=1)
        d.text((x + 2, 2), str(k), fill=(255, 0, 255))
        d.text((2, y + 2), str(k), fill=(255, 0, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def click_at(rect, x1000: float, y1000: float) -> dict:
    x = int(rect[0] + (rect[2] - rect[0]) * x1000 / 1000)
    y = int(rect[1] + (rect[3] - rect[1]) * y1000 / 1000)
    with _UI() as auto:
        auto.Click(x, y, waitTime=0.2)
    return {"clicked_at": [x, y]}


def _sig(img):
    return list(img.convert("L").resize((96, 64)).getdata())


def wait_change(rect, timeout: float = 300, settle: float = 0.8, threshold: float = 1.5) -> bool:
    """Block (no LLM) until the area changes and then stays still for `settle` s. False on timeout."""
    base, end = _sig(grab(rect)), time.time() + timeout
    while time.time() < end:
        time.sleep(0.3)
        cur = _sig(grab(rect))
        if sum(abs(a - b) for a, b in zip(base, cur)) / len(cur) > threshold:
            while time.time() < end:  # wait for animations to finish
                time.sleep(settle)
                nxt = _sig(grab(rect))
                if sum(abs(a - b) for a, b in zip(cur, nxt)) / len(nxt) < 0.3:
                    return True
                cur = nxt
            return True
    return False


if __name__ == "__main__":
    assert find_files("open NoSuchBook9.xlsx") == {"NoSuchBook9.xlsx": None}
    t = tree()
    assert "elements" in t and isinstance(tree_text(t), str)
    assert powershell("Write-Output ok")["stdout"].strip() == "ok"
    assert powershell("""powershell -NoProfile -Command "$a = 'x'; Write-Output $a\"""")["stdout"].strip() == "x"
    assert _COM.sub(lambda m: f"{m.group(0)}; {m.group(1)}.DisplayAlerts = $false", "$xl = New-Object -ComObject Excel.Application; $xl.Visible=1") \
        == "$xl = New-Object -ComObject Excel.Application; $xl.DisplayAlerts = $false; $xl.Visible=1"
    assert windows()
    r = content_rect(None)
    assert len(ruled_png(grab(r))) > 1000 and not wait_change(r, timeout=0.5)
    print("desktop ok")
