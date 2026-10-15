"""Office COM MCP server (FastMCP, stdio). Tool names and risk follow CONTRACTS.md §5.

Run standalone:  python -m mcp_servers.office_com.server
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import functools  # noqa: E402

try:  # mcp 2.x renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP  # noqa: E402
    from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP  # noqa: E402
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402

from backend.tools import office  # noqa: E402

_server = FastMCP("office_com")


class _Tools:
    """mcp.tool(...) that turns any exception into a ToolError, so the client sees the real reason
    (e.g. "Excel unavailable (simulated)") instead of an opaque crash message."""

    def tool(self, **kw):
        def deco(fn):
            @functools.wraps(fn)
            def wrapped(*a, **k):
                try:
                    return fn(*a, **k)
                except ToolError:
                    raise
                except Exception as e:
                    raise ToolError(f"{type(e).__name__}: {e}") from e
            return _server.tool(**kw)(wrapped)
        return deco

    def run(self):
        _server.run()


mcp = _Tools()


@mcp.tool(description="Open a workbook and list its sheets. risk=read")
def excel_open(path: str, visible: bool = True) -> dict:
    return office.excel_open(path, visible)


@mcp.tool(description="Read a sheet range as rows and records. risk=read")
def excel_read_range(path: str, sheet: str, range: str = "A1:Z1000") -> dict:
    return office.excel_read_range(path, sheet, range)


@mcp.tool(description="Insert or update rows in a sheet, matched by key_column (no duplicates). risk=write")
def excel_upsert_rows(path: str, sheet: str, key_column: str, rows: list[dict]) -> dict:
    return office.excel_upsert_rows(path, sheet, key_column, rows)


@mcp.tool(description="Find one row by key. risk=read")
def excel_find_row(path: str, sheet: str, key_column: str, key: str) -> dict:
    return office.excel_find_row(path, sheet, key_column, key)


@mcp.tool(description="Write a Word document (.docx) from text; lines starting with # become headings. risk=write")
def word_write_doc(path: str, text: str) -> dict:
    return office.word_write_doc(path, text)


@mcp.tool(description="Create an email draft (saved as .eml when Outlook is absent). risk=write")
def outlook_create_draft(to: str, subject: str, body: str, drafts_dir: str | None = None) -> dict:
    return office.outlook_create_draft(to, subject, body, drafts_dir)


@mcp.tool(description="Send a previously created draft. risk=irreversible")
def outlook_send_draft(draft_id: str, drafts_dir: str | None = None) -> dict:
    return office.outlook_send_draft(draft_id, drafts_dir)


@mcp.tool(description="Create or edit a PowerPoint deck (visible): slides [{title, body?, notes?, index?}]; index edits that slide. risk=write")
def ppt_edit_slides(path: str, slides: list[dict], open_after: bool = True) -> dict:
    return office.ppt_edit_slides(path, slides, open_after)


@mcp.tool(description="Read slide titles and texts of a .pptx. risk=read")
def ppt_read(path: str) -> dict:
    return office.ppt_read(path)


@mcp.tool(description="Create or edit a Word document (visible): append_text and/or replacements [{find, replace}]. risk=write")
def word_edit(path: str, append_text: str | None = None, replacements: list[dict] | None = None, open_after: bool = True) -> dict:
    return office.word_edit(path, append_text, replacements, open_after)


@mcp.tool(description="Set Excel cells or formulas (visible): cells {A1: 5, B1: '=SUM(A1:A3)'}; returns computed values. risk=write")
def excel_set_cells(path: str, sheet: str, cells: dict, open_after: bool = True) -> dict:
    return office.excel_set_cells(path, sheet, cells, open_after)


@mcp.tool(description="Open a document in its default app so the user can see it. risk=read")
def office_open(path: str) -> dict:
    return office.office_open(path)


@mcp.tool(description="Simulate an Excel outage (every excel_* tool raises while on). risk=read")
def set_outage(on: bool) -> dict:
    return office.set_outage(on)


if __name__ == "__main__":
    mcp.run()
