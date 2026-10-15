"""File tools. Relative paths resolve inside the project's files/ folder. Deletes go to the Recycle Bin."""
from __future__ import annotations

import csv
import fnmatch
import io
import os
import shutil
from pathlib import Path

from backend.guard.safe_delete import safe_delete


def read_text(path: str, max_chars: int = 50000) -> dict:
    p = Path(path)
    if p.is_dir():  # a folder: list it and include its main text files, instead of failing
        listing = list_dir(str(p), recursive=True, limit=200)["entries"]
        parts, used = [], 0
        for e in sorted(listing, key=lambda e: (e["is_dir"], e["size"])):
            if e["is_dir"] or e["size"] > 400_000 or Path(e["path"]).suffix.lower() not in (".txt", ".md", ".html", ".htm", ".json", ".csv", ".py", ".js", ".yaml", ".yml"):
                continue
            body = Path(e["path"]).read_text(encoding="utf-8", errors="replace")[:4000]
            parts.append(f"--- {e['path']}\n{body}")
            used += len(body)
            if used > max_chars:
                break
        return {"path": str(p), "is_dir": True, "entries": [e["path"] for e in listing][:200],
                "text": "\n".join(parts)[:max_chars]}
    if not p.is_file():
        raise FileNotFoundError(f"no file at {p}")
    if p.suffix.lower() == ".docx":
        from backend.tools.office import word_read_doc
        text = word_read_doc(str(p))["text"]
    elif p.suffix.lower() == ".pdf":
        import fitz  # pymupdf
        text = "\n".join(page.get_text() for page in fitz.open(p))
    elif p.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
        text = "\n".join(f"[{ws.title}]\n" + "\n".join("\t".join("" if v is None else str(v) for v in r)
                                                      for r in ws.iter_rows(values_only=True)) for ws in wb.worksheets)
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
    return {"path": str(p), "text": text[:max_chars], "truncated": len(text) > max_chars, "chars": len(text)}


def write_text(path: str, text: str) -> dict:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existed = p.exists()
    p.write_text(text if isinstance(text, str) else str(text), encoding="utf-8")
    return {"path": str(p), "bytes": p.stat().st_size, "overwrote": existed}


def append_text(path: str, text: str) -> dict:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(text)
    return {"path": str(p), "bytes": p.stat().st_size}


def write_csv(path: str, rows: list[dict]) -> dict:
    if not rows:
        raise ValueError("no rows to write")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    cols: list[str] = []
    for r in rows:
        cols += [k for k in r if k not in cols]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in cols})
    p.write_text(buf.getvalue(), encoding="utf-8")
    return {"path": str(p), "rows": len(rows)}


def list_dir(path: str, pattern: str = "*", recursive: bool = False, limit: int = 500) -> dict:
    p = Path(path)
    if not p.is_dir():
        raise FileNotFoundError(f"no folder at {p}")
    it = p.rglob(pattern) if recursive else p.glob(pattern)
    entries = []
    for e in it:
        try:
            st = e.stat()
        except OSError:
            continue
        entries.append({"name": e.name, "path": str(e), "is_dir": e.is_dir(), "size": st.st_size, "modified": int(st.st_mtime)})
        if len(entries) >= limit:
            break
    return {"path": str(p), "entries": entries, "count": len(entries)}


def find_files(root: str, pattern: str, limit: int = 200) -> dict:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("node_modules", "__pycache__")]
        for f in filenames:
            if fnmatch.fnmatch(f.lower(), pattern.lower()):
                out.append(os.path.join(dirpath, f))
                if len(out) >= limit:
                    return {"matches": out, "count": len(out), "truncated": True}
    return {"matches": out, "count": len(out), "truncated": False}


def make_dir(path: str) -> dict:
    Path(path).mkdir(parents=True, exist_ok=True)
    return {"path": str(Path(path))}


def copy(src: str, dst: str) -> dict:
    s, d = Path(src), Path(dst)
    if not s.exists():
        raise FileNotFoundError(f"no file at {s}")
    d.parent.mkdir(parents=True, exist_ok=True)
    if s.is_dir():
        shutil.copytree(s, d, dirs_exist_ok=True)
    else:
        shutil.copy2(s, d)
    return {"src": str(s), "dst": str(d)}


def move(src: str, dst: str) -> dict:
    s, d = Path(src), Path(dst)
    if not s.exists():
        raise FileNotFoundError(f"no file at {s}")
    if d.exists() and d.is_dir():
        d = d / s.name
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    return {"src": str(s), "dst": str(d)}


def delete(path: str) -> dict:
    return safe_delete(path)


if __name__ == "__main__":
    import tempfile
    t = Path(tempfile.mkdtemp())
    write_text(str(t / "a.txt"), "hello")
    assert read_text(str(t / "a.txt"))["text"] == "hello"
    write_csv(str(t / "r.csv"), [{"a": 1}, {"a": 2, "b": 3}])
    assert "a,b" in read_text(str(t / "r.csv"))["text"]
    assert list_dir(str(t))["count"] == 2
    copy(str(t / "a.txt"), str(t / "sub" / "b.txt"))
    move(str(t / "sub" / "b.txt"), str(t / "c.txt"))
    assert find_files(str(t), "*.txt")["count"] == 2
    delete(str(t / "c.txt"))
    assert not (t / "c.txt").exists()
    print("tools.files self-check OK")
