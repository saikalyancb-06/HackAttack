"use client";
import { useCallback, useEffect, useReducer, useRef } from "react";
import { API, api, Run, Step } from "./api";
import { extensionReady, extRequest, onExtensionEvent } from "./extension";

export type State = {
  runs: Record<string, Run>;
  logs: Record<string, { text: string; ts: string }[]>;
  reports: Record<string, any>;
  approvals: any[];
  suggestions: Record<string, any[]>;
  graph: { nodes: any[]; edges: any[] };
  connected: boolean;
  kgPulse: number;
  clarifications: Record<string, any[]>;   // run_id -> questions waiting for the user
  browser: Record<string, any>;            // run_id -> live state of its browser tab task
};

const initial: State = { runs: {}, logs: {}, reports: {}, approvals: [], suggestions: {}, graph: { nodes: [], edges: [] }, connected: false, kgPulse: 0, clarifications: {}, browser: {} };

function upsertStep(plan: Step[], step: Step): Step[] {
  const i = plan.findIndex((s) => s.step_id === step.step_id);
  if (i === -1) return [...plan, step];
  const copy = plan.slice();
  copy[i] = step;
  return copy;
}

function reducer(s: State, a: any): State {
  switch (a.type) {
    case "reset":
      return { ...initial, connected: s.connected };
    case "connected":
      return { ...s, connected: a.value };
    case "run": {
      return { ...s, runs: { ...s.runs, [a.run.run_id]: { ...(s.runs[a.run.run_id] || {}), ...a.run } } };
    }
    case "plan": {
      const r = s.runs[a.run_id] || ({ run_id: a.run_id, plan: [], status: "running" } as any);
      return { ...s, runs: { ...s.runs, [a.run_id]: { ...r, plan: a.steps, planSource: a.source } } };
    }
    case "step": {
      const r = s.runs[a.run_id];
      if (!r) return s;
      return { ...s, runs: { ...s.runs, [a.run_id]: { ...r, plan: upsertStep(r.plan || [], a.step) } } };
    }
    case "status": {
      const r = s.runs[a.run_id];
      if (!r) return s;
      return { ...s, runs: { ...s.runs, [a.run_id]: { ...r, status: a.status, error: a.error, active: a.status === "running" } } };
    }
    case "assessment": {
      const r = s.runs[a.run_id] || ({ run_id: a.run_id, plan: [] } as any);
      return { ...s, runs: { ...s.runs, [a.run_id]: { ...r, assessment: a.assessment } } };
    }
    case "log": {
      if (!a.run_id) return s;
      const cur = s.logs[a.run_id] || [];
      return { ...s, logs: { ...s.logs, [a.run_id]: [...cur, { text: a.text, ts: a.ts }].slice(-200) } };
    }
    case "report":
      return { ...s, reports: { ...s.reports, [a.run_id]: a.report } };
    case "approvals":
      return { ...s, approvals: a.list };
    case "suggestions": {
      return { ...s, suggestions: { ...s.suggestions, [a.run_id]: a.list } };
    }
    case "suggestion": {
      const cur = (s.suggestions[a.sug.run_id] || []).filter((x) => x.sug_id !== a.sug.sug_id);
      return { ...s, suggestions: { ...s.suggestions, [a.sug.run_id]: [...cur, { status: "open", ...a.sug }] } };
    }
    case "graph":
      return { ...s, graph: a.graph, kgPulse: s.kgPulse + 1 };
    case "clarification":
      return { ...s, clarifications: { ...s.clarifications, [a.run_id]: a.questions } };
    case "browser":
      return { ...s, browser: { ...s.browser, [a.run_id]: { ...(s.browser[a.run_id] || {}), ...a.patch } } };
    default:
      return s;
  }
}

