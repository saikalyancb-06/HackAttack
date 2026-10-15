"use client";
import React, { useState } from "react";
import { api } from "@/lib/api";
import { extensionReady, extRequest } from "@/lib/extension";
import { Icon, Spinner } from "./ui";

const AGENT_CHOICE = "__agent_choice__";

/** Questions the agent asks before acting on an underspecified task. Every question can be left to the agent. */
export function ClarifyCard({ pid, runId, questions, onDone }: { pid: string; runId: string; questions: any[]; onDone: () => void }) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const set = (id: string, v: string) => setAnswers((a) => ({ ...a, [id]: v }));
  const submit = async (all?: Record<string, string>) => {
    setBusy(true);
    setErr("");
    try {
      const body = Object.fromEntries(questions.map((q) => [q.id, (all || answers)[q.id]?.trim() || AGENT_CHOICE]));
      const r = await api.post(`/projects/${pid}/runs/${runId}/clarify`, { answers: body });
      if (r.ok === false) throw new Error(r.error);
      onDone();
    } catch (e: any) {
      setErr(e.message);
    }
    setBusy(false);
  };
  return (
    <div className="fade-in" style={{ border: "1.5px solid var(--info)", background: "var(--info-soft)", borderRadius: 12, padding: 14, display: "grid", gap: 12 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <Icon.chat width={15} height={15} style={{ color: "var(--info)" }} />
        <b style={{ fontSize: 13.5 }}>A few details before I start</b>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>— answer what matters, leave the rest to me</span>
      </div>
      {questions.map((q) => {
        const v = answers[q.id] || "";
        const mine = v === AGENT_CHOICE;
        return (
          <div key={q.id} style={{ display: "grid", gap: 6 }}>
            <div style={{ fontSize: 13.5, fontWeight: 600 }}>{q.question}</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {(q.options || []).map((o: string) => (
                <button key={o} className="btn btn-sm" onClick={() => set(q.id, o)}
                  style={v === o ? { background: "var(--info)", color: "#fff", borderColor: "var(--info)" } : undefined}>{o}</button>
              ))}
              <button className="btn btn-sm" onClick={() => set(q.id, mine ? "" : AGENT_CHOICE)}
                style={mine ? { background: "var(--accent)", color: "#fff", borderColor: "var(--accent)" } : undefined}>
                <Icon.sparkle width={11} height={11} /> Your choice
              </button>
            </div>
            {!mine ? <input className="input" placeholder="Or type your answer…" value={v && !(q.options || []).includes(v) ? v : ""}
              onChange={(e) => set(q.id, e.target.value)} /> : null}
          </div>
        );
      })}
      {err ? <div style={{ color: "var(--bad)", fontSize: 12.5 }}>{err}</div> : null}
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="btn" disabled={busy} onClick={() => submit(Object.fromEntries(questions.map((q) => [q.id, AGENT_CHOICE])))}>
          You decide everything
        </button>
        <button className="btn btn-primary" disabled={busy} onClick={() => submit()}>{busy ? <Spinner /> : <Icon.send width={13} height={13} />} Continue</button>
      </div>
    </div>
  );
}

/** Live view of a web step running in its own browser tab. */
export function BrowserCard({ b }: { b: any }) {
  if (!b) return null;
  const st = b.state || {};
  const turn = st.waitingForUser;
  const phase = b.phase;
  const color = phase === "user_turn" ? "var(--info)" : phase === "ended" ? (b.end?.outcome === "SUCCESS" ? "var(--ok)" : "var(--warn)") : phase === "no_extension" || phase === "error" ? "var(--bad)" : "var(--accent)";
  return (
    <div className="fade-in" style={{ border: `1.5px solid ${color}`, borderRadius: 12, padding: 12, display: "grid", gap: 8,
      boxShadow: phase === "running" || phase === "opening" ? `0 0 0 3px var(--accent-soft)` : undefined }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <Icon.globe width={15} height={15} style={{ color }} />
        <b style={{ fontSize: 13.5 }}>
          {phase === "opening" ? "Opening a new tab…" : phase === "running" ? "Working live in the browser tab" : phase === "user_turn" ? "Your turn in the browser tab"
            : phase === "ended" ? `Browser task ${b.end?.outcome === "SUCCESS" ? "finished" : "ended"}` : "Browser extension needed"}
        </b>
        {phase === "running" || phase === "opening" ? <Spinner size={13} /> : null}
        <span style={{ flex: 1 }} />
        {st.redactedCount ? <span className="chip chip-violet"><Icon.shield width={11} height={11} /> {st.redactedCount} redacted from agent</span> : null}
        {extensionReady() && phase !== "no_extension" ? (
          <button className="btn btn-sm" onClick={() => extRequest("FOCUS_TAB")}><Icon.open width={12} height={12} /> Show tab</button>
        ) : null}
      </div>
      {phase === "running" && st.currentAction ? (
        <div className="mono" style={{ fontSize: 12, color: "var(--muted)", wordBreak: "break-all" }}>
          step {st.step}/{st.maxSteps} · {st.currentAction}{st.currentUrl ? ` · ${st.currentUrl}` : ""}
        </div>
      ) : null}
      {turn ? (
        <div style={{ background: "var(--info-soft)", borderRadius: 10, padding: 10, display: "grid", gap: 8 }}>
          <div style={{ fontSize: 13.5, lineHeight: 1.5 }}>{turn.question}</div>
          <div style={{ fontSize: 12, color: "var(--muted)" }}>
            Type it yourself in the browser tab (the field is highlighted). Sensitive data you type is boxed in red there and redacted from the agent.
          </div>
          <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
            <button className="btn btn-sm" onClick={() => extRequest("FOCUS_TAB")}>Go to the tab</button>
            <button className="btn btn-sm btn-primary" onClick={() => extRequest("USER_DONE")}><Icon.check width={12} height={12} /> I'm done — continue</button>
          </div>
        </div>
      ) : null}
      {phase === "ended" && b.end ? (
        <div style={{ fontSize: 12.5, color: "var(--muted)" }}>{b.end.reason || b.end.status} {b.end.finalTitle ? `· ${b.end.finalTitle}` : ""}</div>
      ) : null}
      {phase === "no_extension" || phase === "error" ? (
        <div style={{ fontSize: 12.5, lineHeight: 1.55 }}>
          {b.error ? <div style={{ color: "var(--bad)" }}>{b.error}</div> : null}
          Install the Operator browser extension once: open <span className="mono">chrome://extensions</span>, turn on Developer mode, click
          “Load unpacked” and pick <span className="mono">E:\hackattackamc\browser-extension</span>. Then reload this page and press Resume.
        </div>
      ) : null}
    </div>
  );
}
