"use client";
import React, { useMemo, useState } from "react";
import { api, Run, Step } from "@/lib/api";
import { Icon, Json, PIIBadge, RiskChip, RouteIcon, Spinner, StatusChip, fileName, hasPII } from "./ui";

const ACTIVE = ["running"];

export function AssessmentCard({ a }: { a: any }) {
  if (!a || !a.verdict) return null;
  const f = a.feasibility || {};
  const it = a.intent || {};
  const verdict: Record<string, { label: string; cls: string; icon: any }> = {
    proceed: { label: "Safe & feasible", cls: "chip-ok", icon: <Icon.check width={12} height={12} /> },
    proceed_partial: { label: "Partly feasible", cls: "chip-warn", icon: <Icon.alert width={12} height={12} /> },
    clarify: { label: "Not feasible as asked", cls: "chip-warn", icon: <Icon.alert width={12} height={12} /> },
    refuse: { label: "Refused: harmful intent", cls: "chip-bad", icon: <Icon.x width={12} height={12} /> },
  };
  const v = verdict[a.verdict] || verdict.proceed;
  return (
    <div className="fade-in" style={{ border: "1px solid var(--border)", borderRadius: 10, padding: "10px 12px", background: "var(--panel-2)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <Icon.shield width={14} height={14} style={{ color: "var(--muted)" }} />
        <span style={{ fontWeight: 600, fontSize: 12.5 }}>Intent & feasibility check</span>
        <span className={`chip ${v.cls}`}>{v.icon}{v.label}</span>
        <span className={`chip ${it.label === "benign" ? "chip-ok" : it.label === "harmful" ? "chip-bad" : "chip-warn"}`}>
          intent: {it.label}{it.category && it.label !== "benign" ? ` · ${it.category}` : ""}
        </span>
        <span className={`chip ${f.level === "full" ? "chip-ok" : f.level === "none" ? "chip-bad" : "chip-warn"}`}>feasibility: {f.level}</span>
        {a.injection?.flagged ? <span className="chip chip-bad">prompt injection</span> : null}
      </div>
      {a.verdict === "refuse" ? <div style={{ marginTop: 8, color: "var(--bad)", fontSize: 13 }}>{it.reasons?.[0] || a.summary}</div> : null}
      {f.unsupported?.length ? (
        <div style={{ marginTop: 8, display: "grid", gap: 4 }}>
          {f.unsupported.slice(0, 4).map((u: any, i: number) => (
            <div key={i} style={{ fontSize: 12.5, display: "flex", gap: 6 }}>
              <span style={{ color: "var(--bad)" }}>✕</span>
              <span><b>{u.need}</b>{u.alternative ? <span style={{ color: "var(--muted)" }}> → {u.alternative}</span> : null}</span>
            </div>
          ))}
        </div>
      ) : null}
      {f.needs_approval?.length ? (
        <div style={{ marginTop: 6, fontSize: 12, color: "var(--muted)" }}>Will ask before: {f.needs_approval.slice(0, 3).join(" · ")}</div>
      ) : null}
      {f.missing_inputs?.length ? <div style={{ marginTop: 6, fontSize: 12, color: "var(--warn)" }}>Missing: {f.missing_inputs.join(" · ")}</div> : null}
    </div>
  );
}

function Evidence({ pid, step }: { pid: string; step: Step }) {
  const ev = step.evidence || {};
  const out = step.output;
  const shots: string[] = [];
  if (ev.screenshot) shots.push(ev.screenshot);
  const paths: string[] = [];
  if (out?.path) paths.push(out.path);
  (out?.items || []).forEach((it: any) => it.ok && it.output?.path && paths.push(it.output.path));
  return (
    <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
      {ev.checks?.length ? (
        <div style={{ display: "grid", gap: 3 }}>
          {ev.checks.map((c: string, i: number) => (
            <div key={i} style={{ fontSize: 12, display: "flex", gap: 6, color: "var(--muted)" }}>
              <Icon.check width={13} height={13} style={{ color: "var(--ok)", flexShrink: 0, marginTop: 1 }} /> {c}
            </div>
          ))}
        </div>
      ) : null}
      {ev.transport || ev.backend ? (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {ev.transport ? <span className="chip chip-muted">via {String(ev.transport).startsWith("mcp") ? "MCP · office_com" : ev.transport}</span> : null}
          {ev.backend ? <span className="chip chip-muted">{ev.backend}</span> : null}
          {ev.source ? <span className="chip chip-muted">source: {ev.source}</span> : null}
        </div>
      ) : null}
      {shots.map((s) => (
        <div key={s}>
          <img src={api.fileUrl(pid, s)} alt="screenshot evidence (PII blurred)" style={{ maxWidth: "100%", borderRadius: 8, border: "1px solid var(--border)" }} />
          <span className="chip chip-violet" style={{ marginTop: 4 }}>{ev.regions_blurred || 0} PII regions blurred</span>
        </div>
      ))}
      {paths.length ? (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {paths.slice(0, 8).map((p) => (
            <button key={p} className="btn btn-sm" onClick={() => api.post(`/projects/${pid}/open`, { path: p }).catch(() => {})}>
              <Icon.open width={12} height={12} /> {fileName(p)}
            </button>
          ))}
        </div>
      ) : null}
      <details>
        <summary style={{ cursor: "pointer", fontSize: 12, color: "var(--faint)" }}>Arguments & raw output</summary>
        <div style={{ display: "grid", gap: 6, marginTop: 6 }}>
          <Json value={step.args} max={2500} />
          {out ? <Json value={out} max={5000} /> : null}
        </div>
      </details>
    </div>
  );
}

function StepRow({ pid, step, onEdit }: { pid: string; step: Step; onEdit: (s: Step) => void }) {
  const [open, setOpen] = useState(false);
  const isActive = step.status === "running";
  const muted = step.status === "skipped";
  return (
    <div className="fade-in" style={{ borderTop: "1px solid var(--border)", padding: "9px 2px", opacity: muted ? 0.55 : 1 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer" }} onClick={() => setOpen(!open)}>
        <span style={{ width: 26, height: 26, borderRadius: 7, display: "grid", placeItems: "center", flexShrink: 0,
          background: step.status === "done" ? "var(--ok-soft)" : step.status === "reused" ? "var(--violet-soft)" : step.status === "failed" ? "var(--bad-soft)" : "var(--panel-2)",
          color: step.status === "done" ? "var(--ok)" : step.status === "reused" ? "var(--violet)" : step.status === "failed" ? "var(--bad)" : "var(--muted)" }}>
          {isActive ? <Spinner /> : <RouteIcon route={step.route} tool={step.tool} />}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13.5, fontWeight: 500, textDecoration: muted ? "line-through" : "none" }}>{step.description}</div>
          <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 2, flexWrap: "wrap" }}>
            <span className="mono" style={{ fontSize: 11, color: "var(--faint)" }}>{step.step_id} · {step.tool}{step.foreach ? " · for each" : ""}</span>
            {step.foreach && step.attempts > 1 ? <span className="chip chip-muted">{step.attempts} tool calls</span>
              : step.attempts > 1 ? <span className="chip chip-warn">{step.attempts} attempts</span> : null}
            {step.status === "reused" ? <span className="chip chip-violet"><Icon.graph width={11} height={11} /> Reused from knowledge</span> : null}
            {step.risk !== "read" ? <RiskChip risk={step.risk} /> : null}
            {hasPII(step.args) || hasPII(step.output) ? <PIIBadge /> : null}
          </div>
        </div>
        <StatusChip status={step.status} />
        <Icon.chevron width={14} height={14} style={{ color: "var(--faint)", transform: open ? "rotate(90deg)" : "none", transition: "transform .15s" }} />
      </div>
      {step.error && step.status !== "done" ? (
        <div style={{ marginLeft: 36, marginTop: 5, fontSize: 12, color: step.status === "failed" ? "var(--bad)" : "var(--muted)" }}>{step.error}</div>
      ) : null}
      {open ? (
        <div style={{ marginLeft: 36 }}>
          {step.status === "pending" ? (
            <button className="btn btn-sm" style={{ marginTop: 8 }} onClick={() => onEdit(step)}>
              <Icon.edit width={12} height={12} /> Edit step
            </button>
          ) : null}
          <Evidence pid={pid} step={step} />
        </div>
      ) : null}
    </div>
  );
}

