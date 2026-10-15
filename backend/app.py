"""FastAPI app: HTTP API + SSE (CONTRACTS §8).  Run:  python -m uvicorn backend.app:app --port 8000"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import queue
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from backend import classify, config, llm
from backend.agent import events, loop
from backend.kg import rag, store
from backend.ledger import db
from backend.tools import mcp_client, mcp_registry, office


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(mcp_client.connect_all)
    if not db.list_projects():
        pid = db.create_project("My workspace")
        db.create_thread(pid, "Getting started")
    # recover runs that were mid-flight when the server stopped: leave them paused so the user can resume
    for p in db.list_projects():
        for r in db.list_runs(p["id"]):
            if r["status"] == "running" and not loop.is_active(r["run_id"]):
                db.set_run_status(p["id"], r["run_id"], "paused", "server restarted; press Resume to continue from the last finished step")
    yield


app = FastAPI(title="Autonomous AI Operator", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _pid(pid: str) -> str:
    if not db.project_exists(pid):
        raise HTTPException(404, f"project {pid} not found")
    return pid


# ---------------------------------------------------------------- projects / threads / messages
@app.get("/projects")
def projects():
    return [{"id": p["id"], "name": p["name"]} for p in db.list_projects()]


@app.post("/projects")
def new_project(body: dict = Body(...)):
    pid = db.create_project(str(body.get("name") or "Untitled project")[:80])
    return {"id": pid}


@app.get("/projects/{pid}/threads")
def threads(pid: str):
    return db.list_threads(_pid(pid))


@app.post("/projects/{pid}/threads")
def new_thread(pid: str, body: dict = Body(...)):
    return {"id": db.create_thread(_pid(pid), str(body.get("title") or "New thread")[:80])}


@app.get("/projects/{pid}/threads/{tid}/messages")
def messages(pid: str, tid: str):
    return db.list_messages(_pid(pid), tid)


@app.get("/projects/{pid}/threads/{tid}/runs")
def thread_runs(pid: str, tid: str):
    return db.list_runs(_pid(pid), tid)


@app.post("/projects/{pid}/threads/{tid}/messages")
def post_message(pid: str, tid: str, body: dict = Body(...)):
    _pid(pid)
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "empty message")
    mode = body.get("mode")  # optional explicit "task" | "question"
    if mode not in ("task", "question"):
        c = classify.classify(text, ["a task to perform", "a question about the project"])
        mode = "question" if c["label"] == "a question about the project" else "task"
    if len(db.list_messages(pid, tid)) == 0:
        db.touch_thread(pid, tid, title=text[:60])
    db.add_message(pid, tid, "user", mode, text)
    if mode == "question":
        out = rag.ask(pid, text)
        db.add_message(pid, tid, "assistant", "answer", out["answer"], {"sources": out["sources"]})
        return {"answer": db.mask_text(pid, out["answer"]), "sources": out["sources"]}
    rid = loop.start(pid, tid, text)
    db.add_message(pid, tid, "assistant", "run", "", {}, rid)
    return {"run_id": rid}


# ---------------------------------------------------------------- runs
@app.get("/projects/{pid}/runs/{rid}")
def get_run(pid: str, rid: str):
    run = db.get_run(_pid(pid), rid)
    if not run:
        raise HTTPException(404, "run not found")
    run["active"] = loop.is_active(rid)
    return run


@app.post("/projects/{pid}/runs/{rid}/{action}")
def run_action(pid: str, rid: str, action: str):
    _pid(pid)
    if action == "pause":
        return {"status": loop.pause(pid, rid)}
    if action == "resume":
        if office.outage():
            pass  # resume anyway: the failing step will retry and pause again honestly
        return {"status": loop.resume(pid, rid)}
    if action == "stop":
        return {"status": loop.stop(pid, rid)}
    raise HTTPException(404, f"unknown action {action}")


@app.patch("/projects/{pid}/runs/{rid}/steps/{sid}")
def patch_step(pid: str, rid: str, sid: str, body: dict = Body(...)):
    step = db.get_step(_pid(pid), rid, sid)
    if not step:
        raise HTTPException(404, "step not found")
    if step["status"] != "pending":
        raise HTTPException(409, "only pending steps can be edited")
    fields: dict[str, Any] = {}
    if "args" in body and isinstance(body["args"], dict):
        fields["args"] = body["args"]
        fields["input_hash"] = None
    if "description" in body:
        fields["description"] = str(body["description"])[:300]
    step = db.update_step(pid, rid, sid, **fields)
    db.audit(pid, rid, "step_edited", {"step_id": sid, "fields": list(fields)})
    events.publish(pid, "step_update", {"run_id": rid, "step": step})
    return step


@app.get("/projects/{pid}/runs/{rid}/report")
def run_report(pid: str, rid: str):
    return loop.report(_pid(pid), rid)


# ---------------------------------------------------------------- approvals / suggestions
@app.get("/projects/{pid}/approvals")
def approvals(pid: str, status: str | None = "pending"):
    out = []
    for a in db.list_approvals(_pid(pid), status):
        a["summary"] = db.unmask_value(pid, a["summary"])  # the user must see exactly what will happen
        out.append(a)
    return out


@app.post("/projects/{pid}/approvals/{aid}")
def resolve(pid: str, aid: str, body: dict = Body(...)):
    decision = body.get("decision")
    if decision not in ("approve", "edit", "reject"):
        raise HTTPException(400, "decision must be approve|edit|reject")
    try:
        return loop.approve(_pid(pid), aid, decision, body.get("edited_args"))
    except KeyError:
        raise HTTPException(404, "approval not found")


@app.get("/projects/{pid}/suggestions")
def suggestions(pid: str, run_id: str | None = None):
    return db.list_suggestions(_pid(pid), run_id)


@app.post("/projects/{pid}/suggestions/{sug_id}")
def suggestion(pid: str, sug_id: str, body: dict = Body(...)):
    try:
        return loop.accept_suggestion(_pid(pid), sug_id, bool(body.get("accept")))
    except KeyError:
        raise HTTPException(404, "suggestion not found")


# ---------------------------------------------------------------- knowledge / audit / files
@app.get("/projects/{pid}/graph")
def graph(pid: str):
    return store.graph_snapshot(_pid(pid))


@app.post("/projects/{pid}/ask")
def ask(pid: str, body: dict = Body(...)):
    out = rag.ask(_pid(pid), str(body.get("question", "")))
    return {"answer": db.mask_text(pid, out["answer"]), "sources": out["sources"]}


@app.get("/projects/{pid}/audit")
def audit(pid: str, run_id: str | None = None):
    return db.list_audit(_pid(pid), run_id)


@app.get("/projects/{pid}/file")
def get_file(pid: str, path: str):
    base = (db.project_dir(_pid(pid)) / "files").resolve()
    p = Path(path).resolve()
    if not str(p).startswith(str(base)) or not p.is_file():
        raise HTTPException(403, "only files inside the project folder can be served")
    return FileResponse(p, media_type=mimetypes.guess_type(str(p))[0] or "application/octet-stream", filename=p.name)


@app.post("/projects/{pid}/open")
def open_file(pid: str, body: dict = Body(...)):
    """Open an artifact with its default desktop app (Excel/Word/mail client)."""
    import os
    base = (db.project_dir(_pid(pid)) / "files").resolve()
    p = Path(str(body.get("path", ""))).resolve()
    if not str(p).startswith(str(base)) or not p.exists():
        raise HTTPException(403, "only project files can be opened")
    os.startfile(str(p))
    return {"ok": True}


# ---------------------------------------------------------------- tools / settings / status
@app.get("/tools")
def tools():
    from backend.tools import catalog
    used = set()
    for p in db.list_projects():
        with db.connect(p["id"]) as c:
            for r in c.execute("SELECT DISTINCT json_extract(data_json, '$.tool') AS t FROM steps"):
                used.add(r["t"])
    servers = mcp_client.status()
    installed = mcp_registry.scan_installed()
    return {"servers": servers, "mcp_tools": mcp_client.list_tools(), "installed": installed,
            "installed_unused": [s for s in installed if s["name"] not in servers],
            "catalog": [{"name": k, **{kk: v[kk] for kk in ("description", "risk", "route")}, "used": k in used}
                        for k, v in catalog.all_tools().items()]}


@app.get("/tools/registry")
def registry(need: str):
    return {"results": mcp_registry.search_registry(need, k=6), "best": mcp_registry.best_for(need)}


@app.post("/projects/{pid}/tools/install")
def request_install(pid: str, body: dict = Body(...)):
    cand = body.get("candidate")
    if not isinstance(cand, dict) or not cand.get("install"):
        raise HTTPException(400, "candidate with install spec required")
    aid = mcp_registry.request_install(_pid(pid), cand)
    ap = db.get_approval(pid, aid)
    events.publish(pid, "approval_required", {"approval_id": aid, "step_id": "", "run_id": "", "kind": "mcp_install",
                                              "summary": db.unmask_value(pid, ap["summary"])})
    return {"approval_id": aid}


@app.get("/settings/outage")
def get_outage():
    return {"outage": office.outage()}


@app.post("/settings/outage")
def set_outage(body: dict = Body(...)):
    on = bool(body.get("on"))
    out = office.set_outage(on)
    for p in db.list_projects():
        db.audit(p["id"], None, "outage_toggled", {"on": on})
        events.publish(p["id"], "log", {"run_id": None, "text": f"Excel outage simulation {'ON' if on else 'OFF'}"})
    if not on:
        out["resumed"] = loop.on_outage_cleared()
    return out


@app.get("/status")
def status():
    return {"llm": llm.status(), "mcp": mcp_client.status(), "outage": office.outage(),
            "protected_paths": config.PROTECTED_PATHS}


@app.post("/settings/llm/reset")
def llm_reset():
    llm.reset()
    return llm.status()


# ---------------------------------------------------------------- SSE
@app.get("/projects/{pid}/events")
async def sse(pid: str, request: Request):
    _pid(pid)
    q = events.subscribe(pid)

    async def gen():
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev, data = await asyncio.to_thread(q.get, True, 15)
                    yield f"event: {ev}\ndata: {json.dumps(data, default=str)}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            events.unsubscribe(pid, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
