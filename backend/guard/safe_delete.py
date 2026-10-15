"""safe_delete(path) -> {"recycled": path}. Never hard-deletes: sends to the Recycle Bin. Risk is always irreversible."""
from __future__ import annotations

from pathlib import Path

from send2trash import send2trash

RISK = "irreversible"


def safe_delete(path: str) -> dict:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"nothing to delete at {p}")
    if p.anchor and str(p) == p.anchor:
        raise PermissionError("refusing to delete a drive root")
    send2trash(str(p))
    return {"recycled": str(p)}


if __name__ == "__main__":
    import tempfile
    f = Path(tempfile.mkdtemp()) / "x.txt"
    f.write_text("bye")
    assert safe_delete(str(f))["recycled"].endswith("x.txt") and not f.exists()
    print("guard.safe_delete self-check OK")
