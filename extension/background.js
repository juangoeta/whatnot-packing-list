// Service worker: posts the bundle to the Who Got What service (DEFAULT_ENDPOINT, a declared host in the manifest)
// or to the address saved in Settings as an override (an optional host permission, granted when it was saved).
const DEFAULT_ENDPOINT = 'https://your-receiver.example.com/show';
const originPattern = endpoint => { try { return new URL(endpoint).origin + '/*'; } catch (e) { return null; } };
const hasOrigin = pattern => new Promise(res => { if (!chrome.permissions || !pattern) return res(false); chrome.permissions.contains({ origins: [pattern] }, ok => res(!!ok)); });

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'options') { chrome.runtime.openOptionsPage(); return; }
  if (msg.type !== 'send') return;
  (async () => {
    const v = await chrome.storage.sync.get(['endpoint', 'token']);
    const endpoint = v.endpoint || DEFAULT_ENDPOINT, token = v.token || '';
    const b = msg.bundle;
    if (!token) { sendResponse({ ok: false, error: 'No access code saved. Open Settings and paste the code from your welcome email.' }); return; }
    // An override address is an optional host permission, granted when it was saved in Settings. Without it the
    // fetch below fails with an unhelpful error, so check first and say what to do. The default host is in the manifest.
    if (!(await hasOrigin(originPattern(endpoint)))) {
      sendResponse({ ok: false, error: 'Chrome has not allowed the extension to contact the address in Settings. Open Settings, click Save and choose Allow.' });
      return;
    }
    try {
      const ctl = new AbortController(); const timer = setTimeout(() => ctl.abort(), 30000);
      const r = await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json', ...(token ? { 'X-Packing-Token': token } : {}) }, body: JSON.stringify(b), signal: ctl.signal });
      clearTimeout(timer);
      const text = await r.text();
      let note = '';
      try { note = JSON.parse(text).message || ''; } catch (e) { note = text.slice(0, 120); }
      if (r.ok) sendResponse({ ok: true, note });
      else sendResponse({ ok: false, error: `Service replied ${r.status}. ${note}` });
    } catch (e) { sendResponse({ ok: false, error: (e.name === 'AbortError' ? 'The service did not answer within 30 seconds.' : 'Could not reach the service (' + e.message + ').') + ' Check Settings.' }); }
  })();
  return true;
});
// First run: the welcome page (what the service is, the steps, a sample list, the setup form). The icon opens it
// again until an access code is saved, then Settings.
const welcome = () => chrome.tabs.create({ url: chrome.runtime.getURL('welcome.html') });
chrome.runtime.onInstalled.addListener(d => { if (d.reason === 'install') welcome(); });
chrome.action.onClicked.addListener(async () => {
  const { token = '' } = await chrome.storage.sync.get(['token']);
  if (token) chrome.runtime.openOptionsPage(); else welcome();
});
