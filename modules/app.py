"""FastAPI app + SSE (C1). CONTRACTS §8. Run: uv run uvicorn modules.app:app --port 8000"""
import asyncio
import json
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

load_dotenv()

from modules import llm  # noqa: E402
from modules.agent import loop, planner  # noqa: E402
from modules.ledger import db  # noqa: E402

@asynccontextmanager
async def lifespan(_app):
    from modules.channels import telegram

    bot = telegram.from_env()
    task = asyncio.create_task(bot.run()) if bot else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Autonomous AI Operator", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

MSG_LABELS = ["a task to perform", "a question about the project"]


class Name(BaseModel):
    name: str


class Title(BaseModel):
    title: str


class Text(BaseModel):
    text: str


class StepPatch(BaseModel):
    args: dict | None = None
    description: str | None = None


class Decision(BaseModel):
    decision: str
    edited_args: dict | None = None


class Accept(BaseModel):
    accept: bool


class Outage(BaseModel):
    on: bool


def is_question(text: str) -> bool:
    from modules.classify import classify

    try:
        label = classify(text, MSG_LABELS)["label"]
    except Exception:
        label = None
    if label is None:
        r = llm.complete([{"role": "user", "content": f"Is this message a task to perform or a question about the project? Message: {text}"}],
                         json_schema={"type": "object", "properties": {"label": {"enum": MSG_LABELS}}}, task="local")
        label = r.get("label")
    return label == MSG_LABELS[1]


@app.get("/projects")
def list_projects():
    return db.list_projects()


@app.post("/projects")
def create_project(body: Name):
    pid = db.create_project(body.name)
    db.init_db(pid)
    return {"id": pid}


@app.get("/projects/{pid}/threads")
def list_threads(pid: str):
    return db.list_threads(pid)


@app.post("/projects/{pid}/threads")
def create_thread(pid: str, body: Title):
    return {"id": db.create_thread(pid, body.title)}


@app.post("/projects/{pid}/threads/{tid}/messages")
def post_message(pid: str, tid: str, body: Text):
    if is_question(body.text):
        from modules.kg import rag

        q = loop.mask_text(None, body.text)
        return rag.ask(pid, q)
    key = "pending_" + tid
    goal = loop.mask_text(key, body.text)
    n_pii = len(loop._maps.get(key, {}))
    steps = planner.plan(pid, "", goal)  # create_run stamps the real run_id on each step
    run_id = db.create_run(pid, tid, goal, steps)
    loop._maps[run_id] = loop._maps.pop(key, {})
    run = db.get_run(pid, run_id)
    db.audit(pid, run_id, "pii", {"masked": n_pii})
    loop.emit(pid, "plan", {"run_id": run_id, "steps": run["steps"]}, run_id)
    loop.start(pid, run_id)
    return {"run_id": run_id}


@app.get("/projects/{pid}/runs/{rid}")
def get_run(pid: str, rid: str):
    return db.get_run(pid, rid)


@app.post("/projects/{pid}/runs/{rid}/{action}")
def control(pid: str, rid: str, action: str):
    if action == "resume":
        loop.start(pid, rid)
        return {"status": "running"}
    if action not in ("pause", "stop"):
        raise HTTPException(404, action)
    loop._control[rid] = action
    if rid not in loop._running:  # nothing executing: apply immediately
        return {"status": loop._set_run(pid, rid, "paused") if action == "pause" else loop.run(pid, rid)}
    return {"status": "pausing" if action == "pause" else "stopping"}


@app.patch("/projects/{pid}/runs/{rid}/steps/{sid}")
def patch_step(pid: str, rid: str, sid: str, body: StepPatch):
    step = loop.db_step(pid, rid, sid)
    if step["status"] != "pending":
        raise HTTPException(409, f"step is {step['status']}; only pending steps can be edited")
    fields = {}
    if body.description is not None:
        fields["description"] = body.description
    if body.args is not None:
        fields["args"] = loop.mask_obj(rid, body.args)
        fields["input_hash"] = planner.input_hash(step["tool"], fields["args"])
    return loop._set(pid, rid, sid, **fields) if fields else step


@app.post("/projects/{pid}/approvals/{aid}")
def approve(pid: str, aid: str, body: Decision):
    if body.decision not in ("approve", "edit", "reject"):
        raise HTTPException(400, "decision must be approve|edit|reject")
    try:
        loop.resolve_approval(pid, aid, body.decision, body.edited_args)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@app.post("/projects/{pid}/suggestions/{sug_id}")
def suggestion(pid: str, sug_id: str, body: Accept):
    if sug_id not in loop._suggestions:
        raise HTTPException(404, "unknown suggestion")
    loop.resolve_suggestion(pid, sug_id, body.accept)
    return {"ok": True}


@app.get("/projects/{pid}/graph")
def graph(pid: str):
    from modules.kg import store

    return store.graph_snapshot(pid)


@app.get("/projects/{pid}/audit")
def audit(pid: str, run_id: str | None = None):
    return db.list_audit(pid, run_id)


@app.get("/projects/{pid}/runs/{rid}/report")
def report(pid: str, rid: str):
    run = db.get_run(pid, rid)
    by = {k: [s for s in run["steps"] if s["status"] == k] for k in ("done", "reused", "degraded", "failed")}
    other = [s for s in run["steps"] if s["status"] not in by]
    events = db.list_audit(pid, rid)
    pii = sum(e["payload"].get("masked", 0) for e in events if e.get("kind") == "pii")
    regions = sum(e["payload"].get("regions", 0) for e in events if e.get("kind") == "vision_redact")
    if run["status"] == "done":
        summary = f"All {len(run['steps'])} steps completed and verified ({len(by['reused'])} reused from knowledge)."
    else:
        problems = [f"{s['step_id']} {s['status']}: {s.get('error') or s['description']}" for s in by["failed"] + by["degraded"] + other]
        summary = f"Not fully complete (status {run['status']}). Unresolved: " + "; ".join(problems)
    brief = lambda ss: [{"step_id": s["step_id"], "description": s["description"], "evidence": s.get("evidence"), "error": s.get("error")} for s in ss]
    return {"summary": summary, **{k: brief(v) for k, v in by.items()},
            "privacy": {"pii_masked": pii, "regions_blurred": regions, "raw_pii_to_llm": 0}}


@app.post("/settings/outage")
def outage(body: Outage):
    from modules.tools import mcp_client

    return mcp_client.call("office.set_outage", {"on": body.on})


@app.get("/projects/{pid}/events")
async def events(pid: str, request: Request):
    q: asyncio.Queue = asyncio.Queue()
    sub = (asyncio.get_running_loop(), q, pid)
    loop._subscribers.append(sub)

    async def stream():
        try:
            yield ": connected\n\n"
            while not await request.is_disconnected():
                try:
                    event, data = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            loop._subscribers.remove(sub)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    paths = {r.path for r in app.routes}
    for p in ["/projects", "/projects/{pid}/threads", "/projects/{pid}/threads/{tid}/messages", "/projects/{pid}/runs/{rid}",
              "/projects/{pid}/runs/{rid}/{action}", "/projects/{pid}/runs/{rid}/steps/{sid}", "/projects/{pid}/approvals/{aid}",
              "/projects/{pid}/suggestions/{sug_id}", "/projects/{pid}/graph", "/projects/{pid}/audit",
              "/projects/{pid}/runs/{rid}/report", "/settings/outage", "/projects/{pid}/events"]:
        assert p in paths, p
    print("app ok")
