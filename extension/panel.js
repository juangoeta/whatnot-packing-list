// Adds the "Send show for packing list" panel to the Shipments page and talks to bridge.js.
(() => {
  if (window.__wnPanelInstalled) return; window.__wnPanelInstalled = true;
  const onShipments = () => location.pathname.startsWith('/dashboard/shipments');
  let host = null, els = {};
  const busy = { on: false };

  function build() {
    host = document.createElement('div'); host.id = 'wn-packing-panel';
    host.style.cssText = 'position:fixed;right:20px;bottom:20px;z-index:2147483646;';
    const sh = host.attachShadow({ mode: 'open' });
    sh.innerHTML = `
      <style>
        .card{font:13px/1.4 -apple-system,system-ui,Segoe UI,sans-serif;color:#221D25;background:#fff;border:1px solid #E4DCD4;border-radius:10px;box-shadow:0 8px 24px rgba(34,29,37,.18);width:300px;padding:14px 16px;box-sizing:border-box}
        h1{font-size:14px;margin:0 0 2px;font-weight:600}
        .sub{color:#6E6674;margin:0 0 10px}
        button{font:inherit;border:0;border-radius:7px;padding:9px 12px;cursor:pointer}
        .go{background:#B5236B;color:#fff;font-weight:600;width:100%}
        .go[disabled]{background:#c9a0b5;cursor:wait}
        .row{display:flex;gap:8px;margin-top:8px}
        .row button{flex:1;background:#F3EEE8;color:#221D25}
        .st{margin-top:10px;min-height:18px;color:#6E6674;white-space:pre-line}
        .ok{color:#1F7A4D}.err{color:#B3261E}
        .close{position:absolute;top:6px;right:8px;background:none;color:#6E6674;padding:2px 6px;font-size:14px}
        .card{position:relative}
        .anyway{display:none;background:none;color:#B5236B;padding:4px 0;text-decoration:underline;font-size:12px}
        .anyway.show{display:inline-block}
      </style>
      <div class="card">
        <button class="close" title="Hide">×</button>
        <h1>Packing list</h1>
        <p class="sub">Sends this show's link and order list. No video is downloaded here.</p>
        <button class="go">Send show for packing list</button>
        <div class="row"><button class="copy">Copy show link</button><button class="opt">Settings</button></div>
        <div class="st"></div>
        <button class="anyway">Send anyway (all orders)</button>
      </div>`;
    els = { go: sh.querySelector('.go'), copy: sh.querySelector('.copy'), opt: sh.querySelector('.opt'), st: sh.querySelector('.st'), close: sh.querySelector('.close'), anyway: sh.querySelector('.anyway') };
    els.close.onclick = () => host.remove();
    els.anyway.onclick = () => { sendAnyway.ok = true; els.anyway.classList.remove('show'); run(); };
    window.addEventListener('message', e => { if (e.source === window && e.data && e.data.type === 'WN_NOTE') status(e.data.text, 'err'); });
    els.opt.onclick = () => window.postMessage({ type: 'WN_OPEN_OPTIONS' }, '*');
    els.go.onclick = run;
    els.copy.onclick = copyLink;
    document.body.appendChild(host);
  }
  const status = (t, cls) => { els.st.textContent = t; els.st.className = 'st ' + (cls || ''); };

  // The filters param is base64 JSON, URL-encoded twice:
  // {"salesChannel":{"type":"single","value":"<live id>"},"status":{"type":"single","value":"unfulfilled"},...}
  function filters() {
    const f = new URLSearchParams(location.search).get('filters');
    if (!f) return null;
    try {
      let t = f; for (let i = 0; i < 2; i++) { try { t = decodeURIComponent(t); } catch (e) {} }
      return JSON.parse(atob(t));
    } catch (e) { return undefined; }   // present but unreadable
  }
  function showSelected() {
    const j = filters();
    if (j === null) return !!window.__wnCollect.showTitle();
    if (j === undefined) return true;
    return !!(j.salesChannel && j.salesChannel.value);
  }
  // The status filter's value, lower-cased ("unfulfilled", "all", ...), or null when the address does not
  // carry one. A value may be a string or {value}/{label} or an array of those; take the first string found.
  function statusFilter() {
    const j = filters(); if (!j || !j.status) return null;
    const pick = v => {
      if (v == null) return null;
      if (typeof v === 'string') return v;
      if (Array.isArray(v)) return pick(v[0]);
      if (typeof v === 'object') return pick(v.value) || pick(v.id) || pick(v.label);
      return String(v);
    };
    const s = pick(j.status.value !== undefined ? j.status.value : j.status);
    return s ? s.toLowerCase() : null;
  }
  const UNFULFILLED = /^(unfulfilled|unshipped|to_?ship|ready_?to_?ship|needs_?label)$/;
  // Only the unshipped orders belong in a packing list. Returns the status value when the page is filtered
  // to something else (e.g. "all"), else null. An address with no status filter is treated as fine: the
  // page defaults to Unfulfilled and the receiver sees statusFilter=null.
  const wrongStatus = () => { const s = statusFilter(); return s && !UNFULFILLED.test(s) ? s : null; };
  const sendAnyway = { ok: false };
  function needMoreRows() {
    const pg = window.__wnCollect.paging();
    return pg && pg.total > pg.shown && pg.shown < window.__wnCollect.pageCap() && !/[?&]limit=/.test(location.search);
  }

  // The resume marker rides in the page address (?wnsend=1) so it survives the reload no matter what.
  const RESUME = 'wnsend';
  function loadAllRowsAndResume() {
    const u = new URL(location.href); u.searchParams.set('limit', '250'); u.searchParams.set(RESUME, '1'); location.href = u.toString();
  }
  function hasResumeMarker() {
    let ss = null; try { ss = sessionStorage.getItem('wn_autosend'); } catch (e) {}
    return new URLSearchParams(location.search).get(RESUME) === '1' || ss === '1';
  }
  function clearResumeMarker() {
    try { sessionStorage.removeItem('wn_autosend'); } catch (e) {}
    try { const u = new URL(location.href); u.searchParams.delete(RESUME); history.replaceState(history.state, '', u.toString()); } catch (e) {}
  }
  async function run() {
    if (busy.on) return;
    if (!showSelected()) { status('Pick one show in the filter at the top first.', 'err'); return; }
    const wrong = wrongStatus();
    if (wrong && !sendAnyway.ok) {
      status('This page shows all orders, not just unshipped ones. Switch the status filter to Unfulfilled first.', 'err');
      els.anyway.classList.add('show');
      return;
    }
    els.anyway.classList.remove('show');
    busy.on = true; els.go.disabled = true;
    try {
      if (needMoreRows()) {
        status('This show has more than 50 packages. Showing all of them first...');
        const ok = await window.__wnCollect.showAllPackages();
        if (!ok) {
          // Last resort: reload with all packages and continue afterwards (marker in the address).
          status('This show has more than 50 packages. Reloading the page to show all of them, then continuing automatically. Keep this tab open.');
          setTimeout(loadAllRowsAndResume, 3000);
          return;
        }
      }
      const bundle = await window.__wnCollect.collectShow(s => status(s));
      bundle.statusFilter = statusFilter();   // the receiver names the folder "(all orders)" when it is not unfulfilled
      if (!bundle.showUrl) throw new Error('Could not read the show link. Reload the page and try again.');
      const expected = bundle.expectedOrders || 0;
      if (expected && bundle.items.length < expected) {
        throw new Error(`Only ${bundle.items.length} of ${expected} orders loaded on the page, so nothing was sent. Wait a few seconds and click again. If it keeps happening, reload the page first.`);
      }
      status('Sending...');
      const res = await send(bundle);
      const pages = bundle.pagesRead > 1 ? ` (${bundle.pagesRead} pages)` : '';
      if (res.ok) status(`Sent. ${bundle.items.length} orders, ${bundle.shipments.length} packages${pages}.\n${res.note || ''}`, 'ok');
      else status('Not sent: ' + (res.error || 'unknown error'), 'err');
    } catch (e) { status(String(e.message || e), 'err'); }
    sendAnyway.ok = false;
    busy.on = false; els.go.disabled = false;
  }

  // After the automatic reload, pick up where we left off once the page has its data.
  let resumeArmed = false, resumeWaits = 0;
  function maybeResume() {
    if (!hasResumeMarker() || busy.on || !window.__wnCollect) return;
    if (!resumeArmed) { resumeArmed = true; status('The page was reloaded to show all packages. Waiting for them to load...'); }
    const ready = window.__wnCollect.paging() || window.__wnCollect.expectedReceipts();
    if (!ready) {
      if (++resumeWaits > 40) { clearResumeMarker(); status('The page reloaded but its packages did not load. Reload the page and click Send show for packing list.', 'err'); }
      return;
    }
    clearResumeMarker();
    if (needMoreRows()) { status('The page reloaded but still shows only some packages. Click Send show for packing list again.', 'err'); return; }
    status('The page was reloaded to show all packages. Continuing...');
    setTimeout(run, 1500);
  }

  async function copyLink() {
    try {
      status('Reading the show link...');
      const { items } = window.__wnCollect.collectRows();
      const url = await window.__wnCollect.showUrl(items);
      if (!url) throw new Error('Expand at least one package first.');
      await navigator.clipboard.writeText(url);
      status('Show link copied. Paste it into VLC (File > Open Network). It expires after a while.', 'ok');
    } catch (e) { status(String(e.message || e), 'err'); }
  }

  function send(bundle) {
    return new Promise(resolve => {
      const onMsg = e => { if (e.source === window && e.data && e.data.type === 'WN_RESULT') { window.removeEventListener('message', onMsg); resolve(e.data); } };
      window.addEventListener('message', onMsg);
      window.postMessage({ type: 'WN_SEND', bundle }, '*');
      setTimeout(() => { window.removeEventListener('message', onMsg); resolve({ ok: false, error: 'No reply from the extension. Reload the page.' }); }, 60000);
    });
  }

  function tick() {
    const want = onShipments() && window.__wnCollect;
    if (want && !document.getElementById('wn-packing-panel') && !host) build();
    if (want && host) maybeResume();
    if (want && host && !document.getElementById('wn-packing-panel') && !busy.on) { /* user hid it; leave it hidden */ }
    if (!want && host) { host.remove(); host = null; }
  }
  setInterval(tick, 1500); tick();
})();
