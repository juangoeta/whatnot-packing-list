// Isolated-world content script: relays messages between the page panel and the service worker.
// After the extension is reloaded or updated, an already-open page keeps this old script but loses
// chrome.runtime ("extension context invalidated"). Tell the panel to reload instead of throwing.
const alive = () => { try { return !!(chrome.runtime && chrome.runtime.id); } catch (e) { return false; } };
const STALE = 'The extension was updated. Reload this page and try again.';
window.addEventListener('message', e => {
  if (e.source !== window || !e.data) return;
  if (e.data.type === 'WN_SEND') {
    if (!alive()) { window.postMessage({ type: 'WN_RESULT', ok: false, error: STALE }, '*'); return; }
    try {
      chrome.runtime.sendMessage({ type: 'send', bundle: e.data.bundle }, res => {
        const err = chrome.runtime.lastError ? chrome.runtime.lastError.message : 'no response';
        window.postMessage({ type: 'WN_RESULT', ...(res || { ok: false, error: err }) }, '*');
      });
    } catch (err) { window.postMessage({ type: 'WN_RESULT', ok: false, error: STALE }, '*'); }
  } else if (e.data.type === 'WN_OPEN_OPTIONS') {
    if (!alive()) { window.postMessage({ type: 'WN_NOTE', text: STALE }, '*'); return; }
    try { chrome.runtime.sendMessage({ type: 'options' }); } catch (err) { window.postMessage({ type: 'WN_NOTE', text: STALE }, '*'); }
  }
});
