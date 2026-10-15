"use client";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Empty, Icon, Spinner, fileName } from "./ui";

const COLORS: Record<string, string> = {
  Task: "#c96442", Step: "#8a857b", Artifact: "#2f7d4f", Entity: "#2c5fa8", Fact: "#a86a00", Workflow: "#6b4fbf",
};

type N = { id: string; type: string; label: string; x: number; y: number; vx: number; vy: number; props?: any };

function ForceGraph({ graph, onSelect, selected }: { graph: { nodes: any[]; edges: any[] }; onSelect: (n: any) => void; selected: string | null }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const nodesRef = useRef<Map<string, N>>(new Map());
  const [hover, setHover] = useState<N | null>(null);
  const size = useRef({ w: 400, h: 300 });

  useEffect(() => {
    const m = nodesRef.current;
    const ids = new Set(graph.nodes.map((n) => n.id));
    for (const k of Array.from(m.keys())) if (!ids.has(k)) m.delete(k);
    graph.nodes.forEach((n, i) => {
      const cur = m.get(n.id);
      if (cur) Object.assign(cur, { type: n.type, label: n.label, props: n.props });
      else {
        const a = (i / Math.max(1, graph.nodes.length)) * Math.PI * 2;
        m.set(n.id, { id: n.id, type: n.type, label: n.label, props: n.props, x: size.current.w / 2 + Math.cos(a) * 80 + Math.random() * 10, y: size.current.h / 2 + Math.sin(a) * 80, vx: 0, vy: 0 });
      }
    });
  }, [graph]);

  useEffect(() => {
    let raf = 0;
    let alive = true;
    const c = ref.current!;
    const ctx = c.getContext("2d")!;
    const tick = () => {
      if (!alive) return;
      const dpr = window.devicePixelRatio || 1;
      const rect = c.parentElement!.getBoundingClientRect();
      const w = rect.width, h = 300;
      size.current = { w, h };
      if (c.width !== w * dpr) { c.width = w * dpr; c.height = h * dpr; c.style.width = w + "px"; c.style.height = h + "px"; }
      const nodes = Array.from(nodesRef.current.values());
      const edges = graph.edges.filter((e) => nodesRef.current.has(e.src) && nodesRef.current.has(e.dst));
      // physics
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j];
          let dx = a.x - b.x, dy = a.y - b.y;
          const d2 = Math.max(60, dx * dx + dy * dy);
          const f = 900 / d2;
          dx *= f / Math.sqrt(d2); dy *= f / Math.sqrt(d2);
          a.vx += dx; a.vy += dy; b.vx -= dx; b.vy -= dy;
        }
      }
      edges.forEach((e) => {
        const a = nodesRef.current.get(e.src)!, b = nodesRef.current.get(e.dst)!;
        const dx = b.x - a.x, dy = b.y - a.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = (d - 55) * 0.012;
        a.vx += (dx / d) * f; a.vy += (dy / d) * f; b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
      });
      nodes.forEach((n) => {
        n.vx += (w / 2 - n.x) * 0.0025; n.vy += (h / 2 - n.y) * 0.0035;
        n.vx *= 0.82; n.vy *= 0.82;
        n.x = Math.min(w - 10, Math.max(10, n.x + n.vx)); n.y = Math.min(h - 10, Math.max(10, n.y + n.vy));
      });
      // draw
      const css = getComputedStyle(document.documentElement);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      ctx.strokeStyle = css.getPropertyValue("--border-strong");
      ctx.lineWidth = 1;
      edges.forEach((e) => {
        const a = nodesRef.current.get(e.src)!, b = nodesRef.current.get(e.dst)!;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      });
      nodes.forEach((n) => {
        const r = n.type === "Task" ? 7 : n.type === "Artifact" || n.type === "Workflow" ? 6 : n.type === "Step" ? 3.5 : 4.5;
        ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fillStyle = COLORS[n.type] || "#888"; ctx.fill();
        if (selected === n.id || hover?.id === n.id) { ctx.lineWidth = 2.5; ctx.strokeStyle = css.getPropertyValue("--text"); ctx.stroke(); ctx.lineWidth = 1; ctx.strokeStyle = css.getPropertyValue("--border-strong"); }
        if (n.type === "Artifact" || n.type === "Task" || n.type === "Workflow") {
          ctx.fillStyle = css.getPropertyValue("--muted"); ctx.font = "10.5px ui-sans-serif, Segoe UI";
          ctx.fillText((n.label || "").slice(0, 26), n.x + r + 3, n.y + 3);
        }
      });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => { alive = false; cancelAnimationFrame(raf); };
  }, [graph.edges, selected, hover]);

  const pick = (e: React.MouseEvent) => {
    const r = ref.current!.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    let best: N | null = null, bd = 144;
    nodesRef.current.forEach((n) => { const d = (n.x - x) ** 2 + (n.y - y) ** 2; if (d < bd) { bd = d; best = n; } });
    return best;
  };
  return (
    <div style={{ position: "relative" }}>
      <canvas ref={ref} style={{ display: "block", borderRadius: 10, background: "var(--sunken)", cursor: hover ? "pointer" : "default" }}
        onMouseMove={(e) => setHover(pick(e))} onMouseLeave={() => setHover(null)} onClick={(e) => { const n = pick(e); if (n) onSelect(n); }} />
      {hover ? (
        <div style={{ position: "absolute", left: Math.min(hover.x + 10, size.current.w - 180), top: hover.y + 10, background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 8, padding: "4px 8px", fontSize: 11.5, pointerEvents: "none", boxShadow: "var(--shadow)", maxWidth: 200 }}>
          <b style={{ color: COLORS[hover.type] }}>{hover.type}</b> · {hover.label}
        </div>
      ) : null}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 6 }}>
        {Object.entries(COLORS).map(([k, c]) => <span key={k} style={{ fontSize: 11, color: "var(--muted)", display: "inline-flex", alignItems: "center", gap: 4 }}><span style={{ width: 8, height: 8, borderRadius: 9, background: c }} />{k}</span>)}
      </div>
    </div>
  );
}

