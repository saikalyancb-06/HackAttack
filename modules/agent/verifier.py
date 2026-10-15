"""Independent checks (C1). Never trust a tool's own success message: read back.

verify(project_id, step, output) -> {"ok": bool, "evidence": dict}
`step` carries the executed tool/args (unmasked); `output` is what execution returned.
For workflows, output = {"results": [{"tool", "args", "result"}, ...]} and each part is checked.
"""
import os


def _call(tool: str, args: dict) -> dict:
    from modules.tools import mcp_client

    return mcp_client.call(tool, args)


def _excel_rows(args: dict, result: dict) -> dict:
    if result.get("fallback"):  # degraded: read the fallback file back
        path = result["fallback"]
        if path.endswith(".csv"):
            import csv

            with open(path, newline="", encoding="utf-8") as f:
                keys = {r.get(args["key_column"]) for r in csv.DictReader(f)}
        else:
            from openpyxl import load_workbook

            ws = load_workbook(path)[args["sheet"]]
            head = [c.value for c in ws[1]]
            col = head.index(args["key_column"])
            keys = {str(r[col]) for r in ws.iter_rows(min_row=2, values_only=True)}
        missing = [r[args["key_column"]] for r in args["rows"] if str(r[args["key_column"]]) not in keys]
        return {"ok": not missing, "evidence": {"file": path, "missing": missing, "checked": len(args["rows"])}}
    missing, mismatched = [], []
    for row in args["rows"]:
        key = row[args["key_column"]]
        found = _call("office.excel_find_row", {"path": args["path"], "sheet": args["sheet"], "key_column": args["key_column"], "key": key}).get("row")
        if not found:
            missing.append(key)
        elif any(str(found.get(k)) != str(v) for k, v in row.items() if v is not None):
            mismatched.append(key)
    return {"ok": not missing and not mismatched, "evidence": {"checked": len(args["rows"]), "missing": missing, "mismatched": mismatched}}


def _one(tool: str, args: dict, result) -> dict:
    result = result if result is not None else {}
    if isinstance(result, dict) and result.get("error"):
        return {"ok": False, "evidence": {"error": result["error"]}}
    if tool.endswith("excel_upsert_rows"):
        return _excel_rows(args, result)
    if tool.endswith("word_write_doc"):
        path = result.get("path") or args.get("path")
        return {"ok": bool(path) and os.path.exists(path), "evidence": {"file": path}}
    if tool.endswith("outlook_create_draft"):
        return {"ok": bool(result.get("draft_id")), "evidence": {"draft_id": result.get("draft_id")}}
    if tool.endswith("outlook_send_draft"):
        return {"ok": result.get("sent") is True, "evidence": result}
    if tool == "research.search":
        return {"ok": bool(result.get("results")), "evidence": {"results": len(result.get("results", []))}}
    if tool == "shell.run_command":
        return {"ok": result.get("exit_code") == 0, "evidence": {"exit_code": result.get("exit_code"), "stderr": (result.get("stderr") or "")[:300]}}
    if tool == "windows.explore":
        return {"ok": bool(result.get("ok")), "evidence": result.get("evidence") or {"error": result.get("error")}}
    if tool == "llm.generate":
        expect = args.get("schema", {}).get("properties", {})
        missing = [k for k in expect if k not in result]
        return {"ok": bool(result) and not missing, "evidence": {"keys": sorted(result) if isinstance(result, dict) else [], "missing": missing}}
    if tool.startswith("kg."):
        return {"ok": bool(result), "evidence": {"sources": result.get("sources", []) if isinstance(result, dict) else []}}
    # Unknown MCP tool: the best we can do is a non-empty, error-free result.
    return {"ok": bool(result), "evidence": {"note": "no read-back check for this tool"}}


def verify(project_id: str, step: dict, output: object) -> dict:
    if isinstance(output, dict) and "results" in output and step.get("route") == "workflow":
        parts = [_one(r["tool"], r["args"], r["result"]) for r in output["results"]]
        return {"ok": bool(parts) and all(p["ok"] for p in parts), "evidence": {"parts": [p["evidence"] for p in parts]}}
    return _one(step.get("tool") or "", step.get("args") or {}, output)


if __name__ == "__main__":
    import tempfile

    from openpyxl import Workbook

    p = os.path.join(tempfile.mkdtemp(), "t.xlsx")
    wb = Workbook(); ws = wb.active; ws.title = "S"; ws.append(["key", "title"]); ws.append(["k1", "A"]); wb.save(p)
    args = {"path": p, "sheet": "S", "key_column": "key", "rows": [{"key": "k1", "title": "A"}]}
    assert _one("office.excel_upsert_rows", args, {"fallback": p})["ok"]
    assert not _one("office.excel_upsert_rows", {**args, "rows": [{"key": "k2"}]}, {"fallback": p})["ok"]
    assert verify("p", {"tool": "llm.generate", "args": {"schema": {"properties": {"rows": {}}}}}, {"rows": []})["ok"]
    assert not verify("p", {"tool": "llm.generate", "args": {"schema": {"properties": {"rows": {}}}}}, {"x": 1})["ok"]
    assert not verify("p", {"tool": "shell.run_command"}, {"exit_code": 1})["ok"]
    wf = {"results": [{"tool": "office.outlook_create_draft", "args": {}, "result": {"draft_id": "d1"}}]}
    assert verify("p", {"route": "workflow"}, wf)["ok"]
    print("verifier ok")
