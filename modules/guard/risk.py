"""Approval check (A2). CONTRACTS §7."""

import os
from typing import Any


def _extract_strings(obj: Any) -> list[str]:
    """Recursively extract string values from a dict, list, or primitive."""
    strings: list[str] = []
    if isinstance(obj, str):
        strings.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            strings.extend(_extract_strings(v))
    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            strings.extend(_extract_strings(item))
    return strings


def _is_subpath_or_prefix(target: str, prefix: str) -> bool:
    """Case-insensitive prefix match on normalized path separators."""
    if not target or not prefix:
        return False
    t = target.replace("/", "\\").lower()
    p = prefix.replace("/", "\\").lower().rstrip("\\")
    return t == p or t.startswith(p + "\\")


def needs_approval(step: dict, protected_paths: list[str] | None = None) -> bool:
    """Determine if a step requires human approval.

    Rule: irreversible OR write touching a protected path.
    Protected paths are matched case-insensitively as path prefixes.
    """
    risk = step.get("risk", "write")
    if risk == "irreversible":
        return True

    if risk != "write":
        return False

    if protected_paths is None:
        env_paths = os.environ.get("PROTECTED_PATHS", "")
        protected = [p.strip() for p in env_paths.split(";") if p.strip()]
    else:
        protected = [p.strip() for p in protected_paths if p and p.strip()]

    if not protected:
        return False

    candidates: list[str] = []
    args = step.get("args")
    if args:
        candidates.extend(_extract_strings(args))
    for key in ("path", "cwd", "file", "target"):
        if step.get(key) and isinstance(step[key], str):
            candidates.append(step[key])
    prod = step.get("produces")
    if isinstance(prod, dict) and isinstance(prod.get("key"), str):
        candidates.append(prod["key"])

    for cand in candidates:
        for prot in protected:
            if _is_subpath_or_prefix(cand, prot):
                return True

    return False


if __name__ == "__main__":
    import time

    t0 = time.time()
    protected = [r"C:\Users", r"D:\Important"]

    # 1. Irreversible always needs approval
    assert needs_approval({"risk": "irreversible", "tool": "outlook_send_draft", "args": {}}, protected) is True

    # 2. Read never needs approval even if touching protected path
    assert needs_approval({"risk": "read", "tool": "excel_read_range", "args": {"path": r"C:\Users\doc.xlsx"}}, protected) is False

    # 3. Write touching protected path needs approval (case-insensitive)
    assert needs_approval({"risk": "write", "tool": "excel_upsert_rows", "args": {"path": r"c:\users\alice\report.xlsx"}}, protected) is True
    assert needs_approval({"risk": "write", "tool": "word_write_doc", "args": {"path": r"D:/Important/notes.docx"}}, protected) is True

    # 4. Write in safe path does not need approval
    assert needs_approval({"risk": "write", "tool": "excel_upsert_rows", "args": {"path": r"C:\Projects\report.xlsx"}}, protected) is False

    # 5. Non-path string in args does not falsely match
    assert needs_approval({"risk": "write", "tool": "dummy", "args": {"text": "hello world"}}, protected) is False

    print(f"guard.risk self-check PASSED in {time.time() - t0:.3f}s")
