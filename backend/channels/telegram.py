"""Telegram channel (ported from the team's modules/channels/telegram.py). Bot API over httpx long polling; started by
app.py when TELEGRAM_BOT_TOKEN is set. Only chats listed in TELEGRAM_ALLOWED can use it.

Goals/questions go through the same code as the web UI. Runs started here get their events relayed to the chat:
plan, step done/failed, approval buttons, clarifying questions, browser "your turn" prompts and the final report.
"""
from __future__ import annotations

import asyncio
import json
import os
import queue

import httpx

STEP_OK = ("done", "reused", "degraded")
HELP = ('Send me a goal (e.g. "find 5 CS internships and save them to Excel") or a question about the project.\n'
        "/stop stops the current run. Reply to my questions in plain text, or say \"your choice\".")


def _short(v, n: int = 300) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + "…"


def _approval_text(kind: str, step_id: str, s) -> str:
    """Human-readable approval request: exactly what will happen, no raw JSON."""
    if isinstance(s, str):
        return f"🔐 Approval needed (step {step_id})\n{s[:1500]}"
    title = {"file_write": "💾 Permission to write on your PC", "delete": "🗑️ Delete files (to Recycle Bin)",
             "command": "⌨️ Run a command", "mcp_install": "🧩 Install an MCP server"}.get(kind, "🔐 Approval needed")
    lines = [f"{title} — step {step_id}", f"What: {s.get('description', '')}".strip()]
    if kind == "file_write":
        lines.append(f"Action: {s.get('operation', 'write')}")
        for t in (s.get("targets") or [])[:5]:
            lines.append(f"📄 {t.get('path')}" + ("  (overwrites existing file)" if t.get("exists") else ""))
            if t.get("preview"):
                lines.append("   " + _short(t["preview"], 250).replace("\n", "\n   "))
    elif kind == "command":
        lines += [f"Command: {s.get('command')}", f"Shell: {s.get('shell')}  ·  Folder: {s.get('cwd')}"]
        reasons = (s.get("guard") or {}).get("reasons") or []
        if reasons:
            lines.append("Why it needs approval: " + "; ".join(map(str, reasons[:3])))
    elif kind == "delete":
        lines += [f"🗑️ {p}" for p in (s.get("paths") or [])[:8]] + ["(restorable from the Recycle Bin)"]
    elif kind == "mcp_install":
        lines += [f"Server: {s.get('server')}", f"Install: {s.get('install_command')}", s.get("repo") or ""]
    else:
        args = s.get("args")
        for a in (args if isinstance(args, list) else [args])[:4]:
            if isinstance(a, dict):
                lines += [f"• {k}: {_short(v, 200)}" for k, v in a.items() if v not in (None, "")][:6]
        if s.get("risk"):
            lines.append(f"Risk: {s['risk']}")
    return "\n".join(l for l in lines if l)[:3800]


