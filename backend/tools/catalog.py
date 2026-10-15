"""The agent's capability catalog: every tool the planner may use, its risk, route and argument schema,
plus execute() which runs one tool call with project-relative paths resolved.

Office tools go through the office_com MCP server (stdio). If that server cannot be reached the same
functions run in-process, and the result says which transport was used.
Tools from any other connected MCP server appear dynamically as "mcp.<server>.<tool>".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from backend.ledger import db

T = dict[str, Any]


def _spec(desc: str, args: dict, risk: str, route: str, required: list[str] | None = None) -> T:
    return {"description": desc, "args": args, "risk": risk, "route": route, "required": required or list(args)}


TOOLS: dict[str, T] = {
    "research.search": _spec("Web search. Returns {results:[{title,url,snippet}]}.", {"query": "string", "k": "int (default 8)"},
                             "read", "browser", ["query"]),
    "research.fetch": _spec("Fetch a web page as text. Returns {url,title,text}.", {"url": "string", "max_chars": "int (default 12000)"},
                            "read", "browser", ["url"]),
    "browser.snapshot": _spec("Open a URL in a headless browser; returns page text and a PII-blurred screenshot as evidence.",
                              {"url": "string"}, "read", "browser"),
    "llm.generate": _spec("Reason over earlier step outputs: extract, compare, rank, summarize, or write text. "
                          "format='json' returns an object shaped like `schema` (e.g. {rows:[...]} or {emails:[{to,subject,body}]}); "
                          "format='text' returns {text}.",
                          {"instruction": "string", "inputs": "any (usually {{sN}} references)", "format": "json|text",
                           "schema": "object describing the JSON to return (format=json)"}, "read", "think", ["instruction"]),
    "office.excel_upsert_rows": _spec("Insert or update rows in an Excel sheet keyed by key_column (never duplicates).",
                                      {"path": "string .xlsx", "sheet": "string", "key_column": "string", "rows": "list of objects"},
                                      "write", "com"),
    "office.excel_read_range": _spec("Read a sheet. Returns {header, records}.", {"path": "string", "sheet": "string"}, "read", "com"),
    "office.excel_find_row": _spec("Find one row by key. Returns {row|null}.",
                                   {"path": "string", "sheet": "string", "key_column": "string", "key": "string"}, "read", "com"),
    "office.word_write_doc": _spec("Write a Word .docx (or .md/.txt) document; '#' lines become headings.",
                                   {"path": "string", "text": "string"}, "write", "com"),
    "office.outlook_create_draft": _spec("Create an email draft for review (never sends). Saved as .eml if Outlook is absent.",
                                         {"to": "email", "subject": "string", "body": "string"}, "write", "com"),
    "office.outlook_send_draft": _spec("Send a draft created earlier. Always needs human approval.", {"draft_id": "string"},
                                       "irreversible", "com"),
    "files.read_text": _spec("Read a text/docx/pdf/xlsx file as text.", {"path": "string"}, "read", "shell"),
    "files.write_text": _spec("Write a text file (creates folders).", {"path": "string", "text": "string"}, "write", "shell"),
    "files.append_text": _spec("Append to a text file.", {"path": "string", "text": "string"}, "write", "shell"),
    "files.write_csv": _spec("Write rows to a CSV file.", {"path": "string", "rows": "list of objects"}, "write", "shell"),
    "files.list_dir": _spec("List a folder.", {"path": "string", "pattern": "glob (default *)", "recursive": "bool"}, "read", "shell", ["path"]),
    "files.find_files": _spec("Find files by name pattern under a folder.", {"root": "string", "pattern": "glob"}, "read", "shell"),
    "files.make_dir": _spec("Create a folder.", {"path": "string"}, "write", "shell"),
    "files.copy": _spec("Copy a file or folder.", {"src": "string", "dst": "string"}, "write", "shell"),
    "files.move": _spec("Move or rename a file or folder.", {"src": "string", "dst": "string"}, "write", "shell"),
    "files.delete": _spec("Delete a file or folder by sending it to the Recycle Bin. Always needs approval.", {"path": "string"},
                          "irreversible", "shell"),
    "shell.run_command": _spec("Run a PowerShell (or cmd) command. Non-read commands need approval.",
                               {"cmd": "string", "shell": "powershell|cmd", "cwd": "string (optional)"}, "write", "shell", ["cmd"]),
    "kg.ask": _spec("Answer a question from this project's knowledge graph (previous runs, artifacts, facts).",
                    {"question": "string"}, "read", "kg"),
    "kg.search": _spec("Search knowledge graph nodes by text.", {"text": "string", "type": "Artifact|Fact|Entity|Task|Workflow"},
                       "read", "kg", ["text"]),
    "windows.explore": _spec("Operate a Windows desktop app that has no API (Notepad, Paint, Settings, Calculator...) by "
                             "reading its UI tree and clicking/typing step by step.",
                             {"app": "string", "task": "string"}, "write", "windows"),
}


def all_tools() -> dict[str, T]:
    """Static catalog + tools from connected non-office MCP servers."""
    out = dict(TOOLS)
    try:
        from backend.tools import mcp_client
        for t in mcp_client.list_tools():
            if t["server"] == "office_com":
                continue
            props = (t.get("input_schema") or {}).get("properties", {})
            out[t["qualified"]] = _spec(t["description"][:300], {k: v.get("type", "any") for k, v in props.items()},
                                        t["risk"], "mcp", (t.get("input_schema") or {}).get("required", []))
    except Exception:
        pass
    return out


def catalog_for_prompt() -> str:
    lines = []
    for name, t in all_tools().items():
        args = ", ".join(f"{k}: {v}" for k, v in t["args"].items())
        lines.append(f"- {name}({args}) [risk={t['risk']}] - {t['description']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- execution
_PATH_ARGS = ("path", "src", "dst", "cwd", "root")


def resolve_paths(project_id: str, args: dict) -> dict:
    base = db.project_dir(project_id) / "files"
    out = dict(args)
    for k in _PATH_ARGS:
        v = out.get(k)
        if isinstance(v, str) and v.strip():
            p = Path(v.strip().strip('"'))
            p = Path(str(p).replace("~", str(Path.home()), 1)) if str(p).startswith("~") else p
            out[k] = str(p if p.is_absolute() else (base / p).resolve())
    return out


def _office(name: str, args: dict) -> Any:
    from backend.tools import mcp_client, office
    try:
        result = mcp_client.call(f"office.{name}", args)
        if isinstance(result, dict):
            result.setdefault("transport", "mcp")
        return result
    except (ConnectionError, KeyError) as e:
        fn: Callable = getattr(office, name)
        result = fn(**args)
        if isinstance(result, dict):
            result["transport"] = f"in-process ({str(e)[:80]})"
        return result


def execute(project_id: str, run_id: str, tool: str, args: dict, step: dict | None = None) -> Any:
    args = resolve_paths(project_id, {k: v for k, v in (args or {}).items() if v is not None})
    files_dir = str(db.project_dir(project_id) / "files")
    if tool.startswith("office."):
        name = tool.split(".", 1)[1]
        if name in ("outlook_create_draft", "outlook_send_draft"):
            args.setdefault("drafts_dir", str(Path(files_dir) / "drafts"))
        if name in ("excel_read_range", "excel_find_row", "excel_open") and "range" in args and name != "excel_read_range":
            args.pop("range")
        return _office(name, args)
    if tool.startswith("research."):
        from backend.tools import research
        if tool == "research.search":
            return research.search(str(args["query"]), int(args.get("k", 8)))
        return research.fetch(str(args["url"]), int(args.get("max_chars", 12000)))
    if tool == "browser.snapshot":
        from backend.tools import browser
        import secrets
        out = Path(files_dir) / "evidence" / f"shot_{secrets.token_hex(3)}.png"
        snap = browser.snapshot(str(args["url"]), str(out))
        if snap["regions"]:
            db.bump_privacy(project_id, run_id, "regions_blurred", len(snap["regions"]))
        return {"title": snap["title"], "text": snap["text"][:8000], "screenshot": snap["screenshot"],
                "regions_blurred": len(snap["regions"])}
    if tool == "llm.generate":
        from backend.agent import think
        return think.generate(project_id, run_id, str(args.get("instruction", "")), args.get("inputs"),
                              str(args.get("format", "json")), args.get("schema"))
    if tool.startswith("files."):
        from backend.tools import files
        fn = getattr(files, tool.split(".", 1)[1], None)
        if fn is None:
            raise KeyError(f"unknown files tool {tool}")
        return fn(**args)
    if tool == "shell.run_command":
        from backend.tools import shell
        return shell.run_command(str(args["cmd"]), args.get("shell", "powershell"), args.get("cwd") or files_dir,
                                 int(args.get("timeout", 60)))
    if tool == "kg.ask":
        from backend.kg import rag
        return rag.ask(project_id, str(args["question"]))
    if tool == "kg.search":
        from backend.kg import store
        return {"nodes": store.search_nodes(project_id, args.get("type"), args.get("text"), 20)}
    if tool == "windows.explore":
        from backend.agent import explore
        return explore.explore(project_id, run_id, step or {"description": args.get("task", ""), "args": args})
    if tool.startswith("mcp."):
        from backend.tools import mcp_client
        return mcp_client.call(tool, args)
    raise KeyError(f"no tool named {tool!r}")


def tool_risk(tool: str, args: dict | None = None) -> str:
    t = all_tools().get(tool)
    return t["risk"] if t else "write"


def tool_route(tool: str) -> str:
    t = all_tools().get(tool)
    return t["route"] if t else ("mcp" if tool.startswith("mcp.") else "kg")
