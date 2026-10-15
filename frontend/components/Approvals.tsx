"use client";
import React, { useState } from "react";
import { api } from "@/lib/api";
import { Empty, Icon, Json, RiskChip, fileName } from "./ui";

function Field({ k, v }: { k: string; v: any }) {
  const long = typeof v === "string" && v.length > 120;
  return (
    <div style={{ display: "grid", gridTemplateColumns: long ? "1fr" : "92px 1fr", gap: long ? 3 : 8, fontSize: 12.5, padding: "3px 0" }}>
      <div style={{ color: "var(--faint)", fontWeight: 600 }}>{k}</div>
      {typeof v === "string" ? (
        <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: long ? 160 : undefined, overflow: "auto", background: long ? "var(--sunken)" : undefined, padding: long ? 8 : 0, borderRadius: 7 }} className="scroll-thin">{v}</div>
      ) : (
        <Json value={v} max={1500} />
      )}
    </div>
  );
}

function Preview({ summary }: { summary: any }) {
  const args = summary.args;
  const list = Array.isArray(args) ? args : [args];
  const tool: string = summary.tool || "";
  if (tool.includes("outlook_create_draft") || tool.includes("outlook_send")) {
    return (
      <div style={{ display: "grid", gap: 8 }}>
        {list.slice(0, 6).map((a: any, i: number) => (
          <div key={i} style={{ border: "1px solid var(--border)", borderRadius: 9, padding: 8 }}>
            <Field k="To" v={a?.to} />
            <Field k="Subject" v={a?.subject} />
            <Field k="Body" v={a?.body} />
          </div>
        ))}
        {list.length > 6 ? <div style={{ fontSize: 12, color: "var(--muted)" }}>+{list.length - 6} more</div> : null}
      </div>
    );
  }
  if (tool.includes("excel_upsert_rows")) {
    const a = list[0] || {};
    const rows = a.rows || [];
    const cols = rows.length ? Object.keys(rows[0]).slice(0, 6) : [];
    return (
      <div>
        <Field k="Workbook" v={`${fileName(a.path)} › ${a.sheet}`} />
        <Field k="Match on" v={a.key_column} />
        <div className="scroll-thin" style={{ overflow: "auto", marginTop: 6, border: "1px solid var(--border)", borderRadius: 8 }}>
          <table style={{ borderCollapse: "collapse", fontSize: 11.5, width: "100%" }}>
            <thead><tr>{cols.map((c) => <th key={c} style={{ textAlign: "left", padding: "5px 7px", background: "var(--panel-2)", borderBottom: "1px solid var(--border)" }}>{c}</th>)}</tr></thead>
            <tbody>{rows.slice(0, 8).map((r: any, i: number) => <tr key={i}>{cols.map((c) => <td key={c} style={{ padding: "5px 7px", borderBottom: "1px solid var(--border)", maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{String(r[c] ?? "")}</td>)}</tr>)}</tbody>
          </table>
        </div>
      </div>
    );
  }
  return <div>{list.map((a: any, i: number) => (a && typeof a === "object" ? Object.entries(a).map(([k, v]) => <Field key={k + i} k={k} v={v} />) : <Field key={i} k="args" v={a} />))}</div>;
}

export function ApprovalCard({ pid, ap, onDone }: { pid: string; ap: any; onDone: () => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(() => JSON.stringify(Array.isArray(ap.summary?.args) ? ap.summary.args[0] : ap.summary?.args, null, 2));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const s = ap.summary || {};
  const kind = ap.kind;
  const danger = kind === "delete" || s.risk === "irreversible";
  const decide = async (decision: string, edited?: any) => {
    setBusy(true);
    setErr("");
    try {
      await api.post(`/projects/${pid}/approvals/${ap.approval_id}`, { decision, edited_args: edited });
      onDone();
    } catch (e: any) {
      setErr(e.message);
    }
    setBusy(false);
  };
  const border = danger ? "var(--bad)" : kind === "mcp_install" ? "var(--info)" : "var(--accent)";
  return (
    <div className="card fade-in" style={{ padding: 12, borderColor: border, boxShadow: `0 0 0 3px ${danger ? "var(--bad-soft)" : kind === "mcp_install" ? "var(--info-soft)" : "var(--accent-soft)"}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        {kind === "delete" ? <Icon.recycle style={{ color: "var(--bad)" }} /> : kind === "command" ? <Icon.terminal /> : kind === "mcp_install" ? <Icon.plug style={{ color: "var(--info)" }} /> : <Icon.shield style={{ color: "var(--accent)" }} />}
        <div style={{ fontWeight: 650, flex: 1 }}>
          {kind === "delete" ? "Delete files (to Recycle Bin)" : kind === "command" ? "Run a command" : kind === "mcp_install" ? "Install MCP server" : "Approve action"}
        </div>
        {s.risk ? <RiskChip risk={s.risk} /> : null}
      </div>
      {kind !== "mcp_install" ? <div style={{ fontSize: 13, marginBottom: 8 }}>{s.description}</div> : null}
      {s.reason ? <div style={{ fontSize: 12, color: "var(--warn)", marginBottom: 6 }}>{s.reason}</div> : null}

      {kind === "command" ? (
        <div style={{ display: "grid", gap: 6 }}>
          <pre className="mono" style={{ margin: 0, padding: 10, borderRadius: 8, background: "#151412", color: "#f0efe9", fontSize: 12.5, whiteSpace: "pre-wrap" }}>{s.command}</pre>
          <div style={{ fontSize: 12, color: "var(--muted)" }}>{s.shell} · cwd {s.cwd}</div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {s.guard?.flagged ? <span className="chip chip-bad">safety model flagged</span> : <span className="chip chip-ok">safety model: ok</span>}
            {(s.guard?.reasons || []).map((r: string, i: number) => <span key={i} className="chip chip-muted">{r}</span>)}
          </div>
        </div>
      ) : kind === "delete" ? (
        <div style={{ display: "grid", gap: 4 }}>
          {(s.paths || []).map((p: string) => <div key={p} className="mono" style={{ fontSize: 12, color: "var(--bad)", wordBreak: "break-all" }}>🗑 {p}</div>)}
          <div style={{ fontSize: 12, color: "var(--muted)" }}>Files go to the Recycle Bin and can be restored.</div>
        </div>
      ) : kind === "mcp_install" ? (
        <div style={{ display: "grid", gap: 6 }}>
          <div style={{ fontWeight: 600 }}>{s.server}</div>
          <div style={{ fontSize: 12.5, color: "var(--muted)" }}>{s.description}</div>
          {s.repo ? <a href={s.repo} target="_blank" rel="noreferrer" style={{ fontSize: 12, color: "var(--info)" }}>{s.repo}</a> : null}
          <pre className="mono" style={{ margin: 0, padding: 10, borderRadius: 8, background: "#151412", color: "#f0efe9", fontSize: 12.5 }}>{s.install_command}</pre>
        </div>
      ) : editing ? (
        <textarea className="input mono" style={{ minHeight: 160, fontSize: 12 }} value={text} onChange={(e) => setText(e.target.value)} />
      ) : (
        <Preview summary={s} />
      )}

      {err ? <div style={{ color: "var(--bad)", fontSize: 12, marginTop: 6 }}>{err}</div> : null}
      <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
        {kind === "mcp_install" ? (
          <>
            <button className="btn btn-primary" disabled={busy} onClick={() => decide("approve")}>Install</button>
            <button className="btn" disabled={busy} onClick={() => decide("reject")}>Not now</button>
          </>
        ) : editing ? (
          <>
            <button className="btn btn-primary" disabled={busy} onClick={() => {
              try { decide("edit", JSON.parse(text)); } catch { setErr("Arguments must be valid JSON"); }
            }}>Approve edited</button>
            <button className="btn" onClick={() => setEditing(false)}>Cancel</button>
          </>
        ) : (
          <>
            <button className={`btn ${danger ? "btn-danger" : "btn-primary"}`} disabled={busy} onClick={() => decide("approve")}><Icon.check width={14} height={14} />{danger ? "Approve delete" : "Approve"}</button>
            {!Array.isArray(s.args) && kind !== "delete" ? <button className="btn" disabled={busy} onClick={() => setEditing(true)}><Icon.edit width={13} height={13} />Edit</button> : null}
            <button className="btn" disabled={busy} onClick={() => decide("reject")}><Icon.x width={13} height={13} />Reject</button>
          </>
        )}
      </div>
    </div>
  );
}

export function ApprovalsTab({ pid, approvals, reload }: { pid: string; approvals: any[]; reload: () => void }) {
  if (!approvals.length) return <Empty icon={<Icon.shield />} title="Nothing waiting for you" hint="Sends, deletes, risky commands and installs always stop here first." />;
  return <div style={{ display: "grid", gap: 10 }}>{approvals.map((a) => <ApprovalCard key={a.approval_id} pid={pid} ap={a} onDone={reload} />)}</div>;
}
