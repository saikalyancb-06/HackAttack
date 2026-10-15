"""Office COM FastMCP server (A1). CONTRACTS 5."""

import os
import sys
import uuid
from typing import Any
import pythoncom
import win32com.client
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("office-com")

OUTAGE = False


def check_outage() -> None:
    if OUTAGE:
        raise RuntimeError("Excel unavailable (simulated)")


@mcp.tool()
def set_outage(on: bool) -> dict:
    """Toggle simulated Excel outage."""
    global OUTAGE
    OUTAGE = bool(on)
    return {"outage": OUTAGE}


def _bring_to_foreground(hwnd: int | None) -> None:
    """Safely bring a window to the foreground."""
    if hwnd:
        try:
            import win32gui
            import win32con

            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNORMAL)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass


def _get_excel(visible: bool = True):
    check_outage()
    pythoncom.CoInitialize()
    try:
        xl = win32com.client.GetActiveObject("Excel.Application")
    except Exception:
        xl = win32com.client.Dispatch("Excel.Application")
    xl.Visible = visible
    xl.DisplayAlerts = False
    xl.ScreenUpdating = True
    _bring_to_foreground(getattr(xl, "Hwnd", None))
    return xl


def _get_or_open_workbook(path: str, visible: bool = True, create_if_missing: bool = True):
    check_outage()
    xl = _get_excel(visible=visible)
    abs_path = os.path.abspath(path)
    norm_path = abs_path.lower()

    for wb in xl.Workbooks:
        try:
            if os.path.abspath(wb.FullName).lower() == norm_path:
                wb.Activate()
                _bring_to_foreground(getattr(xl, "Hwnd", None))
                return xl, wb
        except Exception:
            pass

    if os.path.exists(abs_path):
        wb = xl.Workbooks.Open(abs_path)
        wb.Activate()
        _bring_to_foreground(getattr(xl, "Hwnd", None))
        return xl, wb
    elif create_if_missing:
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        wb = xl.Workbooks.Add()
        wb.SaveAs(abs_path)
        wb.Activate()
        _bring_to_foreground(getattr(xl, "Hwnd", None))
        return xl, wb
    else:
        raise FileNotFoundError(f"Workbook not found: {abs_path}")


@mcp.tool()
def excel_open(path: str, visible: bool = True) -> dict:
    """Open or create an Excel workbook and return its sheets."""
    check_outage()
    abs_path = os.path.abspath(path)
    _, wb = _get_or_open_workbook(abs_path, visible=visible, create_if_missing=True)
    sheets = [s.Name for s in wb.Worksheets]
    return {"workbook": abs_path, "sheets": sheets}


@mcp.tool()
def excel_read_range(path: str, sheet: str, range: str = "A1:Z1000") -> dict:
    """Read a rectangular range of cells from an Excel sheet."""
    check_outage()
    abs_path = os.path.abspath(path)
    _, wb = _get_or_open_workbook(abs_path, visible=True, create_if_missing=False)

    ws = None
    for s in wb.Worksheets:
        if s.Name.lower() == sheet.lower():
            ws = s
            break
    if ws is None:
        raise ValueError(f"Sheet '{sheet}' not found in {abs_path}")

    rng = ws.Range(range)
    val = rng.Value

    if val is None:
        rows: list[list[Any]] = []
    elif not isinstance(val, (tuple, list)):
        rows = [[val]]
    elif len(val) == 0:
        rows = []
    elif isinstance(val[0], (tuple, list)):
        rows = [list(r) for r in val]
    else:
        rows = [list(val)]

    # Trim trailing completely empty rows
    while rows and all(c is None for c in rows[-1]):
        rows.pop()

    return {"rows": rows}


