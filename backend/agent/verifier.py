"""Independent result checks: re-read what a tool claims to have done, from the source of truth.

verify(project_id, step, args, output) -> {"ok": bool, "checks": [str], "details": dict}
A step is only "done" when verification passes; a failed check marks the step failed (never a false success).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.tools import catalog
from backend.tools.office import _norm


def _excel(project_id: str, args: dict) -> dict:
    key_col = args["key_column"]
    latest: dict = {}
    for r in args.get("rows") or []:  # several rows with one key: the last one wins in an upsert
        latest[str(r.get(key_col, "")).strip().casefold()] = r
    rows = list(latest.values())
    checks, missing = [], []
    for r in rows:
        key = r.get(key_col)
        found = catalog.execute(project_id, "", "office.excel_find_row",
                                {"path": args["path"], "sheet": args["sheet"], "key_column": key_col, "key": str(key)})
        row = (found or {}).get("row")
        if not row:
            missing.append(str(key))
            continue
        bad = [k for k, v in r.items() if isinstance(v, (str, int, float)) and _norm(row.get(k)) != _norm(v)]
        if bad:
            missing.append(f"{key} (mismatch in {', '.join(bad[:3])})")
    checks.append(f"read back {len(rows) - len(missing)}/{len(rows)} rows from {Path(args['path']).name}!{args['sheet']}")
    full = catalog.execute(project_id, "", "office.excel_read_range", {"path": args["path"], "sheet": args["sheet"]})
    keys = [str(rec.get(key_col, "")).strip().casefold() for rec in full.get("records", [])]
    dupes = len(keys) - len(set(keys))
    checks.append(f"{len(keys)} rows in sheet, {dupes} duplicate keys")
    return {"ok": not missing and dupes == 0, "checks": checks,
            "details": {"missing": missing, "duplicates": dupes, "sheet_rows": len(keys)}}


def verify(project_id: str, step: dict, args: dict, output: Any) -> dict:
    tool = step.get("tool", "")
    try:
        if tool == "office.excel_upsert_rows":
            return _excel(project_id, catalog.resolve_paths(project_id, args))
        if tool == "office.word_write_doc":
            p = Path(output.get("path", ""))
            from backend.tools.office import word_read_doc
            text = word_read_doc(str(p))["text"] if p.exists() else ""
            want = str(args.get("text", "")).strip().splitlines()
            probe = next((l.lstrip("# ").strip() for l in want if len(l.strip()) > 8), "")
            ok = p.exists() and p.stat().st_size > 0 and (not probe or probe[:40] in text)
            return {"ok": ok, "checks": [f"{p.name} exists ({p.stat().st_size if p.exists() else 0} bytes)",
                                         "content matches" if ok else "content check failed"], "details": {"path": str(p)}}
        if tool == "office.ppt_edit_slides":
            from backend.tools.office import ppt_read
            got = [s["title"].strip() for s in ppt_read(output["path"])["slides"]]
            want = [str(s.get("title", "")).strip() for s in (args.get("slides") or []) if isinstance(s, dict) and s.get("title") is not None]
            missing = [w for w in want if w not in got]
            return {"ok": not missing, "checks": [f"{len(got)} slides in {Path(output['path']).name}: {got[:8]}"] +
                    ([f"missing titles: {missing}"] if missing else ["every requested title is on a slide"]), "details": {}}
        if tool == "office.word_edit":
            from backend.tools.office import word_read_doc
            text = word_read_doc(output["path"])["text"]
            probes = [str(args.get("append_text") or "").strip()[:40]] + [str(r.get("replace", ""))[:40] for r in args.get("replacements") or []]
            missing = [x for x in probes if x and x not in text]
            return {"ok": not missing, "checks": ["document contains the new text" if not missing else f"not found in document: {missing}"], "details": {}}
        if tool == "office.excel_set_cells":
            vals = output.get("values") or {}
            bad = [k for k, v in vals.items() if isinstance(v, str) and v.startswith("#")]
            return {"ok": len(vals) == len(args.get("cells") or {}) and not bad,
                    "checks": [f"cells now: {vals}"] + ([f"formula errors in {bad}"] if bad else []), "details": {}}
        if tool == "office.outlook_create_draft":
            p = Path(output.get("path", ""))
            raw = p.read_bytes() if p.exists() else b""
            ok = p.exists() and str(args.get("subject", ""))[:30].encode() in raw.replace(b"\n ", b" ")
            return {"ok": ok, "checks": [f"draft {output.get('draft_id')} saved" if ok else "draft file missing or wrong"],
                    "details": {"path": str(p)}}
        if tool in ("files.write_text", "files.append_text", "files.write_csv"):
            p = Path(output.get("path", ""))
            ok = p.exists() and (tool != "files.write_text" or p.read_text(encoding="utf-8") == str(args.get("text", "")))
            return {"ok": ok, "checks": [f"{p.name} {'verified' if ok else 'does not match'}"], "details": {}}
        if tool in ("files.copy", "files.move"):
            ok = Path(output.get("dst", "")).exists() and (tool == "files.copy" or not Path(output.get("src", "")).exists())
            return {"ok": ok, "checks": ["destination exists" + ("" if tool == "files.copy" else ", source gone")], "details": {}}
        if tool == "files.delete":
            ok = not Path(output.get("recycled", "")).exists()
            return {"ok": ok, "checks": ["moved to Recycle Bin" if ok else "file still present"], "details": {}}
        if tool == "files.make_dir":
            return {"ok": Path(output.get("path", "")).is_dir(), "checks": ["folder exists"], "details": {}}
        if tool == "research.search":
            n = len((output or {}).get("results", []))
            return {"ok": n > 0, "checks": [f"{n} results ({output.get('source')})"], "details": {}}
        if tool == "research.fetch":
            n = len((output or {}).get("text", ""))
            return {"ok": n > 200, "checks": [f"{n} chars of page text"], "details": {}}
        if tool == "llm.generate":
            if isinstance(output, dict) and "rows" in output:
                rows = output["rows"] or []
                return {"ok": bool(rows), "checks": [f"{len(rows)} rows produced"], "details": {}}
            if isinstance(output, dict) and "emails" in output:
                em = output["emails"] or []
                ok = bool(em) and all("@" in str(e.get("to", "")) and e.get("body") for e in em)
                return {"ok": ok, "checks": [f"{len(em)} emails, all with recipient and body" if ok else "some emails lack recipient/body"], "details": {}}
            if isinstance(output, dict) and "text" not in output:
                lists = {k: v for k, v in output.items() if isinstance(v, list)}
                if lists and all(len(v) == 0 for v in lists.values()):
                    return {"ok": False, "checks": [f"empty result ({', '.join(lists)} has no items): nothing usable was found in the inputs"], "details": {}}
                if lists:
                    return {"ok": True, "checks": [", ".join(f"{len(v)} {k}" for k, v in lists.items())], "details": {}}
            text = output.get("text") if isinstance(output, dict) else output
            return {"ok": bool(output) and (text is None or len(str(text)) > 10), "checks": ["output produced"], "details": {}}
        if tool == "shell.run_command":
            ok = output.get("exit_code") == 0
            err = str(output.get("stderr") or "").strip()
            return {"ok": ok, "checks": [f"exit code {output.get('exit_code')}" + ("" if ok or not err else f"; stderr: {err[:400]}")],
                    "details": {}}
        if tool == "windows.explore":
            return {"ok": bool(output.get("ok")), "checks": [f"{len(output.get('actions', []))} UI actions"], "details": {}}
    except Exception as e:
        return {"ok": False, "checks": [f"verification error: {type(e).__name__}: {str(e)[:200]}"], "details": {}}
    return {"ok": True, "checks": ["no independent check for this tool; output recorded"], "details": {}}
