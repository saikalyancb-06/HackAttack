"""needs_approval(step, protected_paths) -> bool: irreversible OR a write that touches a protected path."""
from __future__ import annotations

import os
from pathlib import Path

_PATH_KEYS = ("path", "src", "dst", "cwd", "target", "dir", "folder", "file")


def _paths(args: dict) -> list[str]:
    out = []
    for k, v in (args or {}).items():
        if isinstance(v, str) and (k in _PATH_KEYS or k.endswith("_path")) and v:
            out.append(v)
    return out


def is_protected(path: str, protected_paths: list[str]) -> bool:
    try:
        p = os.path.normcase(str(Path(path).expanduser().resolve()))
    except Exception:
        return True
    for prot in protected_paths:
        try:
            q = os.path.normcase(str(Path(prot).expanduser().resolve()))
        except Exception:
            continue
        if p == q or p.startswith(q.rstrip("\\/") + os.sep):
            return True
    return False


def needs_approval(step: dict, protected_paths: list[str]) -> bool:
    risk = step.get("risk", "read")
    if risk == "irreversible":
        return True
    if risk == "write":
        return any(is_protected(p, protected_paths) for p in _paths(step.get("args") or {}))
    return False


if __name__ == "__main__":
    prot = [r"C:\Windows"]
    assert needs_approval({"risk": "irreversible", "args": {}}, prot)
    assert needs_approval({"risk": "write", "args": {"path": r"C:\Windows\x.txt"}}, prot)
    assert not needs_approval({"risk": "write", "args": {"path": r"E:\tmp\x.txt"}}, prot)
    assert not needs_approval({"risk": "read", "args": {"path": r"C:\Windows\x.txt"}}, prot)
    print("guard.risk self-check OK")
