"use client";
import React from "react";
import { PII_RE } from "@/lib/api";

const P = { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

export const Icon = {
  plus: (p: any = {}) => <svg {...P} {...p}><path d="M12 5v14M5 12h14" /></svg>,
  send: (p: any = {}) => <svg {...P} {...p}><path d="M5 12h14M13 6l6 6-6 6" /></svg>,
  stop: (p: any = {}) => <svg {...P} {...p}><rect x="6" y="6" width="12" height="12" rx="2" /></svg>,
  pause: (p: any = {}) => <svg {...P} {...p}><path d="M9 5v14M15 5v14" /></svg>,
  play: (p: any = {}) => <svg {...P} {...p}><path d="M7 5l12 7-12 7z" /></svg>,
  sun: (p: any = {}) => <svg {...P} {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>,
  moon: (p: any = {}) => <svg {...P} {...p}><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></svg>,
  folder: (p: any = {}) => <svg {...P} {...p}><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></svg>,
  chat: (p: any = {}) => <svg {...P} {...p}><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" /></svg>,
  globe: (p: any = {}) => <svg {...P} {...p}><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" /></svg>,
  table: (p: any = {}) => <svg {...P} {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 10h18M9 4v16" /></svg>,
  brain: (p: any = {}) => <svg {...P} {...p}><path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 1V5a3 3 0 0 0-3-1zM15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 1" /></svg>,
  terminal: (p: any = {}) => <svg {...P} {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M7 9l3 3-3 3M13 15h4" /></svg>,
  window: (p: any = {}) => <svg {...P} {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 9h18M7 6.5h.01M10 6.5h.01" /></svg>,
  graph: (p: any = {}) => <svg {...P} {...p}><circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="8" r="2.5" /><circle cx="10" cy="18" r="2.5" /><path d="M8 7l7.5 1M7 8.5l2.3 7.2M16.3 10l-4.6 6" /></svg>,
  shield: (p: any = {}) => <svg {...P} {...p}><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" /></svg>,
  check: (p: any = {}) => <svg {...P} {...p}><path d="M5 12l5 5 9-10" /></svg>,
  x: (p: any = {}) => <svg {...P} {...p}><path d="M6 6l12 12M18 6L6 18" /></svg>,
  alert: (p: any = {}) => <svg {...P} {...p}><path d="M12 3l9 16H3z" /><path d="M12 10v4M12 17h.01" /></svg>,
  clock: (p: any = {}) => <svg {...P} {...p}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>,
  list: (p: any = {}) => <svg {...P} {...p}><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" /></svg>,
  report: (p: any = {}) => <svg {...P} {...p}><path d="M7 3h7l5 5v13H7z" /><path d="M14 3v5h5M10 13h6M10 17h6" /></svg>,
  plug: (p: any = {}) => <svg {...P} {...p}><path d="M9 7V3M15 7V3M7 7h10v4a5 5 0 0 1-10 0zM12 16v5" /></svg>,
  mail: (p: any = {}) => <svg {...P} {...p}><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 7l9 6 9-6" /></svg>,
  doc: (p: any = {}) => <svg {...P} {...p}><path d="M7 3h7l5 5v13H7z" /><path d="M14 3v5h5" /></svg>,
  recycle: (p: any = {}) => <svg {...P} {...p}><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" /></svg>,
  chevron: (p: any = {}) => <svg {...P} {...p}><path d="M9 6l6 6-6 6" /></svg>,
  sparkle: (p: any = {}) => <svg {...P} {...p}><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /></svg>,
  bolt: (p: any = {}) => <svg {...P} {...p}><path d="M13 2L4 14h7l-1 8 9-12h-7z" /></svg>,
  open: (p: any = {}) => <svg {...P} {...p}><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></svg>,
  eye: (p: any = {}) => <svg {...P} {...p}><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></svg>,
  edit: (p: any = {}) => <svg {...P} {...p}><path d="M4 20h4L19 9l-4-4L4 16z" /></svg>,
  recover: (p: any = {}) => <svg {...P} {...p}><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5" /></svg>,
  logo: (p: any = {}) => (
    <svg width="22" height="22" viewBox="0 0 24 24" {...p}>
      <circle cx="12" cy="12" r="10" fill="var(--accent)" />
      <path d="M8 12.5l2.6 2.6L16.5 9" stroke="#fff" strokeWidth="2.2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
};

export function Spinner({ size = 14 }: { size?: number }) {
  return (
    <svg className="spin" width={size} height={size} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.2" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export const STATUS: Record<string, { label: string; cls: string }> = {
  pending: { label: "Pending", cls: "chip-muted" },
  running: { label: "Running", cls: "chip-info" },
  done: { label: "Done", cls: "chip-ok" },
  reused: { label: "Reused", cls: "chip-violet" },
  failed: { label: "Failed", cls: "chip-bad" },
  degraded: { label: "Degraded", cls: "chip-warn" },
  awaiting_approval: { label: "Needs approval", cls: "chip-accent" },
  rejected: { label: "Rejected", cls: "chip-bad" },
  skipped: { label: "Skipped", cls: "chip-muted" },
  paused: { label: "Paused", cls: "chip-warn" },
  partial: { label: "Partial", cls: "chip-warn" },
  stopped: { label: "Stopped", cls: "chip-muted" },
  refused: { label: "Refused", cls: "chip-bad" },
  needs_input: { label: "Needs input", cls: "chip-warn" },
};

export function StatusChip({ status }: { status: string }) {
  const s = STATUS[status] || { label: status, cls: "chip-muted" };
  return (
    <span className={`chip ${s.cls}`}>
      {status === "running" ? <span className="pulse-dot" style={{ width: 6, height: 6, borderRadius: 9, background: "currentColor" }} /> : null}
      {s.label}
    </span>
  );
}

export function RouteIcon({ route, tool }: { route?: string; tool?: string }) {
  const t = tool || "";
  if (t.includes("excel")) return <Icon.table />;
  if (t.includes("outlook")) return <Icon.mail />;
  if (t.includes("word")) return <Icon.doc />;
  if (t === "files.delete") return <Icon.recycle />;
  switch (route) {
    case "browser": return <Icon.globe />;
    case "think": return <Icon.brain />;
    case "shell": return <Icon.terminal />;
    case "windows": return <Icon.window />;
    case "kg": return <Icon.graph />;
    case "mcp": return <Icon.plug />;
    default: return <Icon.bolt />;
  }
}

export function RiskChip({ risk }: { risk: string }) {
  if (risk === "irreversible") return <span className="chip chip-bad">irreversible</span>;
  if (risk === "write") return <span className="chip chip-warn">write</span>;
  return <span className="chip chip-muted">read</span>;
}

export function hasPII(v: any): boolean {
  try {
    return PII_RE.test(typeof v === "string" ? v : JSON.stringify(v));
  } catch {
    return false;
  }
}

/** Text with every masked value (<EMAIL_1>, [REDACTED_PHONE] ...) drawn as a red box saying it is redacted. */
export function Redacted({ text }: { text: string }) {
  if (!text) return null;
  const re = /<(EMAIL|PHONE|AADHAAR|PAN|CARD|UPI|SECRET|PASSWORD)_\d+>|\[REDACTED_([A-Z_]+)\]/g;
  const out: React.ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const kind = (m[1] || m[2] || "DATA").toLowerCase().replace(/_/g, " ");
    out.push(
      <span key={m.index} title="Personal data: redacted before it reached the agent or the LLM"
        style={{ display: "inline-flex", alignItems: "center", gap: 3, border: "1.5px dashed var(--bad)", background: "var(--bad-soft)",
                 color: "var(--bad)", borderRadius: 5, padding: "0 5px", margin: "0 1px", fontSize: "0.88em", fontWeight: 600, whiteSpace: "nowrap" }}>
        🔒 {kind} redacted
      </span>
    );
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return <>{out}</>;
}

export function PIIBadge() {
  return (
    <span className="chip chip-violet" title="Personal data is replaced by placeholders before it reaches the LLM, the ledger or this screen">
      <Icon.shield width={11} height={11} /> PII masked
    </span>
  );
}

export function fileName(p: string) {
  return (p || "").replace(/\\/g, "/").split("/").pop();
}

export function timeAgo(iso?: string) {
  if (!iso) return "";
  const d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}

export function Json({ value, max = 4000 }: { value: any; max?: number }) {
  let s = "";
  try {
    s = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  } catch {
    s = String(value);
  }
  if (s.length > max) s = s.slice(0, max) + "\n…";
  return (
    <pre className="mono scroll-thin" style={{ fontSize: 11.5, lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 280, overflow: "auto", background: "var(--sunken)", borderRadius: 8, padding: 10, margin: 0, color: "var(--muted)" }}>
      {s}
    </pre>
  );
}

export function Empty({ icon, title, hint }: { icon?: React.ReactNode; title: string; hint?: string }) {
  return (
    <div style={{ textAlign: "center", padding: "36px 16px", color: "var(--muted)" }}>
      <div style={{ display: "inline-flex", padding: 10, borderRadius: 12, background: "var(--panel-2)", marginBottom: 10 }}>{icon}</div>
      <div style={{ fontWeight: 600, color: "var(--text)" }}>{title}</div>
      {hint ? <div style={{ fontSize: 12.5, marginTop: 4 }}>{hint}</div> : null}
    </div>
  );
}