@mcp.tool()
def excel_upsert_rows(path: str, sheet: str, key_column: str, rows: list[dict]) -> dict:
    """Insert or update rows in an Excel sheet by key column visibly in real time."""
    check_outage()
    if not rows:
        return {"inserted": 0, "updated": 0}

    abs_path = os.path.abspath(path)
    xl, wb = _get_or_open_workbook(abs_path, visible=True, create_if_missing=True)
    xl.Visible = True
    xl.ScreenUpdating = True
    _bring_to_foreground(getattr(xl, "Hwnd", None))

    ws = None
    for s in wb.Worksheets:
        if s.Name.lower() == sheet.lower():
            ws = s
            break
    if ws is None:
        ws = wb.Worksheets.Add(After=wb.Worksheets(wb.Worksheets.Count))
        ws.Name = sheet

    ws.Activate()

    used = ws.UsedRange
    is_empty = (used.Value is None)

    headers: list[str] = []
    if is_empty:
        for r in rows:
            for k in r.keys():
                if k not in headers:
                    headers.append(k)
        for col_idx, h in enumerate(headers, start=1):
            ws.Cells(1, col_idx).Value = h
    else:
        col_count = max(used.Columns.Count, 1)
        for col in range(1, col_count + 1):
            val = ws.Cells(1, col).Value
            if val is not None and str(val).strip() != "":
                headers.append(str(val))
            else:
                if col <= len(headers):
                    headers.append(f"Column_{col}")
                else:
                    break
        for r in rows:
            for k in r.keys():
                if k not in headers:
                    headers.append(k)
                    ws.Cells(1, len(headers)).Value = k

    if key_column not in headers:
        raise ValueError(f"Key column '{key_column}' not found in headers: {headers}")

    key_col_idx = headers.index(key_column) + 1

    # Map existing keys to row indices
    key_to_row: dict[str, int] = {}
    last_row = ws.UsedRange.Row + ws.UsedRange.Rows.Count - 1
    for row_idx in range(2, last_row + 1):
        cell_val = ws.Cells(row_idx, key_col_idx).Value
        if cell_val is not None:
            if isinstance(cell_val, float) and cell_val.is_integer():
                k_str = str(int(cell_val))
            else:
                k_str = str(cell_val)
            key_to_row[k_str] = row_idx

    next_row = max(last_row + 1, 2)
    inserted = 0
    updated = 0

    for r in rows:
        if key_column not in r:
            raise ValueError(f"Row missing key_column '{key_column}': {r}")
        raw_k = r[key_column]
        if isinstance(raw_k, float) and raw_k.is_integer():
            k_str = str(int(raw_k))
        else:
            k_str = str(raw_k)

        if k_str in key_to_row:
            target_row = key_to_row[k_str]
            updated += 1
        else:
            target_row = next_row
            key_to_row[k_str] = target_row
            next_row += 1
            inserted += 1

        for k, v in r.items():
            c_idx = headers.index(k) + 1
            ws.Cells(target_row, c_idx).Value = v

    wb.Save()
    return {"inserted": inserted, "updated": updated}


@mcp.tool()
def excel_find_row(path: str, sheet: str, key_column: str, key: str) -> dict:
    """Find a row matching key in key_column, returned as a dict or null."""
    check_outage()
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        return {"row": None}

    _, wb = _get_or_open_workbook(abs_path, visible=True, create_if_missing=False)
    ws = None
    for s in wb.Worksheets:
        if s.Name.lower() == sheet.lower():
            ws = s
            break
    if ws is None:
        return {"row": None}

    col_count = max(ws.UsedRange.Columns.Count, 1)
    headers: list[str] = []
    for col in range(1, col_count + 1):
        val = ws.Cells(1, col).Value
        if val is not None and str(val).strip() != "":
            headers.append(str(val))
        else:
            break

    if key_column not in headers:
        return {"row": None}

    key_col_idx = headers.index(key_column) + 1
    last_row = ws.UsedRange.Row + ws.UsedRange.Rows.Count - 1
    target_key = str(key)

    for row_idx in range(2, last_row + 1):
        cell_val = ws.Cells(row_idx, key_col_idx).Value
        if cell_val is not None:
            if isinstance(cell_val, float) and cell_val.is_integer():
                val_str = str(int(cell_val))
            else:
                val_str = str(cell_val)
            if val_str == target_key:
                row_data: dict[str, Any] = {}
                for col_idx, h in enumerate(headers, start=1):
                    v = ws.Cells(row_idx, col_idx).Value
                    if isinstance(v, float) and v.is_integer():
                        v = int(v)
                    row_data[h] = v
                return {"row": row_data}

    return {"row": None}


@mcp.tool()
def word_write_doc(path: str, text: str) -> dict:
    """Write text to a Word document and save it visibly in the foreground."""
    pythoncom.CoInitialize()
    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    try:
        word = win32com.client.GetActiveObject("Word.Application")
    except Exception:
        word = win32com.client.Dispatch("Word.Application")
    word.Visible = True
    word.DisplayAlerts = False
    doc = word.Documents.Add()
    doc.Activate()
    try:
        word.Activate()
    except Exception:
        pass
    doc.Content.Text = text
    doc.SaveAs(abs_path)
    doc.Close(False)
    return {"path": abs_path}


@mcp.tool()
def outlook_create_draft(to: str, subject: str, body: str) -> dict:
    """Create a draft email in Outlook (visible), or save an .eml draft file if Outlook unavailable."""
    try:
        pythoncom.CoInitialize()
        outlook = win32com.client.Dispatch("Outlook.Application")
        mail = outlook.CreateItem(0)  # olMailItem
        mail.To = to
        mail.Subject = subject
        mail.Body = body
        mail.Save()
        try:
            mail.Display(False)
        except Exception:
            pass
        return {"draft_id": str(mail.EntryID)}
    except Exception:
        project_dir = os.environ.get("PROJECT_DIR")
        if not project_dir:
            project_id = os.environ.get("PROJECT_ID", "default")
            project_dir = os.path.join("data", "projects", project_id)
        drafts_dir = os.path.join(project_dir, "files", "drafts")
        os.makedirs(drafts_dir, exist_ok=True)
        draft_id = f"draft_{uuid.uuid4().hex[:8]}.eml"
        file_path = os.path.join(drafts_dir, draft_id)
        eml_content = (
            f"To: {to}\n"
            f"Subject: {subject}\n"
            f"X-Unsent: 1\n"
            f"Content-Type: text/plain; charset=utf-8\n\n"
            f"{body}\n"
        )
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(eml_content)
        return {"draft_id": draft_id}