export function KnowledgeTab({ pid, graph }: { pid: string; graph: { nodes: any[]; edges: any[] } }) {
  const [sel, setSel] = useState<any>(null);
  const [q, setQ] = useState("");
  const [ans, setAns] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const artifacts = useMemo(() => graph.nodes.filter((n) => n.type === "Artifact" || n.type === "Workflow"), [graph]);
  const selNode = sel ? graph.nodes.find((n) => n.id === sel.id) : null;
  if (!graph.nodes.length) return <Empty icon={<Icon.graph />} title="Knowledge graph is empty" hint="Every verified result is stored here and reused before doing work again." />;
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <ForceGraph graph={graph} onSelect={setSel} selected={sel?.id || null} />
      {selNode ? (
        <div className="panel" style={{ padding: 10 }}>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <span className="chip" style={{ background: COLORS[selNode.type] + "22", color: COLORS[selNode.type] }}>{selNode.type}</span>
            <b style={{ fontSize: 13 }}>{selNode.label}</b>
          </div>
          <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 6, display: "grid", gap: 2 }}>
            {Object.entries(selNode.props || {}).filter(([k]) => !["label"].includes(k)).slice(0, 10).map(([k, v]) => (
              <div key={k}><b>{k}:</b> {typeof v === "string" ? v.slice(0, 160) : JSON.stringify(v).slice(0, 160)}</div>
            ))}
            {selNode.valid_until ? <div><b>valid until:</b> {new Date(selNode.valid_until).toLocaleString()}</div> : null}
          </div>
        </div>
      ) : null}
      <form onSubmit={async (e) => {
        e.preventDefault();
        if (!q.trim()) return;
        setBusy(true);
        try { setAns(await api.post(`/projects/${pid}/ask`, { question: q })); } catch (err: any) { setAns({ answer: err.message, sources: [] }); }
        setBusy(false);
      }} style={{ display: "flex", gap: 6 }}>
        <input className="input" placeholder="Ask the project's knowledge…" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn" disabled={busy}>{busy ? <Spinner /> : <Icon.send />}</button>
      </form>
      {ans ? <div className="panel" style={{ padding: 10, fontSize: 13, whiteSpace: "pre-wrap" }}>{ans.answer}{ans.sources?.length ? <div style={{ fontSize: 11, color: "var(--faint)", marginTop: 6 }}>sources: {ans.sources.join(", ")}</div> : null}</div> : null}
      <div>
        <div style={{ fontWeight: 650, fontSize: 12.5, marginBottom: 6 }}>Artifacts & workflows</div>
        <div style={{ display: "grid", gap: 6 }}>
          {artifacts.map((a) => (
            <div key={a.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 9px", border: "1px solid var(--border)", borderRadius: 9, cursor: "pointer" }} onClick={() => setSel(a)}>
              <span style={{ width: 8, height: 8, borderRadius: 9, background: COLORS[a.type] }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12.5, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.props?.path ? fileName(a.props.path) : a.key}</div>
                <div style={{ fontSize: 11, color: "var(--faint)" }}>{a.props?.kind || a.type}{a.valid_until ? ` · valid until ${new Date(a.valid_until).toLocaleTimeString()}` : ""}</div>
              </div>
              {a.props?.path ? <button className="btn btn-sm" onClick={(e) => { e.stopPropagation(); api.post(`/projects/${pid}/open`, { path: a.props.path }).catch(() => {}); }}><Icon.open width={12} height={12} /></button> : null}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