class Bot:
    def __init__(self, token: str, allowed: set[int], project_id: str):
        self.url = f"https://api.telegram.org/bot{token}/"
        self.allowed, self.pid = allowed, project_id
        self.http = httpx.AsyncClient(timeout=40)
        self.thread_id: str | None = None
        self.runs: dict[str, int] = {}          # run_id -> chat id
        self.last_run: dict[int, str] = {}      # chat id -> latest run
        self.awaiting: dict[int, str] = {}      # chat id -> run waiting for clarification answers
        self.outbox: asyncio.Queue = asyncio.Queue()

    async def api(self, method: str, **params) -> dict:
        r = await self.http.post(self.url + method, json=params)
        return r.json().get("result")

    def say(self, chat: int, text: str, markup: dict | None = None) -> None:
        self.outbox.put_nowait((chat, text, markup))

    async def sender(self) -> None:
        """One sender, at most 1 message / 1.5 s; plain texts queued for the same chat are merged."""
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
                self.outbox.put_nowait(h)
            try:
                await self.api("sendMessage", chat_id=chat, text=text[:4000], **({"reply_markup": markup} if markup else {}))
            except Exception as e:
                print(f"[telegram] send failed: {e}")
            await asyncio.sleep(1.5)

    async def _thread(self) -> str:
        from backend.ledger import db
        if not self.thread_id:
            t = next((t for t in db.list_threads(self.pid) if t["title"] == "Telegram"), None)
            self.thread_id = t["id"] if t else db.create_thread(self.pid, "Telegram")
        return self.thread_id

    async def handle_update(self, u: dict) -> None:
        from backend import app
        from backend.agent import loop

        if cq := u.get("callback_query"):
            chat = cq["message"]["chat"]["id"]
            if chat not in self.allowed:
                return
            kind, _, aid = cq.get("data", "").partition(":")
            decision = {"a": "approve", "r": "reject"}.get(kind)
            try:
                await asyncio.to_thread(loop.approve, self.pid, aid, decision)
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
            r = await asyncio.to_thread(loop.stop, self.pid, rid)
            return self.say(chat, f"Stop requested ({r}).")
        if chat in self.awaiting:  # answers to clarifying questions: free text applies to all of them
            rid = self.awaiting.pop(chat)
            from backend.ledger import db
            qs = db.get_run_setting(self.pid, rid, "clarification", []) or []
            delegated = any(k in text.lower() for k in ("your choice", "you decide", "up to you", "anything"))
            answers = {q["id"]: (loop.AGENT_CHOICE if delegated else text) for q in qs}
            await asyncio.to_thread(loop.answer_clarification, self.pid, rid, answers)
            return self.say(chat, "Thanks, starting now.")
        try:
            tid = await self._thread()
            r = await asyncio.to_thread(app.post_message, self.pid, tid, {"text": text})
        except Exception as e:
            return self.say(chat, f"⚠️ {getattr(e, 'detail', e)}")
        if "run_id" in r:
            self.runs[r["run_id"]] = chat
            self.last_run[chat] = r["run_id"]
            self.say(chat, "On it. I'll post the plan and progress here. /stop to cancel.")
        else:
            self.say(chat, str(r.get("answer", r)))

    async def on_event(self, event: str, d: dict) -> None:
        from backend.agent import loop
        rid = d.get("run_id")
        chat = self.runs.get(rid)
        if chat is None:
            return
        if event == "assessment":
            a = d.get("assessment") or {}
            it, f = a.get("intent") or {}, a.get("feasibility") or {}
            icon = {"proceed": "🛡️ Safe & feasible", "proceed_partial": "⚠️ Partly feasible", "clarify": "❔ Not feasible as asked",
                    "refuse": "⛔ Refused"}.get(a.get("verdict"), "🛡️ Checked")
            lines = [f"{icon} · intent: {it.get('label', '?')} · feasibility: {f.get('level', '?')}"]
            lines += [f"✕ {u.get('need')} → {u.get('alternative', '')}" for u in (f.get("unsupported") or [])[:3]]
            if f.get("needs_approval"):
                lines.append("I'll ask you before: " + "; ".join(f["needs_approval"][:2]))
            self.say(chat, "\n".join(lines))
        elif event == "plan":
            self.say(chat, f"📋 Plan ({len(d['steps'])} steps):\n" +
                     "\n".join(f"{i}. {s['description']}  [{s.get('tool', '')}]" for i, s in enumerate(d["steps"], 1)))
        elif event == "step_update":
            s = d["step"]
            checks = ((s.get("evidence") or {}).get("checks") or [])
            proof = f"\n   ↳ {checks[0]}" if checks and checks[0] else ""
            if s["status"] in STEP_OK:
                tag = {"done": "✅", "reused": "♻️ reused from memory ·", "degraded": "⚠️ degraded ·"}[s["status"]]
                self.say(chat, f"{tag} {s['step_id']} {s['description']}{proof}")
            elif s["status"] == "failed":
                self.say(chat, f"❌ {s['step_id']} failed: {(s.get('error') or s['description'])[:200]}")
            elif s["status"] == "running" and s.get("tool") == "browser.agent":
                self.say(chat, "🌐 Working live in a new browser tab on your PC — watch it there.")
        elif event == "log" and "[browser] [YOUR TURN]" in str(d.get("text", "")):
            self.say(chat, "🔑 Your turn in the browser tab: " + str(d["text"]).split("[YOUR TURN]", 1)[1].strip()[:300] +
                     "\nType it on the PC (the agent can't see it), then click \"I'm done — continue\".")
        elif event == "clarification":
            self.awaiting[chat] = rid
            self.say(chat, "Before I start:\n" + "\n".join(f"• {q['question']} ({', '.join(q.get('options') or [])})" for q in d["questions"])
                     + '\nReply in one message, or say "your choice".')
        elif event == "approval_required":
            aid, summary = d["approval_id"], d["summary"]
            self.say(chat, _approval_text(d["kind"], d.get("step_id", ""), summary),
                     {"inline_keyboard": [[{"text": "✅ Approve", "callback_data": f"a:{aid}"}, {"text": "❌ Reject", "callback_data": f"r:{aid}"}]]})
        elif event == "browser_task":
            self.say(chat, "This step runs in your browser: open the Operator page on your PC to watch it (and log in if asked).")
        elif event == "run_status" and d["status"] in ("done", "failed", "stopped", "partial", "refused"):
            head = {"done": "🎉 Done", "partial": "🟡 Partly done", "failed": "🔴 Failed", "stopped": "⏹️ Stopped", "refused": "⛔ Refused"}[d["status"]]
            try:
                rep = await asyncio.to_thread(loop.report, self.pid, rid)
                files = sorted({str(a["path"]).replace("\\", "/").rsplit("/", 1)[-1] for a in rep.get("artifacts", [])})[:8]
                p = rep.get("privacy") or {}
                if rep.get("answer"):
                    self.say(chat, "✨ Here's what you asked for:\n" + rep["answer"].replace("**", "")[:3000])
                self.say(chat, f"{head}\n{rep['summary'][:1500]}" + (f"\n📎 Files: {', '.join(files)}" if files else "") +
                         f"\n🔒 {p.get('pii_masked', 0)} PII masked · 0 raw PII sent to the LLM")
            except Exception:
                self.say(chat, f"{head}: {d.get('error') or ''}")

    async def events(self) -> None:
        from backend.agent import events
        q = events.subscribe(self.pid)
        try:
            while True:
                try:
                    event, data = await asyncio.to_thread(q.get, True, 15)
                except queue.Empty:
                    continue
                try:
                    await self.on_event(event, data)
                except Exception as e:
                    print(f"[telegram] event {event}: {e}")
        finally:
            events.unsubscribe(self.pid, q)

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
    from backend import config
    from backend.ledger import db
    token = config.env("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    allowed = {int(x) for x in config.env("TELEGRAM_ALLOWED").replace(" ", "").split(",") if x}
    pid = config.env("TELEGRAM_PROJECT") or next((p["id"] for p in db.list_projects()), "")
    return Bot(token, allowed, pid) if pid else None


if __name__ == "__main__":
    calls: list = []

    async def main():
        bot = Bot("x", {42}, "p1")

        async def fake_api(method, **p):
            calls.append((method, p))
            return {}
        bot.api = fake_api
        await bot.handle_update({"message": {"chat": {"id": 99}, "text": "do evil"}})  # unknown chat: ignored
        assert bot.outbox.empty() and not calls
        await bot.handle_update({"message": {"chat": {"id": 42}, "text": "/help"}})
        assert not bot.outbox.empty()
        bot.runs["r1"] = 42
        await bot.on_event("plan", {"run_id": "r1", "steps": [{"description": "search"}, {"description": "save"}]})
        await bot.on_event("clarification", {"run_id": "r1", "questions": [{"id": "q1", "question": "Which date?", "options": ["Mon"]}]})
        assert bot.awaiting == {42: "r1"}
        texts = []
        while not bot.outbox.empty():
            texts.append(bot.outbox.get_nowait()[1])
        assert any("1. search" in t for t in texts) and any("Which date?" in t for t in texts), texts

    asyncio.run(main())
    print("telegram ok")
