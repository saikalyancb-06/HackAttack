/**
 * Operator bridge: runs ONLY on the Operator UI (localhost:3000).
 * Relays requests from the page to the extension background, and pushes
 * agent events (progress, user turn, task end) back to the page.
 *
 * Page -> extension:  window.postMessage({ __operator: 'to-ext', reqId, type, ...payload }, origin)
 * Extension -> page:  window.postMessage({ __operator: 'from-ext', reqId?, type, ...payload }, origin)
 */
(() => {
  if (window.__OPERATOR_BRIDGE__) return;
  window.__OPERATOR_BRIDGE__ = true;

  const origin = window.location.origin;
  const post = (msg) => window.postMessage({ __operator: 'from-ext', ...msg }, origin);

  document.documentElement.dataset.operatorExt = chrome.runtime.getManifest().version;

  window.addEventListener('message', (ev) => {
    if (ev.source !== window || ev.origin !== origin) return;
    const msg = ev.data;
    if (!msg || msg.__operator !== 'to-ext' || typeof msg.type !== 'string') return;
    const { reqId, __operator, ...payload } = msg;
    chrome.runtime.sendMessage({ ...payload, type: `OPERATOR_${msg.type}` })
      .then((res) => post({ reqId, type: 'RESPONSE', ok: true, ...(res || {}) }))
      .catch((err) => post({ reqId, type: 'RESPONSE', ok: false, error: String(err && err.message || err) }));
  });

  chrome.runtime.onMessage.addListener((request) => {
    if (request && request.type === 'OPERATOR_EVENT') post({ type: 'EVENT', event: request.event });
  });

  post({ type: 'READY', version: chrome.runtime.getManifest().version });
})();
