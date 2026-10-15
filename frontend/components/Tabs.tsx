"use client";
import React, { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Empty, Icon, Json, PIIBadge, RiskChip, Spinner, StatusChip, fileName, hasPII } from "./ui";

const KIND: Record<string, { label: string; color: string }> = {
  assessment: { label: "Intent & feasibility", color: "var(--info)" },
  plan: { label: "Plan", color: "var(--accent)" },
  tool_call: { label: "Tool call", color: "var(--ok)" },
  tool_error: { label: "Tool error", color: "var(--bad)" },
  llm_call: { label: "LLM call (masked)", color: "var(--violet)" },
  llm_fallback: { label: "LLM fallback", color: "var(--warn)" },
  approval_requested: { label: "Approval requested", color: "var(--accent)" },
  approval_resolved: { label: "Approval decision", color: "var(--accent)" },
  kg_reuse: { label: "Reused from KG", color: "var(--violet)" },
  replan: { label: "Self-healing re-plan", color: "var(--warn)" },
  outage_toggled: { label: "Outage toggled", color: "var(--bad)" },
  ui_action: { label: "UI action", color: "var(--info)" },
  prompt_injection_detected: { label: "Prompt injection flagged", color: "var(--bad)" },
  workflow_saved: { label: "Workflow saved", color: "var(--violet)" },
  step_edited: { label: "Step edited", color: "var(--muted)" },
  runner_crash: { label: "Runner error", color: "var(--bad)" },
};

function summary(a: any): string {
  const p = a.payload || {};
  switch (a.kind) {
    case "tool_call": return `${p.step_id} ${p.tool}${p.verify?.length ? " · " + p.verify[0] : ""}`;
    case "tool_error": return `${p.step_id} ${p.tool} attempt ${p.attempt}: ${p.error}`;
    case "llm_call": return `${p.model} · ${p.prompt_tokens ?? "?"}→${p.completion_tokens ?? "?"} tokens${p.json ? " · JSON" : ""}`;
    case "assessment": return `${p.verdict} · intent ${p.intent}${p.category ? " (" + p.category + ")" : ""} · feasibility ${p.feasibility}`;
    case "plan": return `${p.source}: ${(p.steps || []).join(" → ")}`;
    case "approval_requested": return `${p.step_id} ${p.kind} · ${p.tool}`;
    case "approval_resolved": return `${p.decision}${p.edited ? " (edited)" : ""} · ${p.kind}`;
    case "kg_reuse": return `${p.step_id} ← ${p.from}`;
    case "replan": return `${p.failed_step} failed: ${p.error} → ${(p.new_steps || []).join(", ")}`;
    case "outage_toggled": return p.on ? "Excel outage ON" : "Excel outage OFF";
    case "llm_fallback": return `${p.where}: ${p.error}`;
    default: return JSON.stringify(p).slice(0, 160);
  }
}

