"use client";
// Talks to the Operator browser extension through its bridge content script (operator_bridge.js).
// The extension opens a tab next to this one, runs the web task there live, and streams events back.

type Listener = (event: any) => void;
const listeners = new Set<Listener>();
const waiting = new Map<string, (res: any) => void>();
let ready = false;
let started = false;

function init() {
  if (started || typeof window === "undefined") return;
  started = true;
  ready = Boolean(document.documentElement.dataset.operatorExt);
  window.addEventListener("message", (ev) => {
    if (ev.source !== window || ev.origin !== window.location.origin) return;
    const m = ev.data;
    if (!m || m.__operator !== "from-ext") return;
    if (m.type === "READY") ready = true;
    else if (m.type === "RESPONSE" && m.reqId && waiting.has(m.reqId)) {
      waiting.get(m.reqId)!(m);
      waiting.delete(m.reqId);
    } else if (m.type === "EVENT") listeners.forEach((fn) => fn(m.event));
  });
}

export function extensionReady(): boolean {
  init();
  return ready || Boolean(typeof document !== "undefined" && document.documentElement.dataset.operatorExt);
}

export function extRequest(type: string, payload: Record<string, any> = {}, timeoutMs = 20000): Promise<any> {
  init();
  return new Promise((resolve) => {
    if (!extensionReady()) return resolve({ ok: false, success: false, error: "extension not installed" });
    const reqId = Math.random().toString(36).slice(2);
    const t = setTimeout(() => { waiting.delete(reqId); resolve({ ok: false, success: false, error: "extension did not answer" }); }, timeoutMs);
    waiting.set(reqId, (res) => { clearTimeout(t); resolve(res); });
    window.postMessage({ __operator: "to-ext", reqId, type, ...payload }, window.location.origin);
  });
}

export function onExtensionEvent(fn: Listener): () => void {
  init();
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}
