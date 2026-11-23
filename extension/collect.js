// Runs in the page's own JavaScript world (manifest "world": "MAIN") so it can read the
// React table data behind the "Video Receipt" buttons. Exposes window.__wnCollect.
(() => {
  if (window.__wnCollect) return;
  const dec = s => { try { return atob(s).split(':').pop(); } catch (e) { return s; } };
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // The page loads a package's orders with one GraphQL query (operationName MyShipmentsById). We wrap
  // window.fetch to remember that request once (url, init with the page's own headers, body); later the
  // same query is replayed for every shipment, ten ids per request, which is far faster and more
  // reliable than expanding 140 packages in the DOM. The wrapper sits on top of the page's anti-bot
  // fetch wrapper and replays through it, so whatever that wrapper adds is added again. Nothing from
  // the captured request is ever logged (it carries the session token).
  const cap = { req: null, fetch: null };
  const headersToObj = h => { const o = {}; try { new Headers(h || {}).forEach((v, k) => { o[k] = v; }); } catch (e) {} return o; };
  function remember(input, init, bodyText) {
    try {
      if (!bodyText || bodyText.indexOf('MyShipmentsById') < 0) return;
      const body = JSON.parse(bodyText);
      if (body.operationName !== 'MyShipmentsById' || !body.variables || !Array.isArray(body.variables.shipmentIds)) return;
      const isReq = typeof Request !== 'undefined' && input instanceof Request;
      const url = isReq ? input.url : String(input);
      const headers = headersToObj(isReq ? input.headers : (init && init.headers));
      const base = isReq ? { method: input.method, credentials: input.credentials, mode: input.mode } : Object.assign({}, init || {});
      delete base.body; delete base.headers; delete base.signal;
      cap.req = { url, base, headers, body };
    } catch (e) {}
  }
  function installCapture() {
    const cur = window.fetch;
    if (!cur || cur.__wnWrapped) return;
    const prev = cur;
    const wrapped = function (input, init) {
      try {
        if (!cap.req) {
          const isReq = typeof Request !== 'undefined' && input instanceof Request;
          const u = isReq ? input.url : String(input);
          if (/\/services\/graphql/.test(u)) {
            if (init && typeof init.body === 'string') remember(input, init, init.body);
            else if (isReq) input.clone().text().then(t => remember(input, init, t), () => {});
          }
        }
      } catch (e) {}
      return prev.apply(this, arguments);
    };
    wrapped.__wnWrapped = true; cap.fetch = prev;
    try { window.fetch = wrapped; } catch (e) {}
  }
  installCapture();
  setInterval(installCapture, 2000);   // the page may replace window.fetch after we ran
  // The page wraps fetch() with anti-bot code that fails for these URLs; XMLHttpRequest works.
  const xhr = u => new Promise(res => {
    const x = new XMLHttpRequest(); x.open('GET', u);
    x.onload = () => res(x.status === 200 ? x.responseText : null);
    x.onerror = () => res(null); x.send();
  });
  const buttons = text => [...document.querySelectorAll('button')].filter(b => b.textContent.trim() === text);
  const receiptCount = () => buttons('Video Receipt').length;
  const fiberProps = (el, pred, max = 80) => {
    const k = Object.keys(el).find(k => k.startsWith('__reactFiber'));
    let f = el[k];
    for (let i = 0; i < max && f; i++) { const p = f.memoizedProps; if (p && pred(p)) return p; f = f.return; }
    return null;
  };
  const isTable = p => p.table && p.table.options && Array.isArray(p.table.options.data);
  const itemsSold = () => { const m = document.body.innerText.match(/Items sold\s*(\d+)/); return m ? +m[1] : null; };
  // The footer reads "Showing 1-50 of 53", "Showing 51-100 of 300" or "Showing 1-50 of many".
  // shown = packages on this page; from/to = their positions in the whole list.
  const paging = () => {
    const m = document.body.innerText.match(/Showing\s+(\d+)-(\d+)\s+of\s+(\d+|many)/);
    return m ? { from: +m[1], to: +m[2], shown: +m[2] - +m[1] + 1, total: m[3] === 'many' ? Infinity : +m[3] } : null;
  };
  // The page shows at most 250 packages per view; a bigger show is read page by page. With ?wntest=1 in the
  // address (and limit=50) the cap is 50, so paging can be tested on any show with more than 50 packages.
  const testMode = () => /[?&]wntest=1/.test(location.search);
  const pageCap = () => testMode() ? 50 : 250;
  // The next-page control, in one place. Its exact markup has not been recorded (Oct 2026); this matches the
  // usual forms: a button/link labelled "Next" / "Next page" / "Go to next page", or a bare chevron.
  const nextPageControl = () => {
    const cands = [...document.querySelectorAll('button, a, [role=button]')];
    const labels = e => [e.getAttribute('aria-label') || '', e.getAttribute('title') || '', e.textContent || ''].map(s => s.trim()).filter(Boolean);
    const enabled = e => !e.disabled && e.getAttribute('aria-disabled') !== 'true';
    const matches = (e, re) => labels(e).some(s => re.test(s));
    return cands.find(e => enabled(e) && matches(e, /^(next|next page|go to next page)$/i))
      || cands.find(e => enabled(e) && matches(e, /^(›|>|→|❯|chevron[-_ ]?right)$/i))
      || null;
  };
  const showTitle = () => { const m = document.body.innerText.match(/Open Sidebar\s*\n(.+)\n/); return m ? m[1].trim() : ''; };

  // A package row is expanded when the row right after it holds Video Receipt buttons.
  const collapsedPackageButtons = () => buttons('Expand').filter(b => {
    const tr = b.closest('tr'); if (!tr) return false;
    if (!/\b(Ready to Ship|Needs Label|Shipped|Label|Sep|Oct|Nov|Dec|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug)\b/.test(tr.innerText)) return false;
    const nx = tr.nextElementSibling;
    return !(nx && [...nx.querySelectorAll('button')].some(x => x.textContent.trim() === 'Video Receipt'));
  });

  // Package rows that are open (button reads "Collapse") but whose order list never appeared.
  const openEmptyPackages = () => buttons('Collapse').map(b => b.closest('tr')).filter(tr => {
    if (!tr) return false;
    const nx = tr.nextElementSibling;
    return !(nx && [...nx.querySelectorAll('button')].some(x => x.textContent.trim() === 'Video Receipt'));
  });

  // The Shipments table is a TanStack table; its instance is reachable from any Expand/Collapse
  // button's React props and has toggleAllRowsExpanded(). One call expands every package in a few
  // seconds. Clicking the buttons one by one is the fallback (slow: each click re-renders the page).
  // Once packages are expanded, the first "Expand" button in the DOM belongs to an order sub-row,
  // so try "Collapse" buttons (package rows) first and walk a few candidates.
  const shipmentTable = () => {
    for (const b of buttons('Collapse').concat(buttons('Expand')).slice(0, 12)) {
      const p = fiberProps(b, q => isTable(q) && q.table.options.data[0] && 'trackingCode' in q.table.options.data[0], 200);
      if (p) return p.table;
    }
    return null;
  };
  // How many orders the page should show once every package is open: the sum of the packages'
  // item counts. ("Items sold" counts the whole show, including already-shipped orders.)
  const expectedReceipts = () => {
    const t = shipmentTable();
    if (t) { const n = t.options.data.reduce((a, s) => a + (s.totalItemQuantity || 0), 0); if (n) return n; }
    return itemsSold();
  };
  // The page shows 50 packages at a time. Its "Show 250" option (a hidden listbox item) switches the
  // page size in place, without a reload, so the extension keeps running. Returns true when every
  // package is on the page.
  async function showAllPackages() {
    const opt = [...document.querySelectorAll('[role=option]')].find(e => /^Show 250$/.test(e.textContent.trim()));
    if (!opt) return false;
    opt.click();
    for (let i = 0; i < 40; i++) {
      await sleep(500);
      const pg = paging();
      if (pg && pg.total !== Infinity && pg.shown >= Math.min(pg.total, pageCap())) return true;
    }
    return false;
  }
  // More pages after this one? (true on "Showing 1-250 of 300", false on "Showing 251-300 of 300")
  const hasNextPage = () => { const pg = paging(); return !!(pg && pg.total !== Infinity && pg.to < pg.total); };
  // Click the next-page control and wait until the footer shows the following range. Returns the new paging
  // or null (no control found, or the page did not move within 20 s).
  async function gotoNextPage() {
    const before = paging(); if (!before) return null;
    const firstId = () => { const t = shipmentTable(); return t && t.options.data[0] ? t.options.data[0].id : null; };
    const was = firstId();
    const ctl = nextPageControl(); if (!ctl) return null;
    ctl.click();
    for (let i = 0; i < 40; i++) {
      await sleep(500);
      const pg = paging(), now = firstId();
      if (pg && pg.from > before.from && now && now !== was) return pg;
    }
    return null;
  }
  async function expandAll(onProgress) {
    const target = expectedReceipts();
    const table = shipmentTable();
    if (table && typeof table.toggleAllRowsExpanded === 'function') {
      table.toggleAllRowsExpanded(true);
      // Each package loads its orders lazily after expanding; on a 50-package show the last ones can
      // take 30-60 s. Keep waiting while the receipt count is still growing (up to 3 min), and stop
      // only after 15 s with no growth.
      let last = -1, lastChange = Date.now();
      for (let i = 0; i < 180; i++) {
        await sleep(1000);
        const n = receiptCount(); if (onProgress) onProgress(n, target);
        if (target && n >= target) break;
        if (n !== last) { last = n; lastChange = Date.now(); }
        else if (Date.now() - lastChange > 30000) break;
      }
      // Some packages come back empty when 50 order lists are requested at once. Close and reopen
      // each open-but-empty package (and click any retry button inside it), up to 3 rounds.
      for (let round = 0; round < 3 && target && receiptCount() < target; round++) {
        const stuck = openEmptyPackages();
        if (!stuck.length) break;
        if (onProgress) onProgress(receiptCount(), target);
        for (const tr of stuck) {
          const retry = [...(tr.nextElementSibling ? tr.nextElementSibling.querySelectorAll('button') : [])].find(x => /retry|try again|reload/i.test(x.textContent));
          if (retry) { retry.click(); continue; }
          const btn = [...tr.querySelectorAll('button')].find(x => x.textContent.trim() === 'Collapse');
          if (!btn) continue;
          btn.click(); await sleep(800);
          const again = [...tr.querySelectorAll('button')].find(x => x.textContent.trim() === 'Expand');
          if (again) again.click();
          await sleep(400);
        }
        let last = -1, lastChange = Date.now();
        for (let i = 0; i < 60; i++) {
          await sleep(1000);
          const n = receiptCount(); if (onProgress) onProgress(n, target);
          if (n >= target) break;
          if (n !== last) { last = n; lastChange = Date.now(); } else if (Date.now() - lastChange > 12000) break;
        }
      }
      if (!target || receiptCount() >= target) return { receipts: receiptCount(), expected: target };
    }
    // Fallback: click one package at a time and undo any click that collapsed something.
    let stale = 0, last = receiptCount();
    for (let round = 0; round < 600; round++) {
      const n = receiptCount();
      if (onProgress) onProgress(n, target);
      if (target && n >= target) break;
      const cands = collapsedPackageButtons();
      if (!cands.length) break;
      const b = cands[round % cands.length];
      const before = receiptCount(); b.click(); await sleep(600);
      let after = receiptCount();
      if (after < before) { b.click(); await sleep(600); after = receiptCount(); }
      if (after === last) { if (++stale > 40) break; } else { stale = 0; last = after; }
    }
    return { receipts: receiptCount(), expected: target };
  }

  // One order row (from the DOM table or from the API) -> one bundle item. Same fields either way.
  const rowToItem = (r, i) => ({
    i, shipmentId: r.shipmentId, buyer: r.order.buyer.username, orderNo: dec(r.order.id),
    listing: r.listing.title, qty: r.quantity, cents: r.subtotal && r.subtotal.amount,
    createdAt: r.createdAt, receiptUrl: (r.order.videoReceipt || {}).videoUrl || null
  });
  const liveOf = rows => { const ls = rows[0] && rows[0].order && rows[0].order.livestream; return ls ? { id: ls.id, startTime: ls.startTime, title: ls.title } : null; };

  // Make the page fire one MyShipmentsById request so the wrapper can remember it: expand a collapsed
  // package (or close and reopen an open one). Resolves true once a request has been captured.
  async function captureRequest() {
    if (cap.req) return true;
    const tried = new Set();
    for (let attempt = 0; attempt < 3 && !cap.req; attempt++) {
      const cands = collapsedPackageButtons().filter(b => !tried.has(b));
      let b = cands[0];
      if (!b) {
        const open = buttons('Collapse').find(x => !tried.has(x)); if (!open) break;
        tried.add(open); open.click(); await sleep(600);
        const tr = open.closest('tr'); b = tr && [...tr.querySelectorAll('button')].find(x => x.textContent.trim() === 'Expand');
        if (!b) break;
      }
      tried.add(b); b.click();
      for (let i = 0; i < 16 && !cap.req; i++) await sleep(500);
    }
    return !!cap.req;
  }
  const nodesOf = x => Array.isArray(x) ? x : (x && Array.isArray(x.edges) ? x.edges.map(e => e.node) : []);

  // Orders for every shipment in the table, through the page's own query. Returns {rows} in table
  // order, or {error} when anything is missing (the caller then expands packages instead).
  async function fetchOrdersApi(onProgress) {
    const table = shipmentTable();
    if (!table) return { error: 'no shipments table' };
    if (!(await captureRequest())) return { error: 'no request captured' };
    const ships = table.options.data;
    const want = {}; ships.forEach(s => { want[s.id] = s.totalItemQuantity || 0; });
    const got = {};
    const ids = ships.map(s => s.id);
    const run = async batch => {
      const body = JSON.parse(JSON.stringify(cap.req.body));
      body.variables.shipmentIds = batch;
      const init = Object.assign({}, cap.req.base, { method: 'POST', headers: cap.req.headers, body: JSON.stringify(body) });
      let res, json;
      try { res = await cap.fetch.call(window, cap.req.url, init); json = await res.json(); } catch (e) { return false; }
      const data = json && json.data && json.data.myShipmentsById;
      if (!data) return false;
      for (const node of nodesOf(data)) {
        if (!node || !node.id) continue;
        const items = nodesOf(node.orderItems).filter(r => r && r.order && r.listing);
        got[node.id] = items.map(r => (r.shipmentId ? r : Object.assign({ shipmentId: dec(node.id) }, r)));
      }
      return true;
    };
    let done = 0;
    for (let k = 0; k < ids.length; k += 10) {
      const batch = ids.slice(k, k + 10);
      if (!(await run(batch))) { await sleep(1500); if (!(await run(batch))) return { error: 'request failed' }; }
      done += batch.length; if (onProgress) onProgress(done, ids.length);
    }
    // Any shipment short of its item count: ask for it alone once more.
    for (const id of ids) if ((got[id] || []).length < want[id]) { await run([id]); }
    const short = ids.filter(id => (got[id] || []).length !== want[id]);
    if (short.length) return { error: `${short.length} package${short.length > 1 ? 's' : ''} incomplete` };
    const rows = []; ids.forEach(id => (got[id] || []).forEach(r => rows.push(r)));
    return { rows };
  }

  function collectRows() {
    const seen = new Set(), rows = [];
    for (const b of buttons('Video Receipt')) {
      const p = fiberProps(b, isTable, 60);
      if (!p || seen.has(p.table.options.data)) continue;
      seen.add(p.table.options.data);
      for (const r of p.table.options.data) rows.push(r);
    }
    return { livestream: liveOf(rows), items: rows.map(rowToItem) };
  }

  function collectShipments() {
    const table = shipmentTable();
    if (!table) return [];
    return table.options.data.map(s => ({
      shipmentId: dec(s.id), buyer: s.buyer && s.buyer.username, tracking: s.trackingCode || '',
      courier: s.courier || '', method: s.method || '', qty: s.totalItemQuantity, isFirst: !!s.isFirstShipmentToBuyer
    }));
  }

  // Each receipt has a countdown.vtt next to it whose first cue starts 30 s before the purchase.
  async function fetchVtt(items, onProgress) {
    let idx = 0, done = 0;
    async function worker() {
      while (idx < items.length) {
        const it = items[idx++];
        if (!it.receiptUrl) { it.vttStart = null; done++; continue; }
        const t = await xhr(it.receiptUrl.replace('index_captions.m3u8', 'countdown.vtt'));
        const m = t && t.match(/(\d\d):(\d\d):(\d\d)\.(\d+) -->/);
        it.vttStart = m ? (+m[1] * 3600 + +m[2] * 60 + +m[3]) : null;
        it.purchaseAt = it.vttStart == null ? null : it.vttStart + 30;
        done++; if (onProgress) onProgress(done, items.length);
      }
    }
    await Promise.all([0, 1, 2, 3, 4, 5].map(worker));
  }

  // The receipt's master playlist lists index.m3u8, which is the entire show with a signed query
  // string that is valid for every segment.
  async function showUrl(items) {
    const it = items.find(x => x.receiptUrl); if (!it) return null;
    const master = await xhr(it.receiptUrl); if (!master) return null;
    const line = master.split('\n').map(l => l.trim()).find(l => /index\.m3u8/.test(l));
    if (!line) return null;
    return line.startsWith('http') ? line : 'https://api.whatnot.com' + line;
  }

  // The orders and packages on the page as it is now (one page of the Shipments table).
  async function collectPage(say, pageLabel) {
    say(`Reading orders${pageLabel}...`);
    const target = expectedReceipts();
    const api = await fetchOrdersApi((d, n) => say(`Reading orders${pageLabel}... ${d} / ${n} packages`));
    let livestream, items, ex, how;
    if (api.rows) {
      livestream = liveOf(api.rows); items = api.rows.map(rowToItem);
      ex = { receipts: items.length, expected: target }; how = 'api';
    } else {
      say(`Could not read orders directly (${api.error}). Expanding packages instead...`);
      await sleep(1200);
      ex = await expandAll((n, t) => say(`Expanding packages${pageLabel}... ${n}${t ? ' / ' + t : ''} receipts`));
      say(`Reading orders${pageLabel}...`);
      ({ livestream, items } = collectRows()); how = 'expand';
    }
    return { livestream, items, shipments: collectShipments(), ex, how };
  }

  // Walk every page of the Shipments table when the show has more packages than one view holds (250, or
  // 50 with ?wntest=1). Orders are merged by order number and packages by shipment id, so a package that
  // appears on two pages (the list shifted underneath us) is counted once. The expected count is the sum
  // of the packages' item counts over all pages, which is what the receiver checks against.
  async function collectShow(onStatus) {
    const say = s => onStatus && onStatus(s);
    const pg0 = paging();
    const paged = !!(pg0 && pg0.total !== Infinity && pg0.total > pg0.shown);
    const pageCount = paged ? Math.ceil(pg0.total / Math.max(1, pg0.shown)) : 1;
    const label = n => paged ? ` (page ${n} of ${pageCount})` : '';
    const seenItems = new Map(), seenShips = new Map();
    let livestream = null, how = 'api', expected = 0, pagesRead = 0;
    for (let n = 1; ; n++) {
      const p = await collectPage(say, label(n));
      livestream = livestream || p.livestream;
      if (p.how !== 'api') how = p.how;
      for (const it of p.items) if (!seenItems.has(it.orderNo)) seenItems.set(it.orderNo, it);
      for (const s of p.shipments) if (!seenShips.has(s.shipmentId)) seenShips.set(s.shipmentId, s);
      pagesRead++;
      if (!paged || !hasNextPage()) break;
      say(`Going to page ${n + 1} of ${pageCount}...`);
      const next = await gotoNextPage();
      if (!next) throw new Error(`Only ${pagesRead} of ${pageCount} pages could be read: the next-page control was not found or the page did not change. Nothing was sent.`);
    }
    const shipments = [...seenShips.values()];
    const items = [...seenItems.values()].map((it, i) => Object.assign(it, { i }));
    expected = shipments.reduce((a, s) => a + (s.qty || 0), 0) || (paged ? 0 : expectedReceipts());
    say(`Reading ${items.length} receipt timestamps...`);
    await fetchVtt(items, (d, n) => say(`Reading receipt timestamps... ${d} / ${n}`));
    say('Getting the show link...');
    const url = await showUrl(items);
    const bundle = {
      v: 1, collectedAt: new Date().toISOString(), pageUrl: location.href, showTitle: (livestream && livestream.title) || showTitle(),
      livestream, showUrl: url, itemsSold: itemsSold(), expectedOrders: expected, receiptsFound: items.length, collectedBy: how,
      pagesRead, items: items.map(({ receiptUrl, ...rest }) => rest), shipments
    };
    return bundle;
  }

  window.__wnCollect = { showAllPackages, expandAll, fetchOrdersApi, captureRequest, collectRows, collectShipments, fetchVtt, showUrl, collectShow, collectPage, gotoNextPage, hasNextPage, nextPageControl, itemsSold, expectedReceipts, paging, pageCap, testMode, receiptCount, showTitle };
})();
