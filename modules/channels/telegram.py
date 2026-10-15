"""Telegram channel (C1, CONTRACTS §11). Bot API over httpx long polling; started by app.py when TELEGRAM_BOT_TOKEN is set.

Goals/questions go through app.post_message (same as the HTTP API). Runs started here get their events
relayed to the chat: plan, merged step done/failed, approval buttons, final report.
"""
import asyncio
import json
import os

import httpx

STEP_OK = ("done", "reused", "degraded")
HELP = ('Send me a goal (e.g. "find 5 CS internships and save them to Excel") or a question about the project.\n'
        "/stop stops the current run.")


class Bot:
    def __init__(self, token: str, allowed: set[int], project_id: str):
        self.url = f"https://api.telegram.org/bot{token}/"
        self.allowed, self.pid = allowed, project_id
        self.http = httpx.AsyncClient(timeout=40)
        self.thread_id = None
        self.runs: dict[str, int] = {}      # run_id -> chat id
        self.last_run: dict[int, str] = {}  # chat id -> latest run
        self.pending_chat = None            # chat whose goal is being planned (the plan event arrives before run_id returns)
        self.outbox: asyncio.Queue = asyncio.Queue()

    async def api(self, method: str, **params) -> dict:
        r = await self.http.post(self.url + method, json=params)
        return r.json().get("result")

    # ---- outgoing: one sender, at most 1 message / 2 s; plain texts queued for the same chat are merged
    def say(self, chat: int, text: str, markup: dict | None = None) -> None:
        self.outbox.put_nowait((chat, text, markup))

    async def sender(self) -> None:
        while True:
            chat, text, markup = await self.outbox.get()
            held = []
            while not markup and not self.outbox.empty():
                c, t, m = self.outbox.get_nowait()
                if c == chat and not m:
                    text += "\n" + t
                else:
                    held.append((c, t, m))
            for h in held:
                self.outbox.put_nowait(h)  # ponytail: merging can reorder around buttons; fine for one owner chat
            try:
                await self.api("sendMessage", chat_id=chat, text=text[:4000], **({"reply_markup": markup} if markup else {}))
            except Exception as e:
                print(f"[telegram] send failed: {e}")
            await asyncio.sleep(2)

    # ---- incoming
    async def handle_update(self, u: dict) -> None:
        from modules import app

        if cq := u.get("callback_query"):
            chat = cq["message"]["chat"]["id"]
            if chat not in self.allowed:
                return
            kind, _, aid = cq.get("data", "").partition(":")
            decision = {"a": "approve", "r": "reject"}.get(kind)
            try:
                await asyncio.to_thread(app.approve, self.pid, aid, app.Decision(decision=decision))
                note = "✅ Approved" if decision == "approve" else "❌ Rejected"
            except Exception as e:
                note = f"⚠️ {getattr(e, 'detail', e)}"
            await self.api("answerCallbackQuery", callback_query_id=cq["id"], text=note[:200])
            await self.api("editMessageText", chat_id=chat, message_id=cq["message"]["message_id"],
                           text=cq["message"].get("text", "") + "\n\n" + note)
            return
        msg = u.get("message") or {}
        chat, text = msg.get("chat", {}).get("id"), (msg.get("text") or "").strip()
        if chat not in self.allowed or not text:
            return
        if text.startswith(("/start", "/help")):
            return self.say(chat, HELP)
        if text.startswith("/stop"):
            rid = self.last_run.get(chat)
            if not rid:
                return self.say(chat, "No run to stop.")
            r = await asyncio.to_thread(app.control, self.pid, rid, "stop")
            return self.say(chat, f"Stop requested ({r['status']}).")
        try:
            if not self.thread_id:
                t = next((t for t in await asyncio.to_thread(app.list_threads, self.pid) if t["title"] == "Telegram"), None)
                self.thread_id = t["id"] if t else (await asyncio.to_thread(app.create_thread, self.pid, app.Title(title="Telegram")))["id"]
            self.pending_chat = chat
            r = await asyncio.to_thread(app.post_message, self.pid, self.thread_id, app.Text(text=text))
        except Exception as e:
            return self.say(chat, f"⚠️ {getattr(e, 'detail', e)}")
        finally:
            self.pending_chat = None
        if "run_id" in r:
            self.runs[r["run_id"]] = chat
            self.last_run[chat] = r["run_id"]
        else:
            self.say(chat, str(r.get("answer", r)))

    # ---- events of runs started from Telegram
    async def on_event(self, event: str, d: dict) -> None:
        from modules import app
        from modules.agent import loop

        rid = d.get("run_id") or loop._approvals.get(d.get("approval_id"), {}).get("run_id")
        if event == "plan" and rid not in self.runs and self.pending_chat:
            # ponytail: a web-UI goal planned at the same moment could be claimed; tag runs by thread if that matters
            self.runs[rid] = self.pending_chat
            self.last_run[self.pending_chat] = rid
        chat = self.runs.get(rid)
        if chat is None:
            return
        if event == "plan":
            self.say(chat, "Plan:\n" + "\n".join(f"{i}. {s['description']}" for i, s in enumerate(d["steps"], 1)) + "\n/stop to cancel.")
        elif event == "step_update":
            s = d["step"]
            if s["status"] in STEP_OK:
                self.say(chat, f"✓ {s['step_id']} {s['description']}" + ("" if s["status"] == "done" else f" ({s['status']})"))
            elif s["status"] == "failed":
                self.say(chat, f"✗ {s['step_id']} failed: {(s.get('error') or s['description'])[:200]}")
        elif event == "approval_required":
            aid, summary = d["approval_id"], d["summary"]
            body = summary if isinstance(summary, str) else json.dumps(summary, indent=1, default=str)[:1500]
            self.say(chat, f"Approval needed ({d['kind']}, step {d['step_id']}):\n{body}",
                     {"inline_keyboard": [[{"text": "✅ Approve", "callback_data": f"a:{aid}"}, {"text": "❌ Reject", "callback_data": f"r:{aid}"}]]})
        elif event == "run_status" and d["status"] in ("done", "failed", "stopped"):
            try:
                self.say(chat, "Report: " + (await asyncio.to_thread(app.report, self.pid, rid))["summary"])
            except Exception:
                self.say(chat, f"Run {d['status']}.")

    async def events(self) -> None:
        from modules.agent import loop

        q: asyncio.Queue = asyncio.Queue()
        sub = (asyncio.get_running_loop(), q, self.pid)
        loop._subscribers.append(sub)
        try:
            while True:
                event, data = await q.get()
                try:
                    await self.on_event(event, data)
                except Exception as e:
                    print(f"[telegram] event {event}: {e}")
        finally:
            loop._subscribers.remove(sub)

    async def poll(self) -> None:
        offset = None
        while True:
            try:
                ups = await self.api("getUpdates", timeout=30, offset=offset, allowed_updates=["message", "callback_query"]) or []
            except Exception as e:
                print(f"[telegram] getUpdates: {e}")
                await asyncio.sleep(5)
                continue
            for u in ups:
                offset = u["update_id"] + 1
                asyncio.create_task(self.handle_update(u))

    async def run(self) -> None:
        try:
            await asyncio.gather(self.poll(), self.sender(), self.events())
        finally:
            await self.http.aclose()


