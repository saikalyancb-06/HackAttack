"""MCP client: keeps one stdio session per configured server alive on a background asyncio loop.

Servers come from data/mcp_servers.json (ours: office_com, plus any the user approved installing).
list_tools() -> [{"server","name","qualified","description","input_schema","risk"}]
call(name, args) -> dict           name = "office.excel_upsert_rows" | "mcp.<server>.<tool>" | bare tool name
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import threading
from concurrent.futures import TimeoutError as FutTimeout
from typing import Any

from backend import config

SERVERS_FILE = config.DATA_DIR / "mcp_servers.json"
_DEFAULT = {"office_com": {"command": sys.executable, "args": ["-m", "mcp_servers.office_com.server"],
                           "cwd": str(config.ROOT), "builtin": True}}

_loop: asyncio.AbstractEventLoop | None = None
_sessions: dict[str, Any] = {}
_tools: dict[str, list[dict]] = {}
_status: dict[str, dict] = {}
_stops: dict[str, asyncio.Event] = {}
_lock = threading.Lock()


def servers() -> dict:
    data = dict(_DEFAULT)
    try:
        data.update(json.loads(SERVERS_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return data


def save_server(name: str, spec: dict) -> None:
    try:
        cur = json.loads(SERVERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        cur = {}
    cur[name] = spec
    SERVERS_FILE.write_text(json.dumps(cur, indent=2), encoding="utf-8")


def tool_risk(name: str, description: str = "") -> str:
    text = f"{name} {description}".lower()
    m = re.search(r"risk\s*=\s*(read|write|irreversible)", text)
    if m:
        return m.group(1)
    if re.search(r"\b(delete|remove|drop|send|submit|pay|purchase|transfer|destroy|kill|shutdown|publish|post)", text):
        return "irreversible"
    if re.search(r"^(get|list|read|search|find|fetch|query|describe|show|view)|_(get|list|read|search|find)", name.lower()):
        return "read"
    return "write"


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, name="mcp-loop", daemon=True).start()
    return _loop


async def _serve(name: str, spec: dict, ready: asyncio.Future) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import os
    params = StdioServerParameters(command=spec["command"], args=spec.get("args", []),
                                   env={**os.environ, **spec.get("env", {})}, cwd=spec.get("cwd"))
    stop = asyncio.Event()
    _stops[name] = stop
    try:
        log_dir = config.DATA_DIR / "logs"
        log_dir.mkdir(exist_ok=True)
        errlog = open(log_dir / f"mcp_{name}.log", "a", encoding="utf-8")
        async with stdio_client(params, errlog=errlog) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                listed = await session.list_tools()
                _tools[name] = [{"server": name, "name": t.name,
                                 "qualified": f"{'office' if name == 'office_com' else 'mcp.' + name}.{t.name}",
                                 "description": t.description or "", "input_schema": getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {},
                                 "risk": tool_risk(t.name, t.description or "")} for t in listed.tools]
                _sessions[name] = session
                _status[name] = {"connected": True, "tools": len(_tools[name]), "error": None}
                if not ready.done():
                    ready.set_result(True)
                await stop.wait()
    except BaseException as e:  # report, never crash the loop
        while isinstance(e, BaseExceptionGroup) and e.exceptions:
            e = e.exceptions[0]
        _status[name] = {"connected": False, "tools": 0, "error": f"{type(e).__name__}: {str(e)[:300]}"}
        if not ready.done():
            ready.set_result(False)
    finally:
        _sessions.pop(name, None)


def connect(name: str, timeout: float = 30) -> bool:
    if name in _sessions:
        return True
    spec = servers().get(name)
    if not spec:
        raise KeyError(f"unknown MCP server {name!r}")
    loop = _ensure_loop()

    async def start():
        ready = loop.create_future()
        loop.create_task(_serve(name, spec, ready))
        return await ready
    try:
        return bool(asyncio.run_coroutine_threadsafe(start(), loop).result(timeout))
    except FutTimeout:
        _status[name] = {"connected": False, "tools": 0, "error": "timed out connecting"}
        return False


def connect_all() -> dict:
    for name in servers():
        try:
            connect(name)
        except Exception as e:
            _status[name] = {"connected": False, "tools": 0, "error": str(e)[:200]}
    return status()


def disconnect(name: str) -> None:
    ev = _stops.get(name)
    if ev and _loop:
        _loop.call_soon_threadsafe(ev.set)


def reload() -> dict:
    for n in list(_sessions):
        disconnect(n)
    return connect_all()


def status() -> dict:
    return {n: _status.get(n, {"connected": False, "tools": 0, "error": None}) for n in servers()}


def list_tools() -> list[dict]:
    return [t for ts in _tools.values() for t in ts]


def _resolve(name: str) -> tuple[str, str]:
    if name.startswith("office."):
        return "office_com", name.split(".", 1)[1]
    if name.startswith("mcp."):
        _, server, tool = name.split(".", 2)
        return server, tool
    for t in list_tools():
        if t["name"] == name:
            return t["server"], t["name"]
    raise KeyError(f"no MCP tool named {name!r}")


def _decode(result) -> Any:
    sc = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if sc:
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    texts = [c.text for c in (result.content or []) if getattr(c, "type", "") == "text"]
    joined = "\n".join(texts)
    try:
        return json.loads(joined)
    except Exception:
        return {"text": joined}


def call(name: str, args: dict, timeout: float = 180) -> Any:
    server, tool = _resolve(name)
    if server not in _sessions and not connect(server):
        raise ConnectionError(f"MCP server {server} not connected: {_status.get(server, {}).get('error')}")
    session = _sessions[server]
    fut = asyncio.run_coroutine_threadsafe(session.call_tool(tool, args), _ensure_loop())
    try:
        result = fut.result(timeout)
    except FutTimeout:
        raise TimeoutError(f"MCP {server}.{tool} timed out after {timeout}s")
    if getattr(result, "is_error", False) or getattr(result, "isError", False):
        texts = [c.text for c in (result.content or []) if getattr(c, "type", "") == "text"]
        msg = " ".join(texts) or "tool error"
        msg = re.sub(r"^Error executing tool \w+: ", "", msg)
        msg = re.sub(r"^(RuntimeError|Exception): ", "", msg)
        raise RuntimeError(msg)
    return _decode(result)


if __name__ == "__main__":
    import os
    import tempfile
    from pathlib import Path
    os.environ["OPERATOR_SETTINGS"] = str(Path(tempfile.mkdtemp()) / "settings.json")  # private outage switch
    assert connect("office_com"), status()
    names = {t["name"] for t in list_tools()}
    assert {"excel_upsert_rows", "outlook_create_draft", "set_outage"} <= names, names
    assert {t["name"]: t["risk"] for t in list_tools()}["outlook_send_draft"] == "irreversible"
    p = str(Path(tempfile.mkdtemp()) / "m.xlsx")
    call("office.set_outage", {"on": False})
    out = call("office.excel_upsert_rows", {"path": p, "sheet": "S", "key_column": "k", "rows": [{"k": "a", "v": 1}]})
    assert out["inserted"] == 1, out
    call("office.set_outage", {"on": True})
    try:
        call("office.excel_find_row", {"path": p, "sheet": "S", "key_column": "k", "key": "a"})
        raise AssertionError("expected outage error")
    except RuntimeError as e:
        assert "simulated" in str(e), e
    call("office.set_outage", {"on": False})
    print("tools.mcp_client self-check OK", status())
