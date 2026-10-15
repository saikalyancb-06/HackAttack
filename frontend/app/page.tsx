"use client";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useOperator } from "@/lib/store";
import { RunCard } from "@/components/RunCard";
import { ApprovalsTab } from "@/components/Approvals";
import { KnowledgeTab } from "@/components/Knowledge";
import { AuditTab, ReportTab, ToolsTab } from "@/components/Tabs";
import { Icon, PIIBadge, Spinner, hasPII, timeAgo } from "@/components/ui";

const EXAMPLES = [
  { t: "Internship research", q: "Find three internship opportunities suitable for a third-year computer science student, compare their eligibility criteria, save the best matches to a spreadsheet and prepare personalized draft emails for each opportunity." },
  { t: "Organize files", q: "Create a folder called notes and write notes/todo.md with a checklist for preparing for a technical interview" },
  { t: "System report", q: "Run a PowerShell command to show the free disk space on each drive and save the output to disk_space.txt" },
  { t: "Research to Word", q: "Research the top 3 open-source vector databases, compare their features and licenses, and write a short report as a Word document" },
];

type Msg = { id: number; role: string; kind: string; text: string; data: any; run_id: string | null; created_at: string };

export default function Page() {
  const [projects, setProjects] = useState<any[]>([]);
  const [pid, setPid] = useState<string | null>(null);
  const [threads, setThreads] = useState<any[]>([]);
  const [tid, setTid] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [mode, setMode] = useState<"auto" | "task" | "question">("auto");
  const [sending, setSending] = useState(false);
  const [tab, setTab] = useState("approvals");
  const [dark, setDark] = useState(false);
  const [outage, setOutage] = useState(false);
  const [status, setStatus] = useState<any>(null);
  const [auditTick, setAuditTick] = useState(0);
  const [err, setErr] = useState("");
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const { state, loadRun, loadApprovals } = useOperator(pid);

  // boot
  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
    api.get("/projects").then((ps) => {
      setProjects(ps);
      const saved = typeof window !== "undefined" ? localStorage.getItem("op-project") : null;
      setPid(ps.find((p: any) => p.id === saved)?.id || ps[0]?.id || null);
    }).catch((e) => setErr(`Backend not reachable at ${process.env.NEXT_PUBLIC_API || "http://localhost:8000"} (${e.message}). Start it with: python -m uvicorn backend.app:app --port 8000`));
    const poll = () => api.get("/status").then((s) => { setStatus(s); setOutage(!!s.outage); }).catch(() => {});
    poll();
    const t = setInterval(poll, 3000);
    return () => clearInterval(t);
  }, []);

  const loadThreads = useCallback(async (keep?: string) => {
    if (!pid) return;
    const ts = await api.get(`/projects/${pid}/threads`);
    setThreads(ts);
    setTid((cur) => keep || (ts.find((t: any) => t.id === cur) ? cur : ts[0]?.id || null));
  }, [pid]);

  useEffect(() => {
    if (!pid) return;
    try { localStorage.setItem("op-project", pid); } catch {}
    setTid(null);
    setMsgs([]);
    loadThreads();
  }, [pid, loadThreads]);

  const loadMsgs = useCallback(async () => {
    if (!pid || !tid) return;
    const m: Msg[] = await api.get(`/projects/${pid}/threads/${tid}/messages`);
    setMsgs(m);
    m.filter((x) => x.kind === "run" && x.run_id).forEach((x) => loadRun(x.run_id!));
  }, [pid, tid, loadRun]);

  useEffect(() => { loadMsgs(); }, [loadMsgs]);

  // when a run finishes, the backend adds a report message: refresh the thread & audit
  const statusKey = Object.values(state.runs).map((r: any) => r.run_id + r.status).join("|");
  useEffect(() => { setAuditTick((x) => x + 1); }, [statusKey, state.kgPulse]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [msgs.length, statusKey]);

  useEffect(() => {
    if (state.approvals.length) {
      setTab("approvals");
      if (window.innerWidth <= 1100) setRightOpen(true);
    }
  }, [state.approvals.length]);

  const toggleTheme = () => {
    const d = !dark;
    setDark(d);
    document.documentElement.classList.toggle("dark", d);
    try { localStorage.setItem("op-theme", d ? "dark" : "light"); } catch {}
  };

  const send = async (q?: string) => {
    const body = (q ?? text).trim();
    if (!body || !pid) return;
    setSending(true);
    setErr("");
    try {
      let thread = tid;
      if (!thread) {
        thread = (await api.post(`/projects/${pid}/threads`, { title: body.slice(0, 60) })).id;
        await loadThreads(thread!);
        setTid(thread);
      }
      setText("");
      const out = await api.post(`/projects/${pid}/threads/${thread}/messages`, { text: body, ...(mode !== "auto" ? { mode } : {}) });
      if (out.run_id) loadRun(out.run_id);
      const m: Msg[] = await api.get(`/projects/${pid}/threads/${thread}/messages`);
      setMsgs(m);
      loadThreads(thread!);
    } catch (e: any) {
      setErr(e.message);
    }
    setSending(false);
  };

  const runIds = useMemo(() => msgs.filter((m) => m.kind === "run" && m.run_id).map((m) => m.run_id!), [msgs]);
  const activeRunId = runIds[runIds.length - 1] || null;
  const anyLive = Object.values(state.runs).some((r: any) => r.status === "running");

  const newProject = async () => {
    const name = `Project ${projects.length + 1}`;
    const { id } = await api.post("/projects", { name });
    const ps = await api.get("/projects");
    setProjects(ps);
    setPid(id);
  };
  const newThread = async () => {
    if (!pid) return;
    const { id } = await api.post(`/projects/${pid}/threads`, { title: "New task" });
    await loadThreads(id);
    setMsgs([]);
  };
  const setOutageOn = async (on: boolean) => {
    setOutage(on);
    try { await api.post("/settings/outage", { on }); } catch {}
  };
  const stopAll = async () => {
    for (const r of Object.values(state.runs) as any[]) {
      if (["running", "paused"].includes(r.status)) await api.post(`/projects/${pid}/runs/${r.run_id}/stop`).catch(() => {});
    }
  };
  const onSuggestion = async (sid: string, accept: boolean) => {
    await api.post(`/projects/${pid}/suggestions/${sid}`, { accept }).catch(() => {});
    if (activeRunId) loadRun(activeRunId);
    runIds.forEach((r) => loadRun(r));
  };

  const project = projects.find((p) => p.id === pid);
  const thread = threads.find((t) => t.id === tid);
  const visible = msgs.filter((m) => !(m.role === "assistant" && ["report", "refusal", "clarify"].includes(m.kind) && runIds.includes(m.run_id || "")));

  return (
    <div className="app-grid">
      <div className={`backdrop ${leftOpen || rightOpen ? "open" : ""}`} onClick={() => { setLeftOpen(false); setRightOpen(false); }} />
      {/* ---------------- sidebar */}
      <aside className={`left-panel ${leftOpen ? "open" : ""}`} style={{ borderRight: "1px solid var(--border)", background: "var(--panel-2)", display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "14px 14px 10px" }}>
          <Icon.logo />
          <span className="font-serif-display" style={{ fontSize: 18, fontWeight: 600, letterSpacing: -0.2 }}>Operator</span>
        </div>
        <div style={{ padding: "0 10px" }}>
          <button className="btn" style={{ width: "100%", justifyContent: "flex-start" }} onClick={newThread}><Icon.plus /> New task</button>
        </div>
        <div style={{ padding: "14px 14px 4px", fontSize: 11, fontWeight: 700, color: "var(--faint)", textTransform: "uppercase", letterSpacing: 0.6, display: "flex", alignItems: "center" }}>
          Projects <span style={{ flex: 1 }} />
          <button className="btn btn-ghost btn-sm" style={{ padding: 2 }} onClick={newProject} title="New project"><Icon.plus width={13} height={13} /></button>
        </div>
        <div style={{ padding: "0 8px", display: "grid", gap: 1 }}>
          {projects.map((p) => (
            <button key={p.id} onClick={() => setPid(p.id)} className="btn btn-ghost" style={{ justifyContent: "flex-start", background: p.id === pid ? "var(--panel)" : undefined, boxShadow: p.id === pid ? "inset 0 0 0 1px var(--border)" : undefined }}>
              <Icon.folder width={14} height={14} style={{ color: "var(--muted)" }} />
              <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{p.name}</span>
            </button>
          ))}
        </div>
        <div style={{ padding: "14px 14px 4px", fontSize: 11, fontWeight: 700, color: "var(--faint)", textTransform: "uppercase", letterSpacing: 0.6 }}>Threads</div>
        <div className="scroll-thin" style={{ flex: 1, overflow: "auto", padding: "0 8px 12px", display: "grid", gap: 1, alignContent: "start" }}>
          {threads.map((t) => (
            <button key={t.id} onClick={() => { setTid(t.id); setLeftOpen(false); }} className="btn btn-ghost" style={{ justifyContent: "flex-start", textAlign: "left", height: "auto", padding: "7px 10px", background: t.id === tid ? "var(--panel)" : undefined, boxShadow: t.id === tid ? "inset 0 0 0 1px var(--border)" : undefined }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 196, fontWeight: 500 }}>{t.title}</div>
                <div style={{ fontSize: 11, color: "var(--faint)" }}>{timeAgo(t.updated_at)}</div>
              </div>
            </button>
          ))}
        </div>
        <div style={{ borderTop: "1px solid var(--border)", padding: 10, display: "grid", gap: 6, fontSize: 11.5, color: "var(--muted)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 7, height: 7, borderRadius: 9, background: status?.llm?.available ? "var(--ok)" : "var(--warn)" }} />
            {status?.llm?.available ? `Groq · ${status.llm.model.replace("openai/", "")}${status.llm.keys > 1 ? ` · ${status.llm.keys} keys` : ""}` : "LLM offline · rule fallback"}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 7, height: 7, borderRadius: 9, background: status?.mcp?.office_com?.connected ? "var(--ok)" : "var(--bad)" }} />
            MCP office_com {status?.mcp?.office_com?.connected ? `· ${status.mcp.office_com.tools} tools` : "· offline"}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 7, height: 7, borderRadius: 9, background: state.connected ? "var(--ok)" : "var(--bad)" }} />
            {state.connected ? "Live events connected" : "Reconnecting…"}
          </div>
        </div>
      </aside>

      {/* ---------------- center */}
      <main style={{ display: "flex", flexDirection: "column", minWidth: 0, minHeight: 0 }}>
        <header style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 18px", borderBottom: "1px solid var(--border)", background: "var(--bg)" }}>
          <button className="btn btn-ghost drawer-btn left" onClick={() => setLeftOpen(true)} title="Projects & threads"><Icon.list /></button>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontSize: 11.5, color: "var(--faint)" }}>{project?.name || "—"}</div>
            <div style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{thread?.title || "New task"}</div>
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: outage ? "var(--bad)" : "var(--muted)", cursor: "pointer", padding: "5px 10px", borderRadius: 9, border: `1px solid ${outage ? "var(--bad)" : "var(--border)"}`, background: outage ? "var(--bad-soft)" : "var(--panel)" }} onClick={() => setOutageOn(!outage)}>
            <Icon.bolt width={14} height={14} /> <span className="hide-narrow">Simulate Excel outage</span><span style={{ whiteSpace: "nowrap" }} className="drawer-btn left">Outage</span>
            <span className={`toggle ${outage ? "toggle-on" : ""}`} />
          </label>
          <button className="btn" disabled={!anyLive} onClick={stopAll} title="Stop every running task"><Icon.stop width={13} height={13} /> <span className="hide-narrow">Stop all</span></button>
          <button className="btn drawer-btn right" onClick={() => setRightOpen(true)} title="Approvals, knowledge, audit, report, tools">
            <Icon.shield width={14} height={14} />{state.approvals.length ? <span className="chip chip-accent" style={{ padding: "0 6px", lineHeight: "16px" }}>{state.approvals.length}</span> : null}
          </button>
          <button className="btn btn-ghost" onClick={toggleTheme} title="Toggle theme">{dark ? <Icon.sun /> : <Icon.moon />}</button>
        </header>

        <div ref={scroller} className="scroll-thin" style={{ flex: 1, overflow: "auto" }}>
          <div style={{ maxWidth: 820, margin: "0 auto", padding: "22px 20px 30px", display: "grid", gap: 16 }}>
            {err ? <div className="card" style={{ padding: 12, borderColor: "var(--bad)", color: "var(--bad)", fontSize: 13 }}>{err}</div> : null}
            {!visible.length && !err ? (
              <div style={{ paddingTop: "8vh", textAlign: "center" }}>
                <div className="font-serif-display" style={{ fontSize: 32, fontWeight: 600, letterSpacing: -0.5 }}>What should I get done?</div>
                <div style={{ color: "var(--muted)", marginTop: 8, fontSize: 14 }}>
                  I check the intent and feasibility, plan, act across web, Excel, Word, email drafts, files and apps, recover from failures and verify every result.
                </div>
                <div className="examples" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 26, textAlign: "left" }}>
                  {EXAMPLES.map((e) => (
                    <button key={e.t} className="card" style={{ padding: 14, cursor: "pointer", textAlign: "left" }} onClick={() => send(e.q)}>
                      <div style={{ fontWeight: 600, fontSize: 13.5 }}>{e.t}</div>
                      <div style={{ color: "var(--muted)", fontSize: 12.5, marginTop: 4, lineHeight: 1.45 }}>{e.q.slice(0, 110)}{e.q.length > 110 ? "…" : ""}</div>
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
            {visible.map((m) => {
              if (m.role === "user") {
                return (
                  <div key={m.id} className="fade-in" style={{ justifySelf: "end", maxWidth: "82%" }}>
                    <div style={{ background: "var(--panel)", border: "1px solid var(--border)", borderRadius: "14px 14px 4px 14px", padding: "10px 14px", fontSize: 14, lineHeight: 1.5, whiteSpace: "pre-wrap" }}>{m.text}</div>
                    <div style={{ display: "flex", gap: 6, justifyContent: "flex-end", marginTop: 4 }}>
                      <span className="chip chip-muted">{m.kind === "question" ? "question" : "task"}</span>
                      {hasPII(m.text) ? <PIIBadge /> : null}
                    </div>
                  </div>
                );
              }
              if (m.kind === "run" && m.run_id) {
                return (
                  <RunCard key={m.id} pid={pid!} run={state.runs[m.run_id]} logs={state.logs[m.run_id] || []} report={state.reports[m.run_id]}
                    suggestions={state.suggestions[m.run_id] || []} approvals={state.approvals}
                    onOpenApprovals={() => setTab("approvals")} onSuggestion={onSuggestion} />
                );
              }
              return (
                <div key={m.id} className="fade-in" style={{ display: "flex", gap: 10 }}>
                  <Icon.logo />
                  <div style={{ flex: 1, fontSize: 14, lineHeight: 1.55, whiteSpace: "pre-wrap" }}>
                    {m.text}
                    {m.data?.sources?.length ? <div style={{ fontSize: 11, color: "var(--faint)", marginTop: 4 }}>from knowledge: {m.data.sources.join(", ")}</div> : null}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div style={{ padding: "0 20px 18px" }}>
          <div className="card" style={{ maxWidth: 820, margin: "0 auto", padding: 10 }}>
            <textarea className="scroll-thin" value={text} onChange={(e) => setText(e.target.value)} rows={2}
              placeholder="Describe a goal, or ask about this project…"
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
              style={{ width: "100%", resize: "none", border: "none", outline: "none", background: "transparent", color: "var(--text)", fontSize: 14, lineHeight: 1.5, padding: "4px 6px" }} />
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              {(["auto", "task", "question"] as const).map((m) => (
                <button key={m} className={`tab ${mode === m ? "tab-active" : ""}`} onClick={() => setMode(m)}>{m === "auto" ? "Auto" : m === "task" ? "Do a task" : "Ask"}</button>
              ))}
              <span style={{ flex: 1 }} />
              <span style={{ fontSize: 11, color: "var(--faint)" }}>PII is masked before anything leaves this PC</span>
              <button className="btn btn-primary" disabled={sending || !text.trim()} onClick={() => send()}>{sending ? <Spinner /> : <Icon.send />}</button>
            </div>
          </div>
        </div>
      </main>

      {/* ---------------- right panel */}
      <aside className={`right-panel ${rightOpen ? "open" : ""}`} style={{ borderLeft: "1px solid var(--border)", background: "var(--panel)", display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ display: "flex", gap: 2, padding: "10px 10px 8px", borderBottom: "1px solid var(--border)", flexWrap: "wrap" }}>
          {[
            ["approvals", "Approvals", <Icon.shield key="a" width={13} height={13} />],
            ["knowledge", "Knowledge", <Icon.graph key="k" width={13} height={13} />],
            ["audit", "Audit", <Icon.list key="u" width={13} height={13} />],
            ["report", "Report", <Icon.report key="r" width={13} height={13} />],
            ["tools", "Tools", <Icon.plug key="t" width={13} height={13} />],
          ].map(([k, label, icon]: any) => (
            <button key={k} className={`tab ${tab === k ? "tab-active" : ""}`} onClick={() => setTab(k)}>
              {icon}{label}
              {k === "approvals" && state.approvals.length ? <span className="chip chip-accent" style={{ padding: "0 6px", lineHeight: "16px" }}>{state.approvals.length}</span> : null}
            </button>
          ))}
        </div>
        <div className="scroll-thin" style={{ flex: 1, overflow: "auto", padding: 14 }}>
          {!pid ? null : tab === "approvals" ? (
            <ApprovalsTab pid={pid} approvals={state.approvals} reload={() => { loadApprovals(); runIds.forEach((r) => loadRun(r)); }} />
          ) : tab === "knowledge" ? (
            <KnowledgeTab pid={pid} graph={state.graph} />
          ) : tab === "audit" ? (
            <AuditTab pid={pid} runId={activeRunId} tick={auditTick} />
          ) : tab === "report" ? (
            <ReportTab pid={pid} runId={activeRunId} report={activeRunId ? state.reports[activeRunId] : null} run={activeRunId ? state.runs[activeRunId] : null} />
          ) : (
            <ToolsTab pid={pid} tick={auditTick} />
          )}
        </div>
      </aside>
    </div>
  );
}