export function AuditTab({ pid, runId, tick }: { pid: string; runId: string | null; tick: number }) {
  const [items, setItems] = useState<any[] | null>(null);
  const [filter, setFilter] = useState("all");
  const [open, setOpen] = useState<number | null>(null);
  useEffect(() => {
    let alive = true;
    api.get(`/projects/${pid}/audit${runId ? `?run_id=${runId}` : ""}`).then((x) => alive && setItems(x)).catch(() => {});
    return () => { alive = false; };
  }, [pid, runId, tick]);
  if (!items) return <div style={{ padding: 16 }}><Spinner /></div>;
  if (!items.length) return <Empty icon={<Icon.list />} title="No audit events yet" />;
  const shown = items.filter((a) => filter === "all" || (filter === "llm" ? a.kind.startsWith("llm") : filter === "tools" ? a.kind.startsWith("tool") : filter === "safety" ? ["assessment", "approval_requested", "approval_resolved", "prompt_injection_detected"].includes(a.kind) : true)).slice().reverse();
  return (
    <div>
      <div style={{ display: "flex", gap: 4, marginBottom: 10, flexWrap: "wrap" }}>
        {["all", "tools", "llm", "safety"].map((f) => <button key={f} className={`tab ${filter === f ? "tab-active" : ""}`} onClick={() => setFilter(f)}>{f}</button>)}
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 11.5, color: "var(--faint)", alignSelf: "center" }}>{items.length} events · append-only</span>
      </div>
      <div style={{ position: "relative", paddingLeft: 14 }}>
        <div style={{ position: "absolute", left: 4, top: 4, bottom: 4, width: 2, background: "var(--border)" }} />
        {shown.map((a) => {
          const k = KIND[a.kind] || { label: a.kind, color: "var(--muted)" };
          return (
            <div key={a.id} style={{ position: "relative", padding: "6px 0 6px 8px" }}>
              <span style={{ position: "absolute", left: -14, top: 11, width: 10, height: 10, borderRadius: 9, background: k.color, boxShadow: "0 0 0 3px var(--panel)" }} />
              <div style={{ display: "flex", gap: 6, alignItems: "center", cursor: "pointer" }} onClick={() => setOpen(open === a.id ? null : a.id)}>
                <span style={{ fontSize: 12, fontWeight: 650, color: k.color }}>{k.label}</span>
                <span style={{ fontSize: 11, color: "var(--faint)" }}>{new Date(a.created_at).toLocaleTimeString()}</span>
                {hasPII(a.payload) ? <PIIBadge /> : null}
              </div>
              <div style={{ fontSize: 12, color: "var(--muted)", wordBreak: "break-word" }}>{summary(a)}</div>
              {open === a.id ? <div style={{ marginTop: 4 }}><Json value={a.payload} max={3000} /></div> : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function count(v: any): number {
  return Array.isArray(v) ? v.length : typeof v === "number" ? v : 0;
}

/** Full report for ONE run. Used in the Report tab and in the full-width report window. */
export function ReportView({ pid, runId, report, run, wide = false }: { pid: string; runId: string; report?: any; run?: any; wide?: boolean }) {
  const [rep, setRep] = useState<any>(report || null);
  const [r, setR] = useState<any>(run || null);
  useEffect(() => {
    let alive = true;
    setRep(report || null);
    setR(run || null);
    api.get(`/projects/${pid}/runs/${runId}/report`).then((x) => alive && setRep(x)).catch(() => {});
    if (!run) api.get(`/projects/${pid}/runs/${runId}`).then((x) => alive && setR(x)).catch(() => {});
    return () => { alive = false; };
  }, [pid, runId, report, run]);
  if (!rep) return <div style={{ padding: 24 }}><Spinner /></div>;
  const plan = r?.plan || r?.steps || [];
  const stat = (label: string, n: number, color: string) => (
    <div className="panel" style={{ padding: "14px 16px", flex: "1 1 120px", minWidth: 110 }}>
      <div style={{ fontSize: 26, fontWeight: 700, color, lineHeight: 1.1 }}>{n}</div>
      <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 4 }}>{label}</div>
    </div>
  );
  const artifacts: any[] = Array.from(new Map((rep.artifacts || []).map((a: any) => [a.path, a])).values());
  const sentences = String(rep.summary || "").split(/(?<=\.)\s+(?=[A-Z0-9])/).filter(Boolean);
  return (
    <div style={{ display: "grid", gap: 20 }}>
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span className="font-serif-display" style={{ fontSize: wide ? 24 : 20, fontWeight: 600 }}>Run report</span>
          <StatusChip status={rep.status || r?.status} />
          <span className="mono" style={{ fontSize: 11.5, color: "var(--faint)" }}>{runId}</span>
        </div>
        {r?.goal_masked ? <div style={{ marginTop: 8, fontSize: 14, color: "var(--muted)", lineHeight: 1.55 }}>{r.goal_masked}</div> : null}
      </div>

      <div style={{ display: "grid", gap: 8 }}>
        {sentences.map((s, i) => (
          <div key={i} style={{ fontSize: 14, lineHeight: 1.6, padding: "10px 14px", borderRadius: 10, background: "var(--panel-2)",
            borderLeft: `3px solid ${/unresolved|failed|need a recipient|degraded/i.test(s) ? "var(--warn)" : /privacy/i.test(s) ? "var(--violet)" : "var(--border-strong)"}` }}>{s}</div>
        ))}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
        {stat("done", count(rep.done), "var(--ok)")}
        {stat("reused from knowledge", count(rep.reused), "var(--violet)")}
        {stat("degraded", count(rep.degraded), "var(--warn)")}
        {stat("failed / open", count(rep.failed), "var(--bad)")}
        {stat("rejected", count(rep.rejected), "var(--muted)")}
        {stat("artifacts", artifacts.length, "var(--text)")}
      </div>

      <div className="panel" style={{ padding: 16, borderColor: "var(--violet)", background: "var(--violet-soft)" }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", fontWeight: 650, color: "var(--violet)" }}><Icon.shield width={15} height={15} /> Privacy</div>
        <div style={{ fontSize: 14, marginTop: 6, lineHeight: 1.55 }}>
          {rep.privacy?.pii_masked || 0} PII items masked · {rep.privacy?.regions_blurred || 0} screenshot regions blurred · <b>{rep.privacy?.raw_pii_to_llm || 0} raw PII sent to the LLM</b>
        </div>
      </div>

      {artifacts.length ? (
        <div>
          <div style={{ fontWeight: 650, fontSize: 14, marginBottom: 10 }}>Artifacts</div>
          <div style={{ display: "grid", gridTemplateColumns: wide ? "repeat(auto-fill, minmax(280px, 1fr))" : "1fr", gap: 8 }}>
            {artifacts.map((a: any) => (
              <div key={a.path} style={{ display: "flex", gap: 10, alignItems: "center", border: "1px solid var(--border)", borderRadius: 10, padding: "10px 12px", background: "var(--panel)" }}>
                {a.kind === "spreadsheet" ? <Icon.table /> : a.path.endsWith(".eml") ? <Icon.mail /> : <Icon.doc />}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13.5, fontWeight: 500, wordBreak: "break-all" }}>{fileName(a.path)}</div>
                  <div className="mono" style={{ fontSize: 11, color: "var(--faint)", wordBreak: "break-all" }}>{a.path}</div>
                </div>
                <button className="btn btn-sm" onClick={() => api.post(`/projects/${pid}/open`, { path: a.path }).catch(() => {})}><Icon.open width={12} height={12} /> Open</button>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {plan.length ? (
        <div>
          <div style={{ fontWeight: 650, fontSize: 14, marginBottom: 10 }}>Step outcomes</div>
          <div style={{ display: "grid", gap: 6 }}>
            {plan.map((s: any) => (
              <div key={s.step_id} style={{ display: "flex", gap: 12, alignItems: "flex-start", padding: "10px 12px", border: "1px solid var(--border)", borderRadius: 10, background: "var(--panel)" }}>
                <span className="mono" style={{ fontSize: 11.5, color: "var(--faint)", minWidth: 30, paddingTop: 2 }}>{s.step_id}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13.5, lineHeight: 1.45 }}>{s.description}</div>
                  <div className="mono" style={{ fontSize: 11, color: "var(--faint)", marginTop: 2 }}>{s.tool}</div>
                  {s.error && s.status !== "done" ? <div style={{ fontSize: 12.5, color: s.status === "failed" ? "var(--bad)" : "var(--muted)", marginTop: 4, wordBreak: "break-word" }}>{s.error}</div> : null}
                </div>
                <StatusChip status={s.status} />
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function ReportTab({ pid, runs, initialRunId, onOpenFull }: { pid: string; runs: { run_id: string; goal: string; status?: string }[]; initialRunId: string | null; onOpenFull: (rid: string) => void }) {
  const [sel, setSel] = useState<string | null>(initialRunId);
  useEffect(() => { setSel(initialRunId); }, [initialRunId]);
  if (!runs.length || !sel) return <Empty icon={<Icon.report />} title="No runs yet" hint="Every task you start gets its own report here." />;
  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <select className="input" value={sel} onChange={(e) => setSel(e.target.value)} style={{ flex: 1, minWidth: 0 }}>
          {runs.slice().reverse().map((r, i) => (
            <option key={r.run_id} value={r.run_id}>Run {runs.length - i} · {(r.goal || r.run_id).slice(0, 70)}</option>
          ))}
        </select>
        <button className="btn" onClick={() => onOpenFull(sel)} title="Open the full report"><Icon.open width={13} height={13} /> Expand</button>
      </div>
      <ReportView key={sel} pid={pid} runId={sel} />
    </div>
  );
}

export function ReportModal({ pid, runId, onClose }: { pid: string; runId: string; onClose: () => void }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(20,18,15,.55)", zIndex: 60, display: "grid", placeItems: "center", padding: 20 }}>
      <div className="card scroll-thin" onClick={(e) => e.stopPropagation()}
        style={{ width: "min(980px, 100%)", maxHeight: "92vh", overflow: "auto", padding: "26px 30px", position: "relative" }}>
        <button className="btn btn-ghost" onClick={onClose} style={{ position: "absolute", top: 14, right: 14 }} title="Close (Esc)"><Icon.x /></button>
        <ReportView pid={pid} runId={runId} wide />
      </div>
    </div>
  );
}

export function ToolsTab({ pid, tick }: { pid: string; tick: number }) {
  const [data, setData] = useState<any>(null);
  const [need, setNeed] = useState("");
  const [reg, setReg] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get("/tools").then(setData).catch(() => {}); }, [tick]);
  if (!data) return <div style={{ padding: 16 }}><Spinner /></div>;
  const servers = Object.entries(data.servers || {});
  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div>
        <div style={{ fontWeight: 650, fontSize: 12.5, marginBottom: 6 }}>Connected MCP servers</div>
        {servers.map(([name, s]: any) => (
          <div key={name} className="panel" style={{ padding: 10, marginBottom: 6 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ width: 8, height: 8, borderRadius: 9, background: s.connected ? "var(--ok)" : "var(--bad)" }} />
              <b style={{ fontSize: 13 }}>{name}</b>
              <span className="chip chip-muted">stdio</span>
              <span style={{ flex: 1 }} />
              <span style={{ fontSize: 11.5, color: "var(--muted)" }}>{s.connected ? `${s.tools} tools` : s.error || "not connected"}</span>
            </div>
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 8 }}>
              {(data.mcp_tools || []).filter((t: any) => t.server === name).map((t: any) => (
                <span key={t.name} className="chip chip-muted" title={t.description}>{t.name} <RiskChip risk={t.risk} /></span>
              ))}
            </div>
          </div>
        ))}
      </div>
      {data.installed_unused?.length ? (
        <div>
          <div style={{ fontWeight: 650, fontSize: 12.5, marginBottom: 6 }}>Installed on this PC but unused</div>
          {data.installed_unused.map((s: any) => (
            <div key={s.name + s.source} style={{ fontSize: 12.5, padding: "4px 0", display: "flex", gap: 6 }}>
              <span className="chip chip-muted">{s.source}</span> <b>{s.name}</b> <span className="mono" style={{ color: "var(--faint)", fontSize: 11 }}>{s.command} {(s.args || []).join(" ")}</span>
            </div>
          ))}
        </div>
      ) : null}
      <div>
        <div style={{ fontWeight: 650, fontSize: 12.5, marginBottom: 6 }}>Find a better tool (MCP registry)</div>
        <form style={{ display: "flex", gap: 6 }} onSubmit={async (e) => {
          e.preventDefault(); if (!need.trim()) return; setBusy(true);
          try { setReg(await api.get(`/tools/registry?need=${encodeURIComponent(need)}`)); } catch {}
          setBusy(false);
        }}>
          <input className="input" placeholder="e.g. control desktop apps, read PDFs, GitHub issues" value={need} onChange={(e) => setNeed(e.target.value)} />
          <button className="btn" disabled={busy}>{busy ? <Spinner /> : "Search"}</button>
        </form>
        {reg ? (
          <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
            {reg.best?.better ? <div className="chip chip-info" style={{ justifySelf: "start" }}>Suggested: {reg.best.better.name}</div> : null}
            {reg.results.map((r: any) => (
              <div key={r.name} className="panel" style={{ padding: 10 }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <b style={{ fontSize: 12.5, flex: 1 }}>{r.name}</b>
                  <span style={{ fontSize: 11, color: "var(--faint)" }}>match {Math.round(r.score * 100)}%</span>
                  <button className="btn btn-sm" onClick={() => api.post(`/projects/${pid}/tools/install`, { candidate: r }).catch(() => {})}>Install…</button>
                </div>
                <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 3 }}>{r.description}</div>
              </div>
            ))}
          </div>
        ) : null}
      </div>
      <div>
        <div style={{ fontWeight: 650, fontSize: 12.5, marginBottom: 6 }}>Agent capability catalog ({data.catalog.length})</div>
        <div style={{ display: "grid", gap: 3 }}>
          {data.catalog.map((t: any) => (
            <div key={t.name} style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12 }} title={t.description}>
              <span style={{ width: 6, height: 6, borderRadius: 9, background: t.used ? "var(--ok)" : "var(--border-strong)" }} />
              <span className="mono" style={{ flex: 1 }}>{t.name}</span>
              <span className="chip chip-muted">{t.route}</span>
              <RiskChip risk={t.risk} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
