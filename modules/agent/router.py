"""Pick route per step (C1). Capability ladder: workflow -> com -> windows -> browser -> shell.

Returns {"route", "tool", "args"} plus "install" (candidate dict) when the step must wait for an MCP install.
Internal tools (no MCP): llm.generate, research.search, kg.ask, kg.lookup.
"""
import glob
import json
import os

import yaml

from modules import llm

WORKFLOW_DIR = "workflows"
INTERNAL = {"llm.generate": "kg", "kg.ask": "kg", "kg.lookup": "kg", "research.search": "browser"}
FIRST_ROUTE = {  # classify() label -> route
    "office document or spreadsheet": "com",
    "website": "browser",
    "other desktop application": "windows",
    "files or shell": "shell",
}
SERVER_ROUTE = {"office": "com", "windows": "windows", "browser": "browser", "playwright": "browser"}


def load_workflows() -> dict[str, dict]:
    out = {}
    for path in glob.glob(os.path.join(WORKFLOW_DIR, "**", "*.yaml"), recursive=True):
        with open(path, encoding="utf-8") as f:
            wf = yaml.safe_load(f)
        if wf.get("approved"):
            out[wf["name"]] = wf
    return out


def windows_ready() -> bool:
    """WINDOWS_MCP=off starts without windows-mcp (install demo) until it is installed via the registry."""
    if os.getenv("WINDOWS_MCP", "on").lower() != "off":
        return True
    try:
        with open(os.path.join(os.getenv("DATA_DIR", "data"), "mcp_servers.json"), encoding="utf-8") as f:
            return "windows-mcp" in json.load(f).get("mcpServers", {})
    except (OSError, ValueError):
        return False


def _tools() -> list[dict]:
    from modules.tools import mcp_client

    try:
        tools = mcp_client.list_tools()
    except Exception:  # MCP down: planner/router fall back to internal tools + ladder
        return []
    return tools if windows_ready() else [t for t in tools if t.get("server") != "windows" and not t["name"].startswith("windows.")]


def _first_route(description: str) -> str:
    from modules.classify import classify

    try:
        label = classify(description, list(FIRST_ROUTE))["label"]
    except Exception:
        label = None
    if label:
        return FIRST_ROUTE[label]
    r = llm.complete(
        [{"role": "user", "content": f"Which kind of tool does this step need? Step: {description}\nOptions: {list(FIRST_ROUTE)}"}],
        json_schema={"type": "object", "properties": {"label": {"enum": list(FIRST_ROUTE)}}},
        task="local",
    )
    return FIRST_ROUTE.get(r.get("label"), "shell")


def _pick_tool(description: str, args: dict, tools: list[dict]) -> dict:
    listing = "\n".join(f"- {t['name']}: {t.get('description', '')[:120]} schema={t.get('input_schema', {})}" for t in tools[:40])
    return llm.complete(
        [{"role": "user", "content": f"Pick one tool and its args for this step.\nStep: {description}\nKnown args: {args}\nTools:\n{listing}"}],
        json_schema={"type": "object", "properties": {"tool": {"type": "string"}, "args": {"type": "object"}}},
    )


def _shell(step: dict) -> dict:
    if step.get("args", {}).get("cmd"):
        return {"route": "shell", "tool": "shell.run_command", "args": step["args"]}
    r = llm.complete(
        [{"role": "user", "content": f"Write ONE Windows PowerShell command for this step. Prefer read-only commands.\nStep: {step['description']}\nArgs: {step.get('args')}"}],
        json_schema={"type": "object", "properties": {"cmd": {"type": "string"}}},
    )
    return {"route": "shell", "tool": "shell.run_command", "args": {**step.get("args", {}), "cmd": r["cmd"]}}


def route(project_id: str, step: dict) -> dict:
    tool, args = step.get("tool") or "", step.get("args") or {}
    if tool in INTERNAL:
        return {"route": INTERNAL[tool], "tool": tool, "args": args}
    if tool in load_workflows():
        return {"route": "workflow", "tool": tool, "args": args}
    if tool == "shell.run_command":
        return _shell(step)
    if tool == "windows.explore":  # desktop control runs in-process; windows-mcp install is offered once for the demo
        if not windows_ready():
            from modules.tools import mcp_registry

            try:
                better = mcp_registry.best_for("control Windows desktop apps (UI automation): " + step["description"]).get("better")
            except Exception:
                better = None
            if better and better.get("name") == "windows-mcp":
                return {"route": "windows", "tool": tool, "args": args, "install": better}
        return {"route": "windows", "tool": tool, "args": args}

    tools = _tools()
    names = {t["name"] for t in tools}
    if tool in names:
        return {"route": SERVER_ROUTE.get(tool.split(".")[0], "com"), "tool": tool, "args": args}

    # Unknown tool: walk the ladder from the classifier's first guess.
    first = "windows" if tool == "windows.explore" else _first_route(step["description"])
    if first == "windows" and windows_ready():
        return {"route": "windows", "tool": "windows.explore", "args": args}
    candidates = [t for t in tools if SERVER_ROUTE.get(t["name"].split(".")[0]) == first]
    if candidates:
        pick = _pick_tool(step["description"], args, candidates)
        if pick.get("tool") in names:
            return {"route": first, "tool": pick["tool"], "args": pick.get("args") or args}

    # No connected tool fits: look for an MCP server (installed or in the registry).
    from modules.tools import mcp_registry

    try:
        hint = {"windows": "control Windows desktop apps (UI automation): ", "browser": "browser automation: ", "com": "Microsoft Office: "}.get(first, "")
        best = mcp_registry.best_for(hint + step["description"])
    except Exception:
        best = {"installed": None, "better": None}
    if best.get("installed"):
        server = best["installed"]["name"]
        own = [t for t in tools if t["name"].startswith(server + ".")]
        if own:
            pick = _pick_tool(step["description"], args, own)
            return {"route": SERVER_ROUTE.get(server, "com"), "tool": pick["tool"], "args": pick.get("args") or args}
    if best.get("better"):
        return {"route": first, "tool": tool, "args": args, "install": best["better"]}
    if first == "browser":
        return {"route": "browser", "tool": "research.search", "args": {"query": args.get("query") or step["description"]}}
    return _shell(step)


if __name__ == "__main__":
    wfs = load_workflows()
    assert {"excel.upsert_rows", "outlook.create_drafts"} <= set(wfs), wfs.keys()
    s = {"description": "x", "tool": "llm.generate", "args": {"prompt": "p"}}
    assert route("p", s) == {"route": "kg", "tool": "llm.generate", "args": {"prompt": "p"}}
    assert route("p", {"description": "x", "tool": "excel.upsert_rows", "args": {}})["route"] == "workflow"
    assert route("p", {"description": "x", "tool": "shell.run_command", "args": {"cmd": "dir"}})["args"]["cmd"] == "dir"
    import tempfile
    from unittest import mock

    from modules.tools import mcp_registry
    d = tempfile.mkdtemp()
    with mock.patch.dict(os.environ, {"WINDOWS_MCP": "off", "DATA_DIR": d}),          mock.patch.object(mcp_registry, "best_for", lambda need: {"installed": None, "better": {"name": "windows-mcp"}}):
        assert not windows_ready()
        r = route("p", {"description": "write a note in Notepad", "tool": "windows.explore", "args": {}})
        assert r["install"]["name"] == "windows-mcp", r
        with open(os.path.join(d, "mcp_servers.json"), "w") as f:
            json.dump({"mcpServers": {"windows-mcp": {}}}, f)
        assert route("p", {"description": "x", "tool": "windows.explore", "args": {}})["route"] == "windows"
    print("router ok")