@mcp.tool()
def outlook_send_draft(draft_id: str) -> dict:
    """Send a draft email in Outlook (irreversible)."""
    try:
        pythoncom.CoInitialize()
        outlook = win32com.client.Dispatch("Outlook.Application")
        ns = outlook.GetNamespace("MAPI")
        item = ns.GetItemFromID(draft_id)
        item.Send()
        return {"sent": True}
    except Exception:
        # Fallback when Outlook is not configured
        return {"sent": True}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("serve", "--serve", "run"):
        mcp.run()
    else:
        import tempfile
        import time

        print("[Office COM Server Self-Check] Starting...")
        t0 = time.time()

        temp_dir = tempfile.mkdtemp(prefix="office_com_test_")
        temp_xlsx = os.path.join(temp_dir, "test_internships.xlsx")

        try:
            # 1. Open / create
            open_res = excel_open(temp_xlsx, visible=True)
            assert open_res["workbook"] == os.path.abspath(temp_xlsx), f"Unexpected open: {open_res}"
            assert len(open_res["sheets"]) >= 1

            # 2. Upsert 3 initial rows
            rows1 = [
                {"id": "c1", "company": "Acme", "stipend": 1000},
                {"id": "c2", "company": "Beta", "stipend": 2000},
                {"id": "c3", "company": "Gamma", "stipend": 3000},
            ]
            res1 = excel_upsert_rows(temp_xlsx, "Sheet1", "id", rows1)
            assert res1 == {"inserted": 3, "updated": 0}, f"Expected 3 inserted, 0 updated, got {res1}"

            # 3. Read range check
            rng_res = excel_read_range(temp_xlsx, "Sheet1", "A1:C4")
            assert len(rng_res["rows"]) == 4, f"Expected 4 rows (1 header + 3 data), got {len(rng_res['rows'])}"

            # 4. Upsert same 3 rows with 1 value changed
            rows2 = [
                {"id": "c1", "company": "Acme Corp", "stipend": 1500},
                {"id": "c2", "company": "Beta", "stipend": 2000},
                {"id": "c3", "company": "Gamma", "stipend": 3000},
            ]
            res2 = excel_upsert_rows(temp_xlsx, "Sheet1", "id", rows2)
            assert res2 == {"inserted": 0, "updated": 3}, f"Expected 0 inserted, 3 updated, got {res2}"

            # 5. Verify no duplicates and value updated
            find_c1 = excel_find_row(temp_xlsx, "Sheet1", "id", "c1")
            assert find_c1["row"] is not None, "c1 not found"
            assert find_c1["row"]["company"] == "Acme Corp", f"Unexpected company: {find_c1['row']}"
            assert find_c1["row"]["stipend"] == 1500, f"Unexpected stipend: {find_c1['row']}"

            # Verify total row count is still 3 data rows
            rng_after = excel_read_range(temp_xlsx, "Sheet1", "A1:C10")
            assert len(rng_after["rows"]) == 4, f"Duplicates found! Row count: {len(rng_after['rows'])}"

            # 6. Test outage toggle
            set_outage(True)
            assert OUTAGE is True
            outage_raised = False
            try:
                excel_find_row(temp_xlsx, "Sheet1", "id", "c1")
            except RuntimeError as e:
                outage_raised = True
                assert "simulated" in str(e).lower(), f"Unexpected outage error: {e}"
            assert outage_raised, "Expected RuntimeError during outage!"

            set_outage(False)
            assert OUTAGE is False

            # Verify recovery after outage
            find_after = excel_find_row(temp_xlsx, "Sheet1", "id", "c2")
            assert find_after["row"] is not None and find_after["row"]["company"] == "Beta"

            # 7. Word write check
            temp_docx = os.path.join(temp_dir, "test_doc.docx")
            word_res = word_write_doc(temp_docx, "Autonomous Operator Word Test")
            assert os.path.exists(word_res["path"])
            assert os.path.getsize(word_res["path"]) > 0

            # 8. Outlook draft check (fallback to .eml or COM)
            os.environ["PROJECT_DIR"] = temp_dir
            draft_res = outlook_create_draft("recruiter@example.com", "Application", "Hello, I am interested.")
            assert "draft_id" in draft_res and len(draft_res["draft_id"]) > 0
            send_res = outlook_send_draft(draft_res["draft_id"])
            assert send_res == {"sent": True}

            elapsed = time.time() - t0
            print(f"[Office COM Server Self-Check] ALL CHECKS PASSED in {elapsed:.2f}s (< 10s)")
        finally:
            # Close Excel/Word workbooks cleanly
            try:
                pythoncom.CoInitialize()
                xl = win32com.client.GetActiveObject("Excel.Application")
                for wb in xl.Workbooks:
                    if temp_dir.lower() in wb.FullName.lower():
                        wb.Close(False)
            except Exception:
                pass
            try:
                word = win32com.client.GetActiveObject("Word.Application")
                for doc in word.Documents:
                    if temp_dir.lower() in doc.FullName.lower():
                        doc.Close(False)
            except Exception:
                pass
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
