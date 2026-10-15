"""mcp-use client with office and windows-mcp servers (A2). CONTRACTS §10, §11."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

MCP_SERVERS_JSON = Path("data/mcp_servers.json")

TOOL_RISKS = {
    "office.excel_open": "read",
    "office.excel_read_range": "read",
    "office.excel_find_row": "read",
    "office.set_outage": "read",
    "office.excel_upsert_rows": "write",
    "office.word_write_doc": "write",
    "office.outlook_create_draft": "write",
    "office.outlook_send_draft": "irreversible",
    "windows.get_ui_tree": "read",
    "windows.take_screenshot": "read",
    "windows.get_cursor_position": "read",
    "windows.click": "write",
    "windows.type_text": "write",
    "windows.press_key": "write",
    "windows.launch_app": "write",
}

OFFICE_TOOLS_META = [
    {
        "name": "office.excel_open",
        "description": "Open or create an Excel workbook and return its sheet names.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "visible": {"type": "boolean", "default": True}},
            "required": ["path"],
        },
    },
    {
        "name": "office.excel_read_range",
        "description": "Read a rectangular range of cells from an Excel sheet.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "sheet": {"type": "string"}, "range": {"type": "string", "default": "A1:Z1000"}},
            "required": ["path", "sheet"],
        },
    },
    {
        "name": "office.excel_upsert_rows",
        "description": "Insert or update rows in an Excel sheet matching on key_column.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "sheet": {"type": "string"},
                "key_column": {"type": "string"},
                "rows": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["path", "sheet", "key_column", "rows"],
        },
    },
    {
        "name": "office.excel_find_row",
        "description": "Find a row in an Excel sheet by key_column value.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "sheet": {"type": "string"}, "key_column": {"type": "string"}, "key": {}},
            "required": ["path", "sheet", "key_column", "key"],
        },
    },
    {
        "name": "office.word_write_doc",
        "description": "Create or overwrite a Word document with text content.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "text": {"type": "string"}},
            "required": ["path", "text"],
        },
    },
    {
        "name": "office.outlook_create_draft",
        "description": "Create an email draft in Outlook or fallback to an .eml file.",
        "input_schema": {
            "type": "object",
            "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "office.outlook_send_draft",
        "description": "Send an existing Outlook draft by draft_id.",
        "input_schema": {
            "type": "object",
            "properties": {"draft_id": {"type": "string"}},
            "required": ["draft_id"],
        },
    },
    {
        "name": "office.set_outage",
        "description": "Toggle simulated Excel outage.",
        "input_schema": {
            "type": "object",
            "properties": {"on": {"type": "boolean"}},
            "required": ["on"],
        },
    },
]

WINDOWS_TOOLS_META = [
    {
        "name": "windows.click",
        "description": "Click on UI element or coordinate.",
        "input_schema": {
            "type": "object",
            "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "element_id": {"type": "string"}},
        },
    },
    {
        "name": "windows.type_text",
        "description": "Type text into active or specified window.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "windows.press_key",
        "description": "Press a key or key combination.",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    },
    {
        "name": "windows.get_ui_tree",
        "description": "Get accessibility UI tree of active window or desktop.",
        "input_schema": {
            "type": "object",
            "properties": {"window": {"type": "string"}},
        },
    },
    {
        "name": "windows.take_screenshot",
        "description": "Take screenshot of current desktop or window.",
        "input_schema": {
            "type": "object",
            "properties": {"window": {"type": "string"}},
        },
    },
    {
        "name": "windows.launch_app",
        "description": "Launch a Windows application by name or path.",
        "input_schema": {
            "type": "object",
            "properties": {"app_name": {"type": "string"}},
            "required": ["app_name"],
        },
    },
    {
        "name": "windows.get_cursor_position",
        "description": "Get current mouse cursor position.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

_last_mtime: float | None = None
_installed_servers: dict[str, dict] = {}


def _get_risk(name: str) -> str:
    if name in TOOL_RISKS:
        return TOOL_RISKS[name]
    lower = name.lower()
    for word in ("delete", "send", "submit", "remove", "overwrite"):
        if word in lower:
            return "irreversible"
    for word in ("read", "get", "find", "list", "search", "tree", "screenshot", "snapshot", "wait", "scrape"):
        if word in lower:
            return "read"
    return "write"


def reload() -> None:
    """Force re-reading of data/mcp_servers.json."""
    global _last_mtime, _installed_servers
    if MCP_SERVERS_JSON.is_file():
        try:
            _last_mtime = os.path.getmtime(MCP_SERVERS_JSON)
            with open(MCP_SERVERS_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
                _installed_servers = data.get("mcpServers", {})
        except Exception:
            _installed_servers = {}
    else:
        _last_mtime = None
        _installed_servers = {}


def _check_servers_file_sync() -> None:
    """Check mtime of data/mcp_servers.json and reload if changed."""
    global _last_mtime
    if MCP_SERVERS_JSON.is_file():
        mtime = os.path.getmtime(MCP_SERVERS_JSON)
        if _last_mtime != mtime:
            reload()
    elif _last_mtime is not None:
        reload()


def _is_windows_connected() -> bool:
    """Check whether windows-mcp server should be connected."""
    _check_servers_file_sync()
    if "windows-mcp" in _installed_servers or "windows" in _installed_servers:
        return True

    env_val = os.environ.get("WINDOWS_MCP", "").strip().lower()
    if env_val in ("off", "0", "false"):
        return False

    return False


def _win_conf() -> dict:
    conf = dict(_installed_servers.get("windows-mcp") or _installed_servers.get("windows") or {"command": "uvx", "args": ["windows-mcp"]})
    args = list(conf.get("args") or [])
    if any("windows-mcp" in a for a in args + [conf.get("command", "")]) and "serve" not in args:
        args.append("serve")  # current windows-mcp CLI needs the subcommand
    return {**conf, "args": args}


def _win_session(fn):
    """Run fn(session) against a fresh windows-mcp stdio session (in a worker thread with its own event loop)."""
    from mcp_use import MCPClient
    import concurrent.futures

    async def go():
        client = MCPClient.from_dict({"mcpServers": {"w": _win_conf()}})
        try:
            return await fn(await client.create_session("w"))
        finally:
            await client.close_all_sessions()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(go())).result()


_win_cache: dict = {}


def _windows_tools() -> list[dict]:
    key = json.dumps(_win_conf(), sort_keys=True)
    if key not in _win_cache:
        try:
            ts = _win_session(lambda sess: sess.list_tools())
            _win_cache[key] = [{"name": "windows." + t.name, "description": t.description or "", "input_schema": t.inputSchema, "schema": t.inputSchema} for t in ts]
        except Exception as e:
            print(f"[mcp_client] windows-mcp list_tools failed: {e}")
            return []
    return _win_cache[key]


def list_tools() -> list[dict]:
    """Return all currently available tools from connected MCP servers.

    Returns list[{"name", "description", "input_schema", "schema", "risk", "server"}].
    """
    _check_servers_file_sync()
    tools: list[dict] = []

    # 1. Office tools (always connected)
    for meta in OFFICE_TOOLS_META:
        t = {
            "name": meta["name"],
            "description": meta["description"],
            "input_schema": meta["input_schema"],
            "schema": meta["input_schema"],
            "risk": _get_risk(meta["name"]),
            "server": "office",
        }
        tools.append(t)

    # 2. Windows tools (connected when not WINDOWS_MCP=off or when installed in mcp_servers.json)
    if _is_windows_connected():
        for t in _windows_tools():
            tools.append({**t, "risk": _get_risk(t["name"]), "server": "windows"})

    # 3. Any additional dynamically installed servers in data/mcp_servers.json
    for srv_name, srv_conf in _installed_servers.items():
        if srv_name in ("office", "office-com", "windows", "windows-mcp"):
            continue
        # Generic registration for dynamically added servers
        generic_name = f"{srv_name}.execute"
        tools.append({
            "name": generic_name,
            "description": f"Execute tool provided by {srv_name}",
            "input_schema": {"type": "object", "properties": {"args": {"type": "object"}}},
            "schema": {"type": "object", "properties": {"args": {"type": "object"}}},
            "risk": _get_risk(generic_name),
            "server": srv_name,
        })

    return tools


def call(tool: str, args: dict) -> dict:
    """Call an MCP tool by name. Raises on tool error or if server is disconnected."""
    _check_servers_file_sync()

    if tool.startswith("office."):
        func_name = tool[len("office."):]
        from modules.mcp_servers.office_com import server as office_server

        if not hasattr(office_server, func_name):
            raise ValueError(f"Unknown office tool: {tool}")
        func = getattr(office_server, func_name)
        res = func(**args)
        if isinstance(res, dict):
            return res
        return {"result": res}

    if tool.startswith("windows."):
        if not _is_windows_connected():
            raise RuntimeError(f"Tool '{tool}' unavailable: windows-mcp server is not connected (WINDOWS_MCP=off)")

        tool_fn = tool.split(".", 1)[1]
        try:
            res = _win_session(lambda sess: sess.call_tool(tool_fn, args))
        except Exception as e:
            raise RuntimeError(f"windows-mcp call error: {e}") from e
        text = "\n".join(getattr(c, "text", "") for c in (res.content or []))
        return {"error": text or "tool error"} if res.isError else {"ok": True, "text": text}

    server_prefix = tool.split(".", 1)[0]
    if server_prefix in _installed_servers:
        try:
            from mcp_use import MCPClient

            client = MCPClient(config={"mcpServers": _installed_servers})

            async def _invoke_generic():
                session = await client.create_session(server_prefix)
                tool_fn = tool.split(".", 1)[1] if "." in tool else tool
                return await session.call_tool(tool_fn, args)

            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(_invoke_generic())).result()
        except Exception as e:
            raise RuntimeError(f"Server '{server_prefix}' call error: {e}") from e

    raise ValueError(f"Unknown or unconfigured tool server: {tool}")


if __name__ == "__main__":
    import tempfile
    import time

    t0 = time.time()
    orig_env = os.environ.get("WINDOWS_MCP")
    os.environ["WINDOWS_MCP"] = "off"

    # Use isolated test file for mcp_servers.json
    orig_json_path = MCP_SERVERS_JSON
    tmp_json = Path(tempfile.mktemp(prefix="mcp_servers_", suffix=".json"))
    with open(tmp_json, "w", encoding="utf-8") as f:
        json.dump({"mcpServers": {}}, f)

    globals()["MCP_SERVERS_JSON"] = tmp_json
    reload()

    try:
        # 1. WINDOWS_MCP=off and windows-mcp not in json -> no windows tools
        t_list1 = list_tools()
        names1 = [t["name"] for t in t_list1]
        assert any(n.startswith("office.") for n in names1), "Office tools should be available"
        assert not any(n.startswith("windows.") for n in names1), f"Windows tools must NOT be connected when WINDOWS_MCP=off: {names1}"

        # Calling windows tool raises error
        win_blocked = False
        try:
            call("windows.click", {"x": 100, "y": 200})
        except RuntimeError:
            win_blocked = True
        assert win_blocked, "Calling disconnected windows tool must raise RuntimeError"

        # 2. Office tool call works
        outage_res = call("office.set_outage", {"on": False})
        assert outage_res == {"outage": False}

        # 3. Simulate mcp_registry.install writing windows-mcp to mcp_servers.json
        # Wait a tiny bit to guarantee mtime difference
        time.sleep(0.05)
        with open(tmp_json, "w", encoding="utf-8") as f:
            json.dump({
                "mcpServers": {
                    "windows-mcp": {
                        "command": "uvx",
                        "args": ["windows-mcp"]
                    }
                }
            }, f)

        # Re-check list_tools() without restart -> windows tools must now be connected
        t_list2 = list_tools()
        names2 = [t["name"] for t in t_list2]
        assert any(n.startswith("windows.") for n in names2), f"Windows tools must be connected after install: {names2}"
        assert "windows.click" in names2
        assert "windows.take_screenshot" in names2

        # Verify risk labels
        risk_map = {t["name"]: t["risk"] for t in t_list2}
        assert risk_map["office.excel_read_range"] == "read"
        assert risk_map["office.excel_upsert_rows"] == "write"
        assert risk_map["office.outlook_send_draft"] == "irreversible"
        assert risk_map["windows.take_screenshot"] == "read"
        assert risk_map["windows.click"] == "write"

        print(f"tools.mcp_client self-check PASSED in {time.time() - t0:.3f}s")
    finally:
        globals()["MCP_SERVERS_JSON"] = orig_json_path
        if orig_env is not None:
            os.environ["WINDOWS_MCP"] = orig_env
        else:
            os.environ.pop("WINDOWS_MCP", None)
        if tmp_json.exists():
            tmp_json.unlink()
