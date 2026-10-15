"""LOCAL SHIM (not part of the C2/A3 deliverable, never zipped): adapts the local reference backend in backend/
to the CONTRACTS.md interface so modules/kg and modules/agent/explore.py can run here. At integration the real
module from C1/A2/A1 replaces this file."""
from backend.tools import mcp_client as _m


def list_tools():
    _m.connect_all()
    return [{"name": t["qualified"].replace("mcp.", "", 1), "description": t["description"],
             "input_schema": t["input_schema"], "risk": t["risk"]} for t in _m.list_tools()]


def call(tool, args):
    name = tool if tool.startswith(("office.", "mcp.")) else "mcp." + tool
    return _m.call(name, args)