export function useOperator(pid: string | null) {
  const [state, dispatch] = useReducer(reducer, initial);
  const esRef = useRef<EventSource | null>(null);

  const loadApprovals = useCallback(async () => {
    if (!pid) return;
    try {
      dispatch({ type: "approvals", list: await api.get(`/projects/${pid}/approvals`) });
    } catch {}
  }, [pid]);

  const loadRun = useCallback(async (rid: string) => {
    if (!pid) return;
    try {
      const run = await api.get(`/projects/${pid}/runs/${rid}`);
      dispatch({ type: "run", run });
      const sugs = await api.get(`/projects/${pid}/suggestions?run_id=${rid}`);
      dispatch({ type: "suggestions", run_id: rid, list: sugs });
      if (["done", "partial", "failed", "stopped"].includes(run.status)) {
        dispatch({ type: "report", run_id: rid, report: await api.get(`/projects/${pid}/runs/${rid}/report`) });
      }
      if (run.status === "needs_input") {
        const c = await api.get(`/projects/${pid}/runs/${rid}/clarification`);
        if (c.questions?.length && !c.answered) dispatch({ type: "clarification", run_id: rid, questions: c.questions });
      }
    } catch {}
  }, [pid]);

  const loadGraph = useCallback(async () => {
    if (!pid) return;
    try {
      dispatch({ type: "graph", graph: await api.get(`/projects/${pid}/graph`) });
    } catch {}
  }, [pid]);

  useEffect(() => {
    dispatch({ type: "reset" });
    if (!pid) return;
    loadApprovals();
    loadGraph();
    const es = new EventSource(`${API}/projects/${pid}/events`);
    esRef.current = es;
    const on = (name: string, fn: (d: any) => void) =>
      es.addEventListener(name, (e: any) => {
        try {
          fn(JSON.parse(e.data));
        } catch {}
      });
    on("hello", () => {
      dispatch({ type: "connected", value: true });
      // reconnect: refetch everything we already show so no event is lost
      loadApprovals();
      loadGraph();
      Object.keys(stateRef.current.runs).forEach((rid) => loadRun(rid));
    });
    on("plan", (d) => dispatch({ type: "plan", run_id: d.run_id, steps: d.steps, source: d.source }));
    on("step_update", (d) => d.step && dispatch({ type: "step", run_id: d.run_id, step: d.step }));
    on("run_status", (d) => {
      dispatch({ type: "status", run_id: d.run_id, status: d.status, error: d.error });
      loadApprovals();
    });
    on("assessment", (d) => dispatch({ type: "assessment", run_id: d.run_id, assessment: d.assessment }));
    on("approval_required", () => loadApprovals());
    on("suggestion", (d) => dispatch({ type: "suggestion", sug: d }));
    on("kg_update", (d) => dispatch({ type: "graph", graph: d }));
    on("log", (d) => dispatch({ type: "log", run_id: d.run_id, text: d.text, ts: d.ts }));
    on("report", (d) => dispatch({ type: "report", run_id: d.run_id, report: d.report }));
    on("clarification", (d) => dispatch({ type: "clarification", run_id: d.run_id, questions: d.questions }));
    on("browser_task", (d) => dispatchBrowserTask(d));
    on("browser_control", (d) => { if (d.command === "stop") extRequest("STOP"); });
    es.onerror = () => dispatch({ type: "connected", value: false });

    // A web step asks this page to run it in a new tab through the extension
    const dispatched = new Set<string>();
    async function dispatchBrowserTask(d: any) {
      const key = `${d.run_id}:${d.step_id}`;
      if (dispatched.has(key)) return;
      dispatched.add(key);
      const report = (body: any) => api.post(`/projects/${pid}/runs/${d.run_id}/steps/${d.step_id}/browser`, body).catch(() => {});
      dispatch({ type: "browser", run_id: d.run_id, patch: { step_id: d.step_id, task: d.task, phase: "opening" } });
      if (!extensionReady()) {
        dispatch({ type: "browser", run_id: d.run_id, patch: { phase: "no_extension" } });
        return report({ kind: "end", outcome: "UNAVAILABLE" });
      }
      const res = await extRequest("START_TASK", { task: d.task, startUrl: d.start_url, runRef: { pid, run_id: d.run_id, step_id: d.step_id } });
      if (!res.success) {
        dispatch({ type: "browser", run_id: d.run_id, patch: { phase: "error", error: res.error } });
        return report({ kind: "end", outcome: "UNAVAILABLE", reason: res.error });
      }
      dispatch({ type: "browser", run_id: d.run_id, patch: { phase: "running", url: res.url } });
      report({ kind: "started", url: res.url });
    }
    const offExt = onExtensionEvent((ev: any) => {
      const ref = ev?.runRef;
      if (!ref || ref.pid !== pid) return;
      const send = (body: any) => api.post(`/projects/${pid}/runs/${ref.run_id}/steps/${ref.step_id}/browser`, body).catch(() => {});
      if (ev.kind === "state") {
        dispatch({ type: "browser", run_id: ref.run_id, patch: { phase: ev.state?.waitingForUser ? "user_turn" : "running", state: ev.state, lastLog: ev.log || undefined } });
        send({ kind: "state", state: ev.state, log: ev.log });
      } else if (ev.kind === "end") {
        dispatch({ type: "browser", run_id: ref.run_id, patch: { phase: "ended", end: ev } });
        send({ kind: "end", ...ev });
      }
    });
    // pick up web steps that started while this page was closed
    api.get(`/projects/${pid}/browser/pending`).then((list: any[]) => list.filter((p) => !p.started).forEach((p) =>
      api.get(`/projects/${pid}/runs/${p.run_id}`).then((run: any) => {
        const st = (run.plan || []).find((s: any) => s.step_id === p.step_id);
        if (st) dispatchBrowserTask({ run_id: p.run_id, step_id: p.step_id, task: st.args?.task || st.description, start_url: st.args?.start_url });
      }))).catch(() => {});
    return () => { es.close(); offExt(); };
  }, [pid, loadApprovals, loadGraph, loadRun]);

  const stateRef = useRef(state);
  stateRef.current = state;

  return { state, dispatch, loadRun, loadApprovals, loadGraph };
}