def from_env() -> Bot | None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    allowed = {int(x) for x in os.getenv("TELEGRAM_ALLOWED", "").replace(" ", "").split(",") if x}
    return Bot(token, allowed, os.getenv("TELEGRAM_PROJECT", ""))


if __name__ == "__main__":
    from modules import app
    from modules.agent import loop

    calls, approved = [], []

    async def fake_api(method, **p):
        calls.append((method, p))
        return {}

    def fake_emit(pid, ev, data, rid=None):
        for a, q, p in list(loop._subscribers):
            if p == pid:
                a.call_soon_threadsafe(q.put_nowait, (ev, data))

    def fake_post(pid, tid, body):
        fake_emit(pid, "plan", {"run_id": "r1", "steps": [{"description": "search"}, {"description": "save"}]})
        return {"run_id": "r1"}

    app.post_message = fake_post
    app.list_threads = lambda pid: [{"id": "t1", "title": "Telegram"}]
    app.approve = lambda pid, aid, body: approved.append((aid, body.decision)) or {"ok": True}

    async def main():
        bot = Bot("x", {42}, "p1")
        bot.api = fake_api
        tasks = [asyncio.create_task(bot.sender()), asyncio.create_task(bot.events())]
        await asyncio.sleep(0)
        await bot.handle_update({"message": {"chat": {"id": 99}, "text": "do evil"}})  # unknown chat: ignored
        assert bot.runs == {} and bot.outbox.empty() and not calls
        await bot.handle_update({"message": {"chat": {"id": 42}, "text": "find internships"}})
        await asyncio.sleep(0.1)
        assert bot.runs == {"r1": 42} and bot.last_run[42] == "r1", bot.runs
        assert any(m == "sendMessage" and "1. search" in p["text"] for m, p in calls), calls
        await bot.handle_update({"callback_query": {"id": "c1", "data": "a:appr_xyz1",
                                                    "message": {"chat": {"id": 42}, "message_id": 7, "text": "Approval needed"}}})
        assert approved == [("appr_xyz1", "approve")], approved
        assert any(m == "editMessageText" and "Approved" in p["text"] for m, p in calls)
        await bot.handle_update({"callback_query": {"id": "c2", "data": "a:appr_other", "message": {"chat": {"id": 99}, "message_id": 8}}})
        assert len(approved) == 1  # unknown chat cannot approve
        for t in tasks:
            t.cancel()

    asyncio.run(main())
    print("telegram ok")
