"""The agent loop: assess -> plan -> (per step) KG-first reuse -> approval gate -> execute with retry -> verify -> record.

One Runner thread per active run. State lives in the ledger, so a run can be paused, stopped, resumed,
or recovered after a crash / outage from the first unfinished step, without redoing finished work.
"""
from __future__ import annotations

import json
import re
import threading
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any

from backend import config
from backend.agent import events, planner, suggester, verifier
from backend.guard import command_guard, intent, risk
from backend.kg import store
from backend.ledger import db
from backend.ledger.retry import NonRetryable, RetryExhausted, with_retry
from backend.tools import catalog

_runners: dict[str, "Runner"] = {}
_lock = threading.Lock()
# Only {{sN...}} and {{item...}} are references; any other {{Word}} is literal text (e.g. a template the LLM fills in).
_REF = re.compile(r"\{\{\s*(s\d+|item)((?:(?:\.|\[)[^}\s]*)*)\s*\}\}")
_FRESH = {"research.search": timedelta(hours=6), "research.fetch": timedelta(hours=24)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class WaitingForApproval(Exception):
    pass


class ToolUnavailable(Exception):
    pass


# ---------------------------------------------------------------- reference resolution
def _get_path(value: Any, path: str) -> Any:
    path = re.sub(r"\[\s*['\"]?([^\]'\"]+)['\"]?\s*\]", r".\1", path)  # results[0].url / row["Name"] -> dotted
    for part in [p for p in path.split(".") if p]:
        if isinstance(value, dict) and part not in value and isinstance(value.get("items"), list) and                 all(isinstance(i, dict) and "output" in i for i in value["items"]):
            # output of a foreach step: look inside each item's output and flatten one level
            vals = [_get_path(i["output"], part) for i in value["items"] if i.get("ok")]
            value = [x for v in vals for x in (v if isinstance(v, list) else [v])]
            continue
        if isinstance(value, dict):
            if part in value:
                value = value[part]
            else:
                lower = {str(k).lower(): v for k, v in value.items()}
                if part.lower() not in lower:
                    payload = {k: v for k, v in value.items() if k not in ("method", "source", "transport", "backend")}
                    if len(payload) == 1:  # e.g. {{s5.summary}} on a text step that returned {"text": ...}
                        value = next(iter(payload.values()))
                        continue
                    raise NonRetryable(f"reference field {part!r} not found (have {list(value)[:8]})")
                value = lower[part.lower()]
        elif isinstance(value, list) and part.isdigit():
            value = value[int(part)]
        elif isinstance(value, list):
            value = [_get_path(v, part) for v in value]
        else:
            raise NonRetryable(f"cannot read {part!r} from {type(value).__name__}")
    return value


def resolve(obj: Any, outputs: dict, item: Any = None) -> Any:
    if isinstance(obj, str):
        full = _REF.fullmatch(obj.strip())
        if full:
            return _lookup(full.group(1), full.group(2), outputs, item)

        def sub(m: re.Match) -> str:
            if m.group(1) == "item" and item is None:
                return m.group(0)  # template text for the LLM (e.g. "Dear {{item.Company}}"), not a reference
            v = _lookup(m.group(1), m.group(2), outputs, item)
            return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
        return _REF.sub(sub, obj)
    if isinstance(obj, dict):
        return {k: resolve(v, outputs, item) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve(v, outputs, item) for v in obj]
    return obj


def _lookup(name: str, path: str, outputs: dict, item: Any) -> Any:
    if name == "item":
        if item is None:
            raise NonRetryable("{{item}} used outside a foreach step")
        return _get_path(item, path)
    if name not in outputs:
        raise NonRetryable(f"step {name} has no output yet")
    return _get_path(outputs[name], path)


def _is_draft_text(v: Any) -> bool:
    """Outputs that are themselves generated emails (they would 'confirm' an address the LLM invented)."""
    return isinstance(v, dict) and ("emails" in v or "drafts" in v)


def _lift_instruction_refs(args: dict) -> dict:
    """'Summarize {{s2}}' would paste a whole tool output into the instruction; move such references into inputs
    (where they are trimmed to the token budget) and leave a pointer in the instruction text."""
    instr = args.get("instruction")
    if not isinstance(instr, str) or not _REF.search(instr):
        return args
    lifted: dict = {}

    def sub(m: re.Match) -> str:
        if m.group(1) == "item":
            return m.group(0)
        key = (m.group(1) + m.group(2)).replace("[", ".").replace("]", "").strip(".")
        lifted[key] = m.group(0)
        return f"[data: {key}]"
    new_instr = _REF.sub(sub, instr)
    if not lifted:
        return args
    inputs = args.get("inputs")
    merged = {"inputs": inputs} if inputs not in (None, "", {}, []) else {}
    merged.update(lifted)
    return {**args, "instruction": new_instr, "inputs": merged}


def _deps(step: dict) -> set[str]:
    raw = json.dumps({"a": step.get("args"), "f": step.get("foreach")})
    return {m.group(1) for m in _REF.finditer(raw) if m.group(1) != "item"}


def _hash(tool: str, args: dict) -> str:
    import hashlib
    return hashlib.sha1(f"{tool}|{json.dumps(args, sort_keys=True, default=str)}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------- arg coercion (common planner slips)
def _coerce(tool: str, args: dict) -> dict:
    a = dict(args)
    if tool == "office.excel_upsert_rows":
        rows = a.get("rows")
        if isinstance(rows, dict):
            lists = [v for v in rows.values() if isinstance(v, list)]
            rows = lists[0] if lists else [rows]
        if isinstance(rows, list):
            rows = [r if isinstance(r, dict) else {"Item": str(r)} for r in rows]
            rows = [{str(k): (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in r.items()} for r in rows]
        a["rows"] = rows or []
        a.setdefault("sheet", "Sheet1")
        kc = a.get("key_column")
        if rows and (not kc or not all(kc in r for r in rows)):
            common = [k for k in rows[0] if all(k in r and r[k] not in (None, "") for r in rows)]
            pref = [k for k in common if k.lower() in ("name", "title", "id", "key", "company", "organization")]
            a["key_column"] = (pref or common or ["Item"])[0]
        kc = a.get("key_column")
        if rows and len({str(r.get(kc, "")).strip().casefold() for r in rows}) < len(rows):
            # key values repeat (e.g. several flights by one airline): upserting would merge distinct rows.
            unique = [k for k in rows[0] if all(k in r for r in rows)
                      and len({str(r[k]).strip().casefold() for r in rows}) == len(rows)]
            if unique:
                a["key_column"] = unique[0]
            else:
                cols = [k for k in rows[0] if k != kc][:3]
                for r in rows:
                    r["Key"] = " | ".join(str(r.get(c, "")) for c in [kc] + cols)
                a["key_column"] = "Key"
        if not str(a.get("path", "")).lower().endswith((".xlsx", ".xlsm")):
            a["path"] = str(a.get("path") or "results").rsplit(".", 1)[0] + ".xlsx"
    if tool == "office.outlook_create_draft":
        for k in ("to", "subject", "body"):
            if isinstance(a.get(k), (list, dict)):
                a[k] = json.dumps(a[k]) if k != "to" else ", ".join(map(str, a[k]))
        to = str(a.get("to", "")).strip()
        # never draft to a guessed address: obvious placeholders become an explicit "fill me in" recipient
        if "@" not in to or re.search(r"(?i)@(company|example|domain|email|yourcompany|organization|org|test)\.(com|org|net)$|[<>{}\[\]]", to):
            from backend.agent.think import NO_RECIPIENT
            a["to"] = NO_RECIPIENT
    if tool == "office.word_write_doc":
        if isinstance(a.get("text"), dict):
            a["text"] = a["text"].get("text") or json.dumps(a["text"], indent=2, ensure_ascii=False)
        a.setdefault("path", "report.docx")
    if tool in ("files.write_text", "files.append_text") and not isinstance(a.get("text"), str):
        t = a.get("text")
        a["text"] = t.get("text") if isinstance(t, dict) and isinstance(t.get("text"), str) else json.dumps(t, indent=2, ensure_ascii=False)
    if tool == "files.write_csv" and isinstance(a.get("rows"), dict):
        lists = [v for v in a["rows"].values() if isinstance(v, list)]
        a["rows"] = lists[0] if lists else [a["rows"]]
    if tool == "research.search":
        a["query"] = str(a.get("query", ""))[:300]
    return a


# ---------------------------------------------------------------- the runner
class Runner(threading.Thread):
    def __init__(self, project_id: str, run_id: str, goal: str | None = None, fresh: bool = False):
        super().__init__(name=f"run-{run_id}", daemon=True)
        self.pid, self.rid, self.goal, self.fresh = project_id, run_id, goal, fresh
        self.pause_flag = threading.Event()
        self.stop_flag = threading.Event()
        self.failed_this_pass: set[str] = set()

    # -- events helpers
    def log(self, text: str) -> None:
        events.publish(self.pid, "log", {"run_id": self.rid, "text": db.mask_text(self.pid, text), "ts": _now()})

    def emit_step(self, step_id: str) -> dict:
        s = db.get_step(self.pid, self.rid, step_id)
        events.publish(self.pid, "step_update", {"run_id": self.rid, "step": s})
        return s

    def set_status(self, status: str, error: str | None = None) -> None:
        db.set_run_status(self.pid, self.rid, status, error)
        events.publish(self.pid, "run_status", {"run_id": self.rid, "status": status, "error": db.mask_text(self.pid, error) if error else None})

    # -- main
    def run(self) -> None:
        db.set_current_run(self.rid)
        try:
            if self.fresh:
                if not self._assess_and_plan():
                    return
            self._execute()
        except Exception as e:
            db.audit(self.pid, self.rid, "runner_crash", {"error": str(e)[:500], "trace": traceback.format_exc()[-1500:]})
            self.set_status("failed", f"internal error: {e}")
        finally:
            db.set_current_run(None)
            with _lock:
                if _runners.get(self.rid) is self:
                    _runners.pop(self.rid, None)

    def _assess_and_plan(self) -> bool:
        goal = self.goal or ""
        self.set_status("running")
        self.log("Checking intent and feasibility...")
        a = intent.assess(goal, project_id=self.pid)
        with db.connect(self.pid) as c:
            c.execute("UPDATE runs SET assessment_json=? WHERE run_id=?", (json.dumps(db.mask_value(self.pid, a)), self.rid))
        events.publish(self.pid, "assessment", {"run_id": self.rid, "assessment": db.mask_value(self.pid, a)})
        db.audit(self.pid, self.rid, "assessment", {"verdict": a["verdict"], "intent": a["intent"]["label"],
                                                    "category": a["intent"].get("category"), "feasibility": a["feasibility"]["level"]})
        run = db.get_run(self.pid, self.rid)
        if a["verdict"] == "refuse":
            self.set_status("refused", a["summary"])
            db.add_message(self.pid, run["thread_id"], "assistant", "refusal", a["summary"], {"assessment": a}, self.rid)
            return False
        if a["verdict"] == "clarify":
            self.set_status("needs_input", a["summary"])
            db.add_message(self.pid, run["thread_id"], "assistant", "clarify", a["summary"], {"assessment": a}, self.rid)
            return False

        from backend.agent import workflows
        wf = workflows.match(self.pid, goal)
        if wf:
            steps, source = wf["steps"], f"workflow:{wf['name']}"
            self.log(f"Reusing saved workflow '{wf['name']}' from the knowledge graph (similarity {wf['score']:.2f}).")
        else:
            self.log("Planning...")
            steps, source = planner.plan(goal, project_id=self.pid, assessment=a)
        check = intent.check_plan(steps)
        for issue in check["issues"]:
            for s in steps:
                if s["step_id"] == issue["step_id"] and "no tool" in issue["problem"]:
                    s["status"], s["error"] = "skipped", f"not feasible: {issue['problem']}"
        db.replace_plan(self.pid, self.rid, steps)
        run = db.get_run(self.pid, self.rid)
        events.publish(self.pid, "plan", {"run_id": self.rid, "steps": run["plan"], "source": source})
        db.audit(self.pid, self.rid, "plan", {"source": source, "steps": [f"{s['step_id']} {s['tool']}" for s in steps]})
        self.log(f"Plan ready: {len(steps)} steps ({source}).")
        task = store.upsert_node(self.pid, "Task", self.rid, {"label": goal[:80], "goal": goal, "run_id": self.rid})
        events.publish(self.pid, "kg_update", store.graph_snapshot(self.pid))
        return True

    def _outputs(self) -> dict:
        run = db.get_run(self.pid, self.rid)
        vault = db.vault(self.pid)
        from backend.guard import pii
        return {s["step_id"]: pii.unmask(s.get("output"), vault) for s in run["plan"] if s["status"] in ("done", "reused", "degraded")}

    def _execute(self) -> None:
        self.set_status("running")
        while True:
            if self.stop_flag.is_set():
                for s in db.get_run(self.pid, self.rid)["plan"]:
                    if s["status"] in ("pending", "running", "awaiting_approval", "failed"):
                        db.update_step(self.pid, self.rid, s["step_id"], status="skipped", error="run stopped by user")
                        self.emit_step(s["step_id"])
                self.set_status("stopped", "stopped by user")
                return self._finish(final=True)
            if self.pause_flag.is_set():
                self.set_status("paused", "paused by user")
                return
            run = db.get_run(self.pid, self.rid)
            plan = run["plan"]
            status_by_id = {s["step_id"]: s["status"] for s in plan}
            nxt = None
            for s in plan:
                if s["status"] in ("done", "reused", "skipped", "rejected", "degraded"):
                    continue
                if s["step_id"] in self.failed_this_pass:
                    continue
                blockers = [d for d in _deps(s) if status_by_id.get(d) not in ("done", "reused", "degraded")]
                unknown = [d for d in blockers if d not in status_by_id]
                if unknown:
                    err = f"bad arguments: references step(s) {', '.join(unknown)} that do not exist"
                    db.update_step(self.pid, self.rid, s["step_id"], status="failed", error=err, ended_at=_now())
                    self.emit_step(s["step_id"])
                    self.failed_this_pass.add(s["step_id"])
                    self._try_repair(s, err)
                    continue
                if blockers:
                    hard = [d for d in blockers if status_by_id.get(d) in ("failed", "rejected", "skipped") or d in self.failed_this_pass
                            or d not in status_by_id]
                    if hard and all(status_by_id.get(d) in ("rejected", "skipped") for d in hard):
                        db.update_step(self.pid, self.rid, s["step_id"], status="skipped",
                                       error=f"skipped: depends on {', '.join(hard)} which was {status_by_id.get(hard[0])}")
                        self.emit_step(s["step_id"])
                        continue
                    if hard:
                        if s["status"] != "pending" or s.get("error") != f"waiting on {', '.join(hard)}":
                            db.update_step(self.pid, self.rid, s["step_id"], status="pending", error=f"waiting on {', '.join(hard)}")
                            self.emit_step(s["step_id"])
                        self.failed_this_pass.add(s["step_id"])
                        continue
                    continue  # dependency still pending/awaiting approval
                if s["status"] == "awaiting_approval":
                    self.set_status("paused", "waiting for your approval")
                    return
                nxt = s
                break
            if nxt is None:
                return self._finish(final=True)
            try:
                self._run_step(nxt)
            except WaitingForApproval:
                self.set_status("paused", "waiting for your approval")
                return
            except ToolUnavailable as e:
                self.set_status("paused", f"{e}. Progress is saved; I'll resume from this step when it's back.")
                return
            except Exception as e:  # never let one step take the whole run down
                db.audit(self.pid, self.rid, "step_crash", {"step_id": nxt["step_id"], "error": str(e)[:400],
                                                            "trace": traceback.format_exc()[-1200:]})
                db.update_step(self.pid, self.rid, nxt["step_id"], status="failed", error=f"internal error: {e}", ended_at=_now())
                self.emit_step(nxt["step_id"])
                self.failed_this_pass.add(nxt["step_id"])

    # -- one step
    def _run_step(self, step: dict) -> None:
        sid, tool = step["step_id"], step["tool"]
        outputs = self._outputs()
        vault = db.vault(self.pid)
        from backend.guard import pii
        try:
            raw_args = pii.unmask(step.get("args") or {}, vault)
            if tool == "llm.generate":
                raw_args = _lift_instruction_refs(raw_args)
            items = None
            if step.get("foreach"):
                items = resolve(pii.unmask(step["foreach"], vault), outputs)
                if isinstance(items, dict):
                    lists = [v for v in items.values() if isinstance(v, list)]
                    items = lists[0] if lists else [items]
                if not isinstance(items, list):
                    items = [items]
                if not items:
                    raise NonRetryable("foreach list is empty: nothing to do")
                args_list = [_coerce(tool, resolve(raw_args, outputs, it)) for it in items]
            else:
                args_list = [_coerce(tool, resolve(raw_args, outputs))]
            if tool == "office.outlook_create_draft":
                self._check_recipients(args_list, outputs)
        except NonRetryable as e:
            db.update_step(self.pid, self.rid, sid, status="failed", error=f"bad arguments: {e}", ended_at=_now())
            self.emit_step(sid)
            self.failed_this_pass.add(sid)
            self._try_repair(step, f"bad arguments: {e}")
            return

        input_hash = _hash(tool, args_list if items is not None else args_list[0])
        step_risk = self._risk(tool, args_list)

        # KG-first: reuse a still-valid identical result from an earlier run
        if not step.get("approved") and self._try_reuse(step, input_hash, args_list):
            return

        # approval gate
        if not step.get("approved"):
            kind, needs, summary = self._approval_needed(step, tool, args_list, step_risk)
            if needs:
                aid = db.create_approval(self.pid, self.rid, sid, summary, kind=kind)
                db.update_step(self.pid, self.rid, sid, status="awaiting_approval", risk=step_risk, input_hash=input_hash,
                               approval_id=aid)
                self.emit_step(sid)
                events.publish(self.pid, "approval_required", {"approval_id": aid, "step_id": sid, "run_id": self.rid,
                                                               "kind": kind, "summary": summary})  # real values: user must see them
                db.audit(self.pid, self.rid, "approval_requested", {"step_id": sid, "kind": kind, "tool": tool})
                self.log(f"Waiting for approval: {step['description']}")
                raise WaitingForApproval()

        db.update_step(self.pid, self.rid, sid, status="running", started_at=_now(), risk=step_risk, input_hash=input_hash, error=None)
        self.emit_step(sid)
        self.log(f"{sid}: {step['description']}")

        done_items = {i["idx"]: i for i in ((step.get("output") or {}).get("items") or []) if i.get("ok")} if items is not None else {}
        results, errors, evidence_checks = [], [], []
        attempts_total = step.get("attempts", 0)
        for idx, args in enumerate(args_list):
            if idx in done_items:  # resumed foreach: never redo a finished item (no duplicate drafts)
                results.append(done_items[idx])
                continue

            def call(args=args):
                return catalog.execute(self.pid, self.rid, tool, args, step)

            def on_error(n: int, e: BaseException) -> None:
                nonlocal attempts_total
                attempts_total += 1
                db.update_step(self.pid, self.rid, sid, attempts=attempts_total, error=f"attempt {n}: {e}")
                self.emit_step(sid)
                self.log(f"{sid} attempt {n} failed: {e}")
                db.audit(self.pid, self.rid, "tool_error", {"step_id": sid, "tool": tool, "attempt": n, "error": str(e)[:300]})

            try:
                tries = 2 if tool in ("research.fetch", "browser.snapshot") else 3  # a slow page rarely recovers
                out = with_retry(call, attempts=tries, backoff=(1, 2, 4), on_error=on_error, should_stop=self.stop_flag.is_set)
                attempts_total += 1
                ver = verifier.verify(self.pid, step, args, out if isinstance(out, dict) else {"value": out})
                evidence_checks += ver["checks"]
                if not ver["ok"]:
                    raise RuntimeError("verification failed: " + "; ".join(ver["checks"]))
                db.audit(self.pid, self.rid, "tool_call", {"step_id": sid, "tool": tool, "args": _short(args), "ok": True,
                                                           "verify": ver["checks"]})
                results.append({"idx": idx, "ok": True, "output": out})
            except (RetryExhausted, RuntimeError, NonRetryable, Exception) as e:
                err = e.last_error if isinstance(e, RetryExhausted) else e
                errors.append(f"{type(err).__name__}: {err}")
                results.append({"idx": idx, "ok": False, "error": str(err)[:300]})
                if items is not None:
                    db.update_step(self.pid, self.rid, sid, output={"items": results})
                if _is_unavailable(err):
                    db.update_step(self.pid, self.rid, sid, status="failed", attempts=attempts_total, ended_at=_now(),
                                   error=f"{err} (after {attempts_total} attempts)", output={"items": results} if items is not None else None)
                    self.emit_step(sid)
                    suggester.after_failure(self.pid, self.rid, {**step, "args": args_list[0] if items is None else step["args"]}, str(err))
                    _register_waiter(self.pid, self.rid)
                    raise ToolUnavailable(str(err))

        ok_items = [r for r in results if r.get("ok")]
        if items is None:
            if ok_items:
                output = ok_items[0]["output"]
                self._complete(step, "done", output, input_hash, attempts_total, evidence_checks, args_list[0])
            else:
                status = "degraded" if tool in ("research.fetch", "browser.snapshot") else "failed"
                db.update_step(self.pid, self.rid, sid, status=status, attempts=attempts_total, ended_at=_now(),
                               error="; ".join(errors)[:500], evidence={"checks": evidence_checks},
                               output={"error": errors[0][:300]} if status == "degraded" else None)
                self.emit_step(sid)
                if status == "failed":
                    self.failed_this_pass.add(sid)
                    suggester.after_failure(self.pid, self.rid, step, "; ".join(errors))
                    self._try_repair(step, "; ".join(errors))
        else:
            output = {"items": results, "count": len(ok_items), "total": len(results)}
            if len(ok_items) == len(results):
                self._complete(step, "done", output, input_hash, attempts_total, evidence_checks, args_list)
            elif ok_items:
                self._complete(step, "degraded", output, input_hash, attempts_total, evidence_checks, args_list,
                               error=f"{len(results) - len(ok_items)} of {len(results)} items failed: {errors[0][:200]}")
            else:
                db.update_step(self.pid, self.rid, sid, status="failed", attempts=attempts_total, ended_at=_now(),
                               error="; ".join(errors)[:500], output=output)
                self.emit_step(sid)
                self.failed_this_pass.add(sid)
                self._try_repair(step, "; ".join(errors))

    def _check_recipients(self, args_list: list[dict], outputs: dict) -> None:
        """A draft may only go to an address that came from the user's goal or from collected data (not one the
        LLM generated itself). Anything else becomes an explicit placeholder for the human to fill in."""
        from backend.agent.think import NO_RECIPIENT
        run = db.get_run(self.pid, self.rid)
        corpus = (db.unmask_value(self.pid, run["goal_masked"]) + json.dumps(
            {k: v for k, v in outputs.items() if not _is_draft_text(v)}, ensure_ascii=False, default=str)).lower()
        for a in args_list:
            to = str(a.get("to", "")).strip().lower()
            if to and to != NO_RECIPIENT and to not in corpus:
                a["note"] = f"recipient {to!r} was not found in the collected data; replaced with a placeholder"
                a["to"] = NO_RECIPIENT
            a.pop("note", None)

    def _try_repair(self, step: dict, error: str) -> bool:
        """Self-healing: ask the planner for a different approach to the failed step and everything after it."""
        from backend import llm
        repairs = sum(1 for a in db.list_audit(self.pid, self.rid) if a["kind"] == "replan")
        if repairs >= 2 or not llm.available() or self.stop_flag.is_set():
            return False
        run = db.get_run(self.pid, self.rid)
        goal = db.unmask_value(self.pid, run["goal_masked"])
        self.log(f"{step['step_id']} failed ({error[:120]}). Re-planning the remaining work...")
        try:
            new = planner.repair(goal, db.unmask_value(self.pid, run["plan"]), step, error, project_id=self.pid)
        except Exception as e:
            db.audit(self.pid, self.rid, "replan_error", {"step_id": step["step_id"], "error": str(e)[:300]})
            new = []
        if not new:
            self.log("No alternative approach found; leaving the step failed.")
            return False
        ids = [s["step_id"] for s in run["plan"]]
        pos = ids.index(step["step_id"])
        superseded = []
        for s in run["plan"][pos:]:
            if s["status"] in ("pending", "failed"):
                db.update_step(self.pid, self.rid, s["step_id"], status="skipped",
                               error=f"superseded by repaired plan (repair {repairs + 1})")
                superseded.append(s["step_id"])
                self.failed_this_pass.discard(s["step_id"])
        db.insert_steps_after(self.pid, self.rid, ids[-1], new)
        db.audit(self.pid, self.rid, "replan", {"failed_step": step["step_id"], "error": error[:300], "superseded": superseded,
                                                "new_steps": [f"{n['step_id']} {n['tool']}" for n in new]})
        run = db.get_run(self.pid, self.rid)
        events.publish(self.pid, "plan", {"run_id": self.rid, "steps": run["plan"], "source": "repair"})
        self.log(f"Repaired plan: replaced {', '.join(superseded)} with {len(new)} new steps.")
        return True

    def _risk(self, tool: str, args_list: list[dict]) -> str:
        if tool == "shell.run_command":
            levels = [command_guard.classify_rules(str(a.get("cmd", "")))[0] for a in args_list]
            return "irreversible" if "irreversible" in levels else ("write" if "write" in levels else "read")
        return catalog.tool_risk(tool)

    def _approval_needed(self, step: dict, tool: str, args_list: list[dict], step_risk: str) -> tuple[str, bool, dict]:
        run = db.get_run(self.pid, self.rid)
        unclear = (run.get("assessment") or {}).get("intent", {}).get("label") == "unclear"
        # show exactly what will happen: absolute paths, not project-relative ones
        args_list = [catalog.resolve_paths(self.pid, a) for a in args_list]
        summary: dict = {"description": step["description"], "tool": tool, "risk": step_risk,
                         "args": [_short(a, 4000) for a in args_list] if len(args_list) > 1 else _short(args_list[0], 4000)}
        if tool == "shell.run_command":
            a = args_list[0]
            g = command_guard.check(str(a.get("cmd", "")), context=run.get("goal_masked", ""))
            summary.update({"command": a.get("cmd"), "shell": a.get("shell", "powershell"),
                            "cwd": a.get("cwd") or str(db.project_dir(self.pid) / "files"), "guard": g})
            return "command", command_guard.needs_approval(g) or unclear, summary
        if tool == "files.delete":
            summary["paths"] = [a.get("path") for a in args_list]
            return "delete", True, summary
        if tool == "windows.explore":
            return "action", unclear, summary
        needs = any(risk.needs_approval({"risk": step_risk, "args": a}, config.PROTECTED_PATHS) for a in args_list)
        if unclear and step_risk != "read":
            needs = True
            summary["reason"] = "the safety check could not confirm this request, so every change needs your OK"
        if tool == "office.outlook_send_draft":
            summary["recipients"] = [a.get("draft_id") for a in args_list]
        return "action", needs, summary

    def _try_reuse(self, step: dict, input_hash: str, args_list: list[dict]) -> bool:
        tool = step["tool"]
        if catalog.tool_risk(tool) != "read" and tool not in ("office.excel_upsert_rows", "office.word_write_doc"):
            return False
        prev = db.find_done_by_hash(self.pid, input_hash)
        if not prev or prev.get("run_id") == self.rid:
            return False
        fresh = _FRESH.get(tool)
        if fresh and prev.get("ended_at"):
            try:
                if datetime.fromisoformat(prev["ended_at"]) < datetime.now(timezone.utc) - fresh:
                    return False
            except ValueError:
                return False
        if catalog.tool_risk(tool) != "read":  # a write is only "already done" if the artifact still checks out
            ver = verifier.verify(self.pid, step, args_list[0], prev.get("output") or {})
            if not ver["ok"]:
                return False
        from backend.guard import pii
        output = pii.unmask(prev.get("output"), db.vault(self.pid))
        db.update_step(self.pid, self.rid, step["step_id"], status="reused", output=output, input_hash=input_hash,
                       started_at=_now(), ended_at=_now(),
                       evidence={"checks": [f"reused result of {prev['run_id']}/{prev['step_id']} from the knowledge graph"],
                                 "reused_from": {"run_id": prev["run_id"], "step_id": prev["step_id"]}})
        self.emit_step(step["step_id"])
        db.audit(self.pid, self.rid, "kg_reuse", {"step_id": step["step_id"], "from": f"{prev['run_id']}/{prev['step_id']}"})
        self.log(f"{step['step_id']}: reused a still-valid result from an earlier run (no repeat work).")
        return True

    def _complete(self, step: dict, status: str, output: Any, input_hash: str, attempts: int, checks: list[str],
                  args: Any, error: str | None = None) -> None:
        sid = step["step_id"]
        ev = {"checks": checks[:12]}
        if isinstance(output, dict):
            for k in ("transport", "backend", "screenshot", "regions_blurred", "path", "source"):
                if k in output:
                    ev[k] = output[k]
        db.update_step(self.pid, self.rid, sid, status=status, output=_trim(output), input_hash=input_hash, attempts=attempts,
                       ended_at=_now(), evidence=ev, error=error)
        self.emit_step(sid)
        for sug in db.list_suggestions(self.pid, self.rid):  # a workaround offered for this step is moot now
            if sug["status"] == "open" and sug["patch"].get("before") == sid:
                db.set_suggestion_status(self.pid, sug["sug_id"], "expired")
                events.publish(self.pid, "suggestion", {**sug, "status": "expired"})
        try:
            self._record_kg(step, output, args)
        except Exception as e:
            db.audit(self.pid, self.rid, "kg_error", {"step_id": sid, "error": str(e)[:300]})
        try:
            suggester.after_step(self.pid, self.rid, step, output if isinstance(output, dict) else None)
        except Exception:
            pass

    def _record_kg(self, step: dict, output: Any, args: Any) -> None:
        task_id = store.upsert_node(self.pid, "Task", self.rid, {"run_id": self.rid})
        st = store.upsert_node(self.pid, "Step", f"{self.rid}:{step['step_id']}",
                               {"label": step["description"][:60], "tool": step["tool"], "run_id": self.rid, "step_id": step["step_id"]},
                               source_step=step["step_id"])
        store.add_edge(self.pid, st, task_id, "PART_OF")
        prod = step.get("produces")
        if prod and prod.get("key"):
            valid = None
            if prod.get("type") == "dataset":
                valid = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat(timespec="seconds")
            props = {"label": f"{prod.get('type', 'artifact')}: {prod['key']}"[:60], "kind": prod.get("type"),
                     "run_id": self.rid, "step_id": step["step_id"], "tool": step["tool"]}
            if isinstance(output, dict):
                for k in ("path", "inserted", "updated", "count"):
                    if k in output:
                        props[k] = output[k]
                if step["tool"] == "office.excel_upsert_rows" and isinstance(args, dict):
                    props["path"] = args.get("path")
                    props["rows"] = [{k: v for k, v in r.items()} for r in (args.get("rows") or [])][:20]
                if "items" in output:
                    props["paths"] = [i.get("output", {}).get("path") for i in output["items"] if i.get("ok")][:20]
            art = store.upsert_node(self.pid, "Artifact", prod["key"], props, valid_until=valid, source_step=step["step_id"])
            store.add_edge(self.pid, st, art, "PRODUCED")
            store.add_edge(self.pid, task_id, art, "PRODUCED")
            if step["tool"] == "office.excel_upsert_rows" and isinstance(args, dict):
                kc = args.get("key_column")
                for r in (args.get("rows") or [])[:20]:
                    ent = store.upsert_node(self.pid, "Entity", str(r.get(kc)), {"label": str(r.get(kc))[:60], **r})
                    store.add_edge(self.pid, art, ent, "ABOUT")
        if step["tool"] == "research.fetch" and isinstance(output, dict) and output.get("url"):
            f = store.upsert_node(self.pid, "Fact", output["url"], {"label": (output.get("title") or output["url"])[:60],
                                                                    "url": output["url"], "excerpt": output.get("text", "")[:1500]},
                                  valid_until=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds"))
            store.add_edge(self.pid, st, f, "USED")
        events.publish(self.pid, "kg_update", store.graph_snapshot(self.pid))

    def _finish(self, final: bool) -> None:
        run = db.get_run(self.pid, self.rid)
        if run["status"] in ("stopped",):
            pass
        statuses = [s["status"] for s in run["plan"]]
        if "awaiting_approval" in statuses and run["status"] != "stopped":
            return
        if run["status"] != "stopped":
            if all(s in ("done", "reused", "skipped") for s in statuses) and not any(
                    s["status"] == "skipped" and str(s.get("error", "")).startswith("not feasible") for s in run["plan"]):
                status = "done"
            elif any(s in ("done", "reused", "degraded") for s in statuses):
                status = "partial"
            else:
                status = "failed"
            failed = [s for s in run["plan"] if s["status"] in ("failed", "pending")]
            self.set_status(status, ("Unresolved: " + "; ".join(f"{s['step_id']} {s.get('error') or s['status']}" for s in failed))[:600]
                            if failed else None)
        run = db.get_run(self.pid, self.rid)
        rep = report(self.pid, self.rid)
        db.add_message(self.pid, run["thread_id"], "assistant", "report", rep["summary"], {"report": rep}, self.rid)
        events.publish(self.pid, "report", {"run_id": self.rid, "report": rep})
        store.upsert_node(self.pid, "Task", self.rid, {"status": run["status"], "summary": rep["summary"][:500]})
        events.publish(self.pid, "kg_update", store.graph_snapshot(self.pid))
        try:
            suggester.after_run(self.pid, self.rid, run)
        except Exception:
            pass


def _is_unavailable(e: BaseException) -> bool:
    m = str(e).lower()
    return any(w in m for w in ("unavailable", "simulated", "not connected", "connection refused", "rpc server", "timed out connecting"))


def _short(args: Any, limit: int = 1500) -> Any:
    s = json.dumps(args, ensure_ascii=False, default=str)
    if len(s) <= limit:
        return args
    return {"_truncated": s[:limit] + "..."}


def _trim(output: Any, limit: int = 60000) -> Any:
    s = json.dumps(output, ensure_ascii=False, default=str)
    if len(s) <= limit:
        return output
    if isinstance(output, dict) and "text" in output:
        return {**output, "text": str(output["text"])[: max(1000, limit - (len(s) - len(str(output["text"]))))]}
    return {"_truncated": s[:limit]}


# ---------------------------------------------------------------- outage auto-resume
_waiters: set[tuple[str, str]] = set()


def _register_waiter(pid: str, rid: str) -> None:
    with _lock:
        _waiters.add((pid, rid))


def on_outage_cleared() -> list[str]:
    with _lock:
        ws = list(_waiters)
        _waiters.clear()
    resumed = []
    for pid, rid in ws:
        run = db.get_run(pid, rid)
        if run and run["status"] == "paused":
            for s in run["plan"]:
                if s["status"] == "failed":
                    db.update_step(pid, rid, s["step_id"], status="pending", error="retrying: tool is available again")
            resume(pid, rid)
            resumed.append(rid)
    return resumed


# ---------------------------------------------------------------- public API used by app.py
def start(project_id: str, thread_id: str, goal: str) -> str:
    rid = db.create_run(project_id, thread_id, db.mask_text(project_id, goal), [])
    r = Runner(project_id, rid, goal=goal, fresh=True)
    with _lock:
        _runners[rid] = r
    r.start()
    return rid


def resume(project_id: str, run_id: str) -> str:
    with _lock:
        cur = _runners.get(run_id)
        if cur and cur.is_alive():
            cur.pause_flag.clear()
            return "running"
        run = db.get_run(project_id, run_id)
        if not run:
            raise KeyError(run_id)
        for s in run["plan"]:
            if s["status"] == "running":  # crashed mid-step: run it again (tools are idempotent upserts / drafts by item)
                db.update_step(project_id, run_id, s["step_id"], status="pending")
            if s["status"] == "failed":
                db.update_step(project_id, run_id, s["step_id"], status="pending", error="retrying after resume")
            if s["status"] == "pending" and str(s.get("error", "")).startswith("waiting on"):
                db.update_step(project_id, run_id, s["step_id"], error=None)
        r = Runner(project_id, run_id)
        _runners[run_id] = r
    r.start()
    return "running"


def pause(project_id: str, run_id: str) -> str:
    r = _runners.get(run_id)
    if r and r.is_alive():
        r.pause_flag.set()
        return "pausing"
    db.set_run_status(project_id, run_id, "paused", "paused by user")
    events.publish(project_id, "run_status", {"run_id": run_id, "status": "paused"})
    return "paused"


def stop(project_id: str, run_id: str) -> str:
    r = _runners.get(run_id)
    if r and r.is_alive():
        r.stop_flag.set()
        return "stopping"
    run = db.get_run(project_id, run_id)
    for s in run["plan"]:
        if s["status"] in ("pending", "awaiting_approval", "failed", "running"):
            db.update_step(project_id, run_id, s["step_id"], status="skipped", error="run stopped by user")
    db.set_run_status(project_id, run_id, "stopped", "stopped by user")
    events.publish(project_id, "run_status", {"run_id": run_id, "status": "stopped"})
    return "stopped"


def is_active(run_id: str) -> bool:
    r = _runners.get(run_id)
    return bool(r and r.is_alive())


def approve(project_id: str, approval_id: str, decision: str, edited_args: Any = None) -> dict:
    ap = db.get_approval(project_id, approval_id)
    if not ap:
        raise KeyError(approval_id)
    if ap["status"] != "pending":
        return {"ok": False, "error": "already resolved"}
    db.resolve_approval(project_id, approval_id, decision, edited_args)
    db.audit(project_id, ap["run_id"] or None, "approval_resolved", {"approval_id": approval_id, "decision": decision,
                                                                     "kind": ap["kind"], "edited": bool(edited_args)})
    if ap["kind"] == "mcp_install":
        if decision in ("approve", "edit"):
            from backend.tools import mcp_registry
            res = mcp_registry.install(ap["summary"]["candidate"])
            db.audit(project_id, None, "mcp_installed", res)
            return {"ok": True, "install": res}
        return {"ok": True}
    sid, rid = ap["step_id"], ap["run_id"]
    if decision == "reject":
        db.update_step(project_id, rid, sid, status="rejected", error="rejected by user", ended_at=_now())
    else:
        fields: dict = {"approved": True, "status": "pending"}
        if decision == "edit" and isinstance(edited_args, dict):
            fields["args"] = edited_args
            fields.pop("approved")  # edited args are approved as shown
            fields["approved"] = True
        db.update_step(project_id, rid, sid, **fields)
    events.publish(project_id, "step_update", {"run_id": rid, "step": db.get_step(project_id, rid, sid)})
    resume(project_id, rid)
    return {"ok": True}


def accept_suggestion(project_id: str, sug_id: str, accept: bool) -> dict:
    sug = db.get_suggestion(project_id, sug_id)
    if not sug:
        raise KeyError(sug_id)
    db.set_suggestion_status(project_id, sug_id, "accepted" if accept else "dismissed")
    if not accept:
        return {"ok": True}
    patch = db.unmask_value(project_id, sug["patch"])
    rid = sug["run_id"]
    if "save_workflow" in patch:
        from backend.agent import workflows
        path = workflows.save(project_id, rid, patch["save_workflow"].get("name"))
        return {"ok": True, "workflow": path}
    if patch.get("steps"):
        after = patch.get("insert_after")
        if after is None and patch.get("before"):
            plan = db.get_run(project_id, rid)["plan"]
            ids = [s["step_id"] for s in plan]
            pos = ids.index(patch["before"]) if patch["before"] in ids else len(ids)
            after = ids[pos - 1] if pos > 0 else None
        new = db.insert_steps_after(project_id, rid, after, patch["steps"])
        run = db.get_run(project_id, rid)
        events.publish(project_id, "plan", {"run_id": rid, "steps": run["plan"], "source": "suggestion"})
        if run["status"] in ("paused", "partial", "done", "failed"):
            _run_specific(project_id, rid, [s["step_id"] for s in new])
    return {"ok": True}


def _run_specific(project_id: str, run_id: str, step_ids: list[str]) -> None:
    """Run inserted steps now even if the run is paused on an unavailable tool (e.g. CSV backup during an outage)."""
    def work():
        db.set_current_run(run_id)
        r = Runner(project_id, run_id)
        for sid in step_ids:
            st = db.get_step(project_id, run_id, sid)
            if st and st["status"] == "pending":
                try:
                    r._run_step(st)
                except (WaitingForApproval, ToolUnavailable):
                    pass
        run = db.get_run(project_id, run_id)
        if run["status"] in ("done", "partial", "failed") and not is_active(run_id):
            resume(project_id, run_id)
    threading.Thread(target=work, daemon=True).start()


def report(project_id: str, run_id: str) -> dict:
    run = db.get_run(project_id, run_id)
    plan = run["plan"]
    by = {k: [s for s in plan if s["status"] == k] for k in ("done", "reused", "degraded", "failed", "rejected", "skipped", "pending", "awaiting_approval")}
    artifacts = []
    for s in plan:
        out = s.get("output") or {}
        if s["status"] in ("done", "reused", "degraded") and isinstance(out, dict) and s.get("risk") != "read" \
                and s["tool"] not in ("files.make_dir", "files.delete"):
            if out.get("path"):
                artifacts.append({"step_id": s["step_id"], "path": out["path"], "kind": (s.get("produces") or {}).get("type", "file")})
            for it in out.get("items") or []:
                p = (it.get("output") or {}).get("path") if it.get("ok") else None
                if p:
                    artifacts.append({"step_id": s["step_id"], "path": p, "kind": (s.get("produces") or {}).get("type", "file")})
            if s["tool"] == "office.excel_upsert_rows":
                path = catalog.resolve_paths(project_id, db.unmask_value(project_id, s.get("args") or {})).get("path")
                if path and "{{" not in path and not any(a["path"] == path for a in artifacts):
                    artifacts.append({"step_id": s["step_id"], "path": path, "kind": "spreadsheet",
                                      "inserted": out.get("inserted"), "updated": out.get("updated")})
    priv = run.get("privacy") or {}
    privacy = {"pii_masked": int(priv.get("pii_masked", 0)), "regions_blurred": int(priv.get("regions_blurred", 0)), "raw_pii_to_llm": 0}
    parts = [f"Run {run['status']}."]
    parts.append(f"{len(by['done'])} done, {len(by['reused'])} reused from knowledge, {len(by['degraded'])} degraded, "
                 f"{len(by['failed'])} failed, {len(by['rejected'])} rejected, {len(by['skipped'])} skipped.")
    if artifacts:
        parts.append("Artifacts: " + ", ".join(sorted({a["path"].replace("\\", "/").rsplit("/", 1)[-1] for a in artifacts})[:8]) + ".")
    unresolved = by["failed"] + by["pending"] + by["awaiting_approval"]
    if unresolved:
        parts.append("Unresolved: " + "; ".join(f"{s['step_id']} ({s['status']}): {str(s.get('error') or '')[:120]}" for s in unresolved) + ".")
    for s in by["degraded"]:
        parts.append(f"Degraded {s['step_id']}: {str(s.get('error') or '')[:120]}.")
    from backend.agent.think import NO_RECIPIENT
    blanks = 0
    for a in db.list_audit(project_id, run_id):
        if a["kind"] == "tool_call" and a["payload"].get("tool") == "office.outlook_create_draft":
            if NO_RECIPIENT in json.dumps(db.unmask_value(project_id, a["payload"].get("args"))):
                blanks += 1
    if blanks:
        parts.append(f"{blanks} draft(s) need a recipient: no contact address was found in the sources, so I did not guess one.")
    replans = [a for a in db.list_audit(project_id, run_id) if a["kind"] == "replan"]
    if replans:
        parts.append(f"Self-healed {len(replans)} time(s): " + "; ".join(f"{a['payload']['failed_step']} failed ({str(a['payload']['error'])[:80]}) -> new approach" for a in replans) + ".")
    parts.append(f"Privacy: {privacy['pii_masked']} PII items masked, {privacy['regions_blurred']} screenshot regions blurred, 0 raw PII sent to the LLM.")
    return {"run_id": run_id, "status": run["status"], "summary": " ".join(parts),
            "done": [s["step_id"] for s in by["done"]], "reused": [s["step_id"] for s in by["reused"]],
            "degraded": [s["step_id"] for s in by["degraded"]], "failed": [s["step_id"] for s in by["failed"] + by["pending"]],
            "rejected": [s["step_id"] for s in by["rejected"]], "artifacts": artifacts, "privacy": privacy,
            "assessment": run.get("assessment")}