function EditStep({ pid, step, onClose }: { pid: string; step: Step; onClose: () => void }) {
  const [desc, setDesc] = useState(step.description);
  const [args, setArgs] = useState(JSON.stringify(step.args, null, 2));
  const [err, setErr] = useState("");
  return (
    <div style={{ border: "1px solid var(--accent)", borderRadius: 10, padding: 10, marginTop: 8, background: "var(--accent-soft)" }}>
      <div style={{ fontWeight: 600, fontSize: 12.5, marginBottom: 6 }}>Edit {step.step_id}</div>
      <input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} />
      <textarea className="input mono" style={{ marginTop: 6, minHeight: 120, fontSize: 12 }} value={args} onChange={(e) => setArgs(e.target.value)} />
      {err ? <div style={{ color: "var(--bad)", fontSize: 12 }}>{err}</div> : null}
      <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
        <button className="btn btn-primary btn-sm" onClick={async () => {
          try {
            await api.patch(`/projects/${pid}/runs/${step.run_id}/steps/${step.step_id}`, { description: desc, args: JSON.parse(args) });
            onClose();
          } catch (e: any) { setErr(e.message); }
        }}>Save</button>
        <button className="btn btn-sm" onClick={onClose}>Cancel</button>
      </div>
    </div>
  );
}

