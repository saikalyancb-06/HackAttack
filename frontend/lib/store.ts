"use client";
import { useCallback, useEffect, useReducer, useRef } from "react";
import { API, api, Run, Step } from "./api";

export type State = {
  runs: Record<string, Run>;
  logs: Record<string, { text: string; ts: string }[]>;
  reports: Record<string, any>;
  approvals: any[];
  suggestions: Record<string, any[]>;
  graph: { nodes: any[]; edges: any[] };
  connected: boolean;
  kgPulse: number;
};

const initial: State = { runs: {}, logs: {}, reports: {}, approvals: [], suggestions: {}, graph: { nodes: [], edges: [] }, connected: false, kgPulse: 0 };

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
    es.onerror = () => dispatch({ type: "connected", value: false });
    return () => es.close();
  }, [pid, loadApprovals, loadGraph, loadRun]);

  const stateRef = useRef(state);
  stateRef.current = state;

  return { state, dispatch, loadRun, loadApprovals, loadGraph };
}