export function RunCard({ pid, run, logs, report, suggestions, approvals, onOpenApprovals, onSuggestion }: {
  pid: string; run: Run | undefined; logs: any[]; report: any; suggestions: any[]; approvals: any[];
  onOpenApprovals: () => void; onSuggestion: (id: string, accept: boolean) => void;
}) {
  const [editing, setEditing] = useState<Step | null>(null);
  const [showLog, setShowLog] = useState(false);
  const plan = run?.plan || [];
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    plan.forEach((s) => (c[s.status] = (c[s.status] || 0) + 1));
    return c;
  }, [plan]);
  if (!run) return <div className="card" style={{ padding: 14 }}><Spinner /> Loading run…</div>;
  const finished = (counts.done || 0) + (counts.reused || 0) + (counts.skipped || 0) + (counts.degraded || 0) + (counts.rejected || 0);
  const pct = plan.length ? Math.round((finished / plan.length) * 100) : 0;
  const myApprovals = approvals.filter((a) => a.run_id === run.run_id);
  const doneIds = new Set(plan.filter((s) => ["done", "reused"].includes(s.status)).map((s) => s.step_id));
  // a workaround offered for a step that has since succeeded is moot
  const openSugs = (suggestions || []).filter((s) => s.status === "open" && !(s.patch?.before && doneIds.has(s.patch.before)));
  const lastLog = logs?.[logs.length - 1]?.text;
  const isLive = ACTIVE.includes(run.status);
  const canResume = ["paused", "partial", "failed"].includes(run.status);

  return (
    <div className="card fade-in" style={{ padding: 14, display: "grid", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Icon.sparkle width={15} height={15} style={{ color: "var(--accent)" }} />
        <span style={{ fontWeight: 650 }}>Run</span>
        <span className="mono" style={{ fontSize: 11, color: "var(--faint)" }}>{run.run_id}</span>
        <StatusChip status={run.status} />
        {(run as any).planSource ? <span className="chip chip-muted">plan: {(run as any).planSource}</span> : null}
        <div style={{ flex: 1 }} />
        {isLive ? (
          <button className="btn btn-sm" onClick={() => api.post(`/projects/${pid}/runs/${run.run_id}/pause`)}><Icon.pause width={12} height={12} />Pause</button>
        ) : null}
        {canResume ? (
          <button className="btn btn-sm btn-primary" onClick={() => api.post(`/projects/${pid}/runs/${run.run_id}/resume`)}><Icon.play width={12} height={12} />Resume</button>
        ) : null}
        {isLive || run.status === "paused" ? (
          <button className="btn btn-sm" onClick={() => api.post(`/projects/${pid}/runs/${run.run_id}/stop`)}><Icon.stop width={12} height={12} />Stop</button>
        ) : null}
      </div>

      <AssessmentCard a={run.assessment} />

      {plan.length ? (
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 2 }}>
            <div style={{ flex: 1, height: 4, borderRadius: 9, background: "var(--panel-2)", overflow: "hidden" }}>
              <div style={{ width: `${pct}%`, height: "100%", background: counts.failed ? "var(--warn)" : "var(--ok)", transition: "width .4s" }} />
            </div>
            <span style={{ fontSize: 11.5, color: "var(--muted)" }}>{finished}/{plan.length} steps</span>
          </div>
          {plan.map((s) => (
            <div key={s.step_id}>
              <StepRow pid={pid} step={s} onEdit={setEditing} />
              {editing?.step_id === s.step_id ? <EditStep pid={pid} step={s} onClose={() => setEditing(null)} /> : null}
            </div>
          ))}
        </div>
      ) : run.status === "running" ? (
        <div style={{ display: "flex", gap: 8, alignItems: "center", color: "var(--muted)", fontSize: 13 }}><Spinner /> {lastLog || "Thinking…"}</div>
      ) : null}

      {run.status === "paused" && run.error ? (
        <div style={{ display: "flex", gap: 8, alignItems: "flex-start", background: "var(--warn-soft)", color: "var(--warn)", padding: "8px 10px", borderRadius: 9, fontSize: 12.5 }}>
          <Icon.recover width={14} height={14} style={{ flexShrink: 0, marginTop: 2 }} /> <span>{run.error}</span>
        </div>
      ) : null}

      {myApprovals.length ? (
        <button className="btn" style={{ justifyContent: "center", borderColor: "var(--accent)", color: "var(--accent)", background: "var(--accent-soft)" }} onClick={onOpenApprovals}>
          <Icon.shield width={14} height={14} /> {myApprovals.length} action{myApprovals.length > 1 ? "s" : ""} waiting for your approval
        </button>
      ) : null}

      {openSugs.map((s) => (
        <div key={s.sug_id} className="fade-in" style={{ display: "flex", gap: 10, alignItems: "center", border: "1px dashed var(--info)", background: "var(--info-soft)", borderRadius: 10, padding: "8px 10px" }}>
          <Icon.sparkle width={15} height={15} style={{ color: "var(--info)", flexShrink: 0 }} />
          <div style={{ flex: 1, fontSize: 12.5 }}>{s.text}</div>
          <button className="btn btn-sm btn-primary" onClick={() => onSuggestion(s.sug_id, true)}>Accept</button>
          <button className="btn btn-sm" onClick={() => onSuggestion(s.sug_id, false)}>Dismiss</button>
        </div>
      ))}

      {isLive && plan.length && lastLog ? (
        <div style={{ fontSize: 12, color: "var(--muted)", display: "flex", gap: 6, alignItems: "center" }}><Spinner size={12} /> {lastLog}</div>
      ) : null}

      {report && !isLive && run.status !== "paused" ? (
        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 10, display: "grid", gap: 8 }}>
          <div style={{ fontSize: 13.5, lineHeight: 1.55 }}>{report.summary}</div>
          {report.artifacts?.length ? (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {Array.from(new Map(report.artifacts.map((a: any) => [a.path, a])).values()).slice(0, 10).map((a: any) => (
                <button key={a.path} className="btn btn-sm" onClick={() => api.post(`/projects/${pid}/open`, { path: a.path }).catch(() => {})}>
                  {a.kind === "spreadsheet" ? <Icon.table width={12} height={12} /> : a.path.endsWith(".eml") ? <Icon.mail width={12} height={12} /> : <Icon.doc width={12} height={12} />}
                  {fileName(a.path)}
                </button>
              ))}
            </div>
          ) : null}
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <span className="chip chip-violet"><Icon.shield width={11} height={11} /> {report.privacy?.pii_masked || 0} PII masked</span>
            <span className="chip chip-violet">{report.privacy?.regions_blurred || 0} regions blurred</span>
            <span className="chip chip-ok">0 raw PII to LLM</span>
          </div>
        </div>
      ) : null}

      {logs?.length ? (
        <div>
          <button className="btn btn-ghost btn-sm" onClick={() => setShowLog(!showLog)} style={{ color: "var(--faint)" }}>
            <Icon.list width={12} height={12} /> {showLog ? "Hide" : "Show"} activity ({logs.length})
          </button>
          {showLog ? (
            <div className="mono scroll-thin" style={{ fontSize: 11.5, maxHeight: 180, overflow: "auto", background: "var(--sunken)", borderRadius: 8, padding: 8, color: "var(--muted)" }}>
              {logs.map((l, i) => <div key={i}>{l.ts ? new Date(l.ts).toLocaleTimeString() : ""} · {l.text}</div>)}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
