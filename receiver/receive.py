#!/usr/bin/env python3
"""Receives show bundles from the Chrome extension and immediately downloads the show.

  python3 receive.py [--port 8787] [--token-file receiver/ACCESS_CODE.txt | --token SECRET] [--out "~/Desktop/Whatnot Packing Lists"] [--admin-port 8789]

POST /show  (JSON bundle from the extension)  ->  {"ok":true,"message":"..."}
Bodies over 8 MB get 413 unread; 10 sends per minute per client IP, then 429; codes compared in constant time.
The admin page and API (/admin, /admin/api/*) live on a second listener bound to 127.0.0.1:--admin-port, so
they are not on the public Funnel; reach them from the tailnet with `tailscale serve` (README).
For each bundle it writes <out>/<Mon D>/work/{show.json, rows.tsv, ship.tsv, livestream.json,
segtimes.tsv, seg_urls.txt} in the layout the packing-list pipeline expects, then downloads every
segment into <out>/<Mon D>/work/seg/ with 16 threads. Run it behind an HTTPS tunnel (Cloudflare
Tunnel, Tailscale Funnel, ngrok) and put that URL in the extension's Settings.

When the download finishes (work/DOWNLOAD_DONE) it runs `pipeline/scripts/run.py prep` in that folder
(audio, whisper, lag, tiles), one show at a time, logging to work/prep.log, and leaves work/PREP_DONE
(first line: the measured lag) or work/PREP_FAILED (last log lines). --no-prep turns that off.

A resend of a downloaded show rewrites the order files only. Row ids stay stable (an order keeps the index it
had), the previous list is kept as work/rows.prev.tsv, and work/diff.json records what changed (orders added,
removed, moved to another package, tracking numbers added). When the review is done (work/notes.tsv) the
packing list is rebuilt with a "Changed since your last list" box and the notification says e.g.
"3 new, 1 removed, 41 tracking numbers added".
"""
import argparse, json, os, re, sys, threading, datetime, time, urllib.request, urllib.parse, zoneinfo, subprocess, queue, hmac, base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor

ap = argparse.ArgumentParser(); ap.add_argument('--port', type=int, default=8787); ap.add_argument('--token', default='', help='master code; prefer --token-file (an argument shows in ps for every local process)')
ap.add_argument('--token-file', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ACCESS_CODE.txt'), help='file holding the master code (mode 600), read when --token is not given')
ap.add_argument('--admin-port', type=int, default=8789, help='admin page and API, bound to 127.0.0.1 only (0 = off); reach it from the tailnet with tailscale serve, never Funnel')
ap.add_argument('--out', default=os.path.expanduser(os.environ.get('WHATNOT_OUT') or '~/Desktop/Whatnot Packing Lists'))
ap.add_argument('--no-prep', action='store_true', help='do not run run.py prep after a download')
ap.add_argument('--work-root', default=os.path.expanduser(os.environ.get('WHATNOT_WORK_ROOT') or '~/whatnot-work'), help='where work/ folders live (outside iCloud); <show>/work is a symlink to it')
# WHATNOT_OUT and WHATNOT_WORK_ROOT set the defaults (tests and ingest.py); tests also set receive.A.out directly.
A = ap.parse_args() if __name__ == '__main__' else ap.parse_args([])
TZ = zoneinfo.ZoneInfo('America/New_York')

def load_token(path, log=print):
    """The master code from a file (first line, stripped), '' when the file is missing or empty. Warns when
    the file is readable by other users; the code is never printed."""
    try: code = open(path).readline().strip()
    except OSError: return ''
    if os.stat(path).st_mode & 0o077: log(f'WARNING: {path} is readable by other users; chmod 600 it')
    return code

HOSTS = ('whatnot.com',)                 # showUrl and every segment: https on one of these domains (api.whatnot.com, s3ntry.whatnot.com seen so far)
MAX_SEGS, MAX_SEG_BYTES, MAX_BYTES = 20000, 16 << 20, 24 << 30   # 11 h of 2 s segments; real segments are ~1 MB; a 4 h 40 min show was ~8 GB

def bad_url(url):
    """Why this URL may not be fetched (not https, or a host off HOSTS: file://, loopback, LAN, tailnet), else None."""
    u = urllib.parse.urlsplit(str(url)); h = (u.hostname or '').lower()
    if u.scheme != 'https': return f'not https: {str(url)[:60]}'
    if not any(h == d or h.endswith('.' + d) for d in HOSTS): return f'host not allowed: {h}'
    return None

def get(url, timeout=60, limit=MAX_SEG_BYTES):
    reason = bad_url(url)
    if reason: raise ValueError(reason)
    data = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=timeout).read(limit + 1)
    if len(data) > limit: raise ValueError(f'over {limit >> 20} MB: {str(url)[:60]}')
    return data

UNFULFILLED = {'unfulfilled', 'unshipped', 'to_ship', 'toship', 'ready_to_ship', 'readytoship', 'needs_label', 'needslabel'}

def all_orders(b):
    """True when the extension reports a Shipments status filter other than Unfulfilled (the seller clicked
    "Send anyway"): the list then holds shipped orders too, and the folder says so."""
    s = b.get('statusFilter')
    return bool(s) and str(s).lower() not in UNFULFILLED

def live_id(b):
    return (b.get('livestream') or {}).get('id') or ''

def show_key(b):
    """The Whatnot live id: from the rows' livestream, else from the Shipments page address (its filters param is base64
    JSON, URL-encoded twice; salesChannel.value is the live id, read the way panel.js does). '' when the bundle has neither.
    The ledger, the show folder and the per-show lock all key on it, so a resend is never a new show."""
    if live_id(b): return live_id(b)
    try:
        f = re.search(r'[?&]filters=([^&#]*)', b.get('pageUrl') or '').group(1)
        for _ in range(2): f = urllib.parse.unquote(f)
        v = json.loads(base64.b64decode(f + '=' * (-len(f) % 4))).get('salesChannel', {}).get('value')
        return str(v) if isinstance(v, (str, int)) and v else ''
    except Exception: return ''

def short_title(b):
    """The first two words of the show title, letters and digits only, capitalized: 'SNACKS DEALS AND...' -> 'Snacks Deals'."""
    words = re.findall(r'[A-Za-z0-9]+', b.get('showTitle') or (b.get('livestream') or {}).get('title') or '')
    return ' '.join(w.capitalize() for w in words[:2])

def folder_of(live):
    """The show folder under --out whose work/show.json holds this Whatnot show id, whatever the folder was renamed
    to (a resend lands in it). One listdir of the out root and one show.json read (~100 KB) per folder that still has
    a work/; never seg/ or tiles/. Finished shows have no work/ (deleted at the end), so they cost one failed open."""
    if not live: return None
    try: names = sorted(os.listdir(A.out))
    except OSError: return None
    for name in names:
        try:
            if show_key(json.load(open(os.path.join(A.out, name, 'work', 'show.json')))) == live: return os.path.join(A.out, name)
        except Exception: pass    # no work/, a half-written show.json, or the '{}' placeholder of the old trick
    return None

def show_dir(b, client='master', reuse=None):
    """<Mon D>/ for the first show of the day, <Mon D> <two title words>/ for another show the same day (another
    show id already holds <Mon D>/); a resend (same show id) finds its folder by id. Client shows: <Mon D> - <Client>."""
    if reuse and os.path.isdir(reuse): return reuse
    found = folder_of(show_key(b))
    if found: return found
    ms = (b.get('livestream') or {}).get('startTime')
    d = datetime.datetime.fromtimestamp(ms / 1000, TZ) if ms else datetime.datetime.now(TZ)
    day = d.strftime('%b %-d') + ('' if client == 'master' else f' - {client}'); tail = ' (all orders)' if all_orders(b) else ''
    base = os.path.join(A.out, day + tail); n = 1
    while os.path.exists(os.path.join(base, 'work', 'show.json')):  # another show holds it: named from its title (never show2), then "<title> 3", ...
        n += 1; base = os.path.join(A.out, f'{day} {short_title(b) or "show2"}{f" {n}" if n > 2 else ""}{tail}')
    return base

INBOX = os.path.expanduser('~/Library/Application Support/whatnot-receiver/inbox')
LEDGER = os.path.expanduser('~/Library/Application Support/whatnot-receiver/clients.json')
LOCK = threading.Lock()
DIR_LOCK = threading.Lock()                       # one show claims its folder at a time (two shows sent the same second)
SHOW_LOCKS = {}                                  # show key -> Lock: two sends of one show run one after the other

def show_lock(b):
    with LOCK: return SHOW_LOCKS.setdefault(show_key(b), threading.Lock())

def load_ledger():
    try: return json.load(open(LEDGER))
    except Exception: return {'clients': {}}

def save_ledger(d):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    tmp = LEDGER + '.tmp'; json.dump(d, open(tmp, 'w'), indent=1); os.replace(tmp, LEDGER)

def same_token(a, b):
    """Constant-time string compare, so a wrong code takes as long as a nearly-right one."""
    return hmac.compare_digest(str(a).encode(), str(b).encode())

def client_for(code):
    """(name, record) for an active client code, ('master', None) for the --token code, else (None, None).
    Every compare is constant-time; the ledger is scanned rather than looked up by key for the same reason (it holds a handful of clients)."""
    if A.token and same_token(code, A.token): return 'master', None
    for k, c in load_ledger()['clients'].items():
        if same_token(code, k) and c.get('active', True): return c['name'], c
    return None, None

def charge(code, name, b):
    """Apply one credit for a new show, or none for a resend. Returns (ok, message, folder_hint)."""
    live = show_key(b)
    now = datetime.datetime.now(TZ).isoformat(timespec='seconds')
    if name == 'master': return True, '', None
    with LOCK:
        d = load_ledger(); c = d['clients'][code]
        shows = c.setdefault('shows', {})
        if live in shows:
            shows[live].update({'last': now, 'sends': shows[live].get('sends', 0) + 1, 'items': len(b['items'])})
            save_ledger(d)
            return True, f"This show was already received; updated with {len(b['items'])} orders. {c['credits']} show{'s' if c['credits'] != 1 else ''} left on your plan.", shows[live].get('folder')
        if c.get('credits', 0) <= 0:
            return False, 'This code has no shows left. Contact Juan to add more.', None
        c['credits'] -= 1; c['used'] = c.get('used', 0) + 1
        shows[live] = {'title': (b.get('showTitle') or '').strip(), 'first': now, 'last': now, 'sends': 1, 'items': len(b['items'])}
        save_ledger(d)
        return True, f"{c['credits']} show{'s' if c['credits'] != 1 else ''} left on your plan.", None

def remember_folder(code, b, folder):
    if not code: return
    live = show_key(b)
    with LOCK:
        d = load_ledger(); c = d['clients'].get(code)
        if c and live in c.get('shows', {}): c['shows'][live]['folder'] = folder; save_ledger(d)

def refund(code, b, log):
    """Give the credit back when the show never arrived (process() crashed, or not one segment could be fetched):
    the show leaves the ledger, so the next Send is charged as new. A resend (sends > 1) refunds nothing."""
    if not code: return
    live = show_key(b)
    with LOCK:
        d = load_ledger(); c = d['clients'].get(code) or {}; s = c.get('shows', {}).get(live)
        if not s or s.get('sends', 0) != 1: return
        del c['shows'][live]; c['credits'] = c.get('credits', 0) + 1; c['used'] = max(0, c.get('used', 0) - 1); save_ledger(d)
    log(f"credit refunded to {c['name']}: the show never arrived ({c['credits']} left)")

def makedirs_retry(path, log, tries=10):
    # A Desktop synced by iCloud can refuse a mkdir for a few seconds right after a delete (EDEADLK).
    for i in range(tries):
        try: os.makedirs(path, exist_ok=True); return
        except OSError as e:
            log(f'mkdir {path} failed ({e.strerror}), retrying'); time.sleep(3)
    os.makedirs(path, exist_ok=True)

RUN_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'pipeline', 'scripts', 'run.py')
PREP_ENV = dict(os.environ, PATH='/opt/homebrew/bin:/usr/local/bin:' + os.environ.get('PATH', ''))  # launchd gives a bare PATH
PREP_Q = queue.Queue(); PREP_WORKER = [None]; PREP_CURRENT = [None]

def notify(msg, title='Packing list prep'):
    try: subprocess.run(['osascript', '-e', f'display notification "{msg.replace(chr(34), chr(39))}" with title "{title}"'], check=False, timeout=10)
    except Exception: pass

def row_ids(work): return [r[0] for r in read_rows(os.path.join(work, 'rows.tsv'))]

def run_prep(work, log):
    """Run run.py prep in work/ (blocking). Writes PREP_DONE or PREP_FAILED; never raises."""
    name = os.path.basename(work)
    if os.path.exists(os.path.join(work, 'PREP_DONE')): log(f'prep already done for {name}'); return
    for m in ('PREP_FAILED',):
        try: os.remove(os.path.join(work, m))
        except FileNotFoundError: pass
    t0 = time.time(); log(f'prep started for {name}'); seen = set(row_ids(work))
    for again in (False, True):   # a second pass when a resend added rows while the first ran: wav/whisper/tiles for those rows only (lag is redone)
        try:
            with open(os.path.join(work, 'prep.log'), 'a') as out:
                out.write(f'--- prep {datetime.datetime.now(TZ).isoformat(timespec="seconds")}\n'); out.flush()
                r = subprocess.run([sys.executable, RUN_PY, '--work', work, 'prep'], stdout=out, stderr=subprocess.STDOUT, env=PREP_ENV, timeout=4 * 3600)
            code = r.returncode
        except Exception as e:
            code = -1; open(os.path.join(work, 'prep.log'), 'a').write(f'prep crashed: {e!r}\n')
        left = [i for i in row_ids(work) if i not in seen] if code == 0 else []   # rows.tsv grew while the subprocess ran
        if not left: break
        if again: log(f'{name}: {len(left)} order(s) arrived in a resend during the second pass too: run.py wav/transcribe/tiles --rows {",".join(left[:40])}'); break
        seen.update(left); log(f'{name}: {len(left)} order(s) arrived in a resend while prep ran; prepping them (rows {",".join(left[:40])})')
    mins = (time.time() - t0) / 60
    if code == 0:
        lag = ''
        try: lag = open(os.path.join(work, 'lag.txt')).read().strip()
        except Exception: pass
        tiles = len([f for f in os.listdir(os.path.join(work, 'tiles')) if f.endswith('.jpg')]) if os.path.isdir(os.path.join(work, 'tiles')) else 0
        try: drops = [l.split('\t') for l in open(os.path.join(work, 'dropouts.tsv')).read().splitlines() if l]   # P10: i P kind a b
        except Exception: drops = []
        gaps = len({(d[3], d[4]) for d in drops if len(d) >= 5}); dropline = f'  {len(drops)} orders in {gaps} stream dropout(s)' if drops else ''
        open(os.path.join(work, 'PREP_DONE'), 'w').write(f'lag {lag}\ntiles {tiles}\nminutes {mins:.1f}\ndropouts {len(drops)} rows in {gaps} gaps\n')
        log(f'prep done for {name}: lag {lag or "?"} s, {tiles} tiles, {mins:.1f} min' + dropline)
        notify(f'{name} is ready for review ({tiles} orders).' + dropline)
    else:
        try: tail = open(os.path.join(work, 'prep.log')).read().splitlines()[-20:]
        except Exception: tail = []
        open(os.path.join(work, 'PREP_FAILED'), 'w').write(f'exit {code} after {mins:.1f} min\n' + '\n'.join(tail) + '\n')
        log(f'PREP FAILED for {name} (exit {code}); see {work}/prep.log'); notify(f'{name}: prep FAILED, see work/prep.log')

def read_rows(path):
    try: return [l.rstrip('\n').split('\t') for l in open(path) if l.strip()]
    except FileNotFoundError: return []

def stable_ids(items, prev):
    """On a resend, every order keeps the row index it had in the previous rows.tsv (wav/, tiles/, frames/,
    notes.tsv and lag_rows.tsv are all keyed by it); orders not seen before get the next free numbers. Without
    this, one cancelled order would shift every later index and the review would no longer line up."""
    old = {r[3]: int(r[0]) for r in prev if len(r) > 3}
    nxt = max(old.values(), default=-1) + 1
    for it in items:
        if it['orderNo'] in old: it['i'] = old[it['orderNo']]
        else: it['i'] = nxt; nxt += 1
    items.sort(key=lambda it: it['i'])

def resend_diff(work, prev, prev_ship, b):
    """Compare the previous rows.tsv with this bundle (order identity = Whatnot order number, package identity =
    shipment id): orders added, removed, moved to another package, and packages that gained a tracking number.
    Removed rows keep the product text from the review (notes.tsv, same row index) and the package number from
    the last build (packages.json) so the seller can pull them out of a box that is already packed. Written to
    work/diff.json for `run.py build`."""
    notes = {r[0]: r[1] for r in read_rows(os.path.join(work, 'notes.tsv')) if len(r) > 1}
    pkg = {}
    try: pkg = {str(p['shipment']): (p['num'], p['buyer']) for p in json.load(open(os.path.join(work, 'packages.json')))}
    except Exception: pass
    old = {r[3]: r for r in prev if len(r) > 4}
    now = {it['orderNo']: it for it in b['items']}
    d = {'at': datetime.datetime.now(TZ).isoformat(timespec='seconds'), 'prev': len(old), 'now': len(now), 'added': [], 'removed': [], 'moved': [], 'tracking': 0}
    for o, it in now.items():
        e = {'i': it['i'], 'order': o, 'buyer': it['buyer'], 'listing': it['listing'] or '', 'shipment': str(it['shipmentId'])}
        if o not in old: d['added'].append(e)
        elif old[o][1] != str(it['shipmentId']):
            p = pkg.get(old[o][1], (None, old[o][2]))
            e.update({'from': old[o][1], 'fromPackage': p[0], 'fromBuyer': p[1]}); d['moved'].append(e)
    for o, r in old.items():
        if o not in now:
            p = pkg.get(r[1], (None, r[2]))
            d['removed'].append({'i': int(r[0]), 'order': o, 'buyer': r[2], 'listing': r[4], 'shipment': r[1], 'product': notes.get(r[0], ''), 'package': p[0]})
    for s in b.get('shipments', []):
        if s.get('tracking') and not prev_ship.get(str(s['shipmentId'])): d['tracking'] += 1
    json.dump(d, open(os.path.join(work, 'diff.json'), 'w'), indent=1)
    return d

def diff_summary(d):
    """'3 new, 1 removed, 41 tracking numbers added' (only the parts that are not zero)."""
    n = [len(d['added']), len(d['removed']), len(d['moved']), d['tracking']]
    words = [f'{n[0]} new', f'{n[1]} removed', f'{n[2]} moved', f'{n[3]} tracking number{"s" if n[3] != 1 else ""} added']
    return ', '.join(w for w, k in zip(words, n) if k) or 'no order changes'

def rebuild_after_resend(work, log, diff=None):
    """A resend after the review is done (notes.tsv exists) rebuilds the packing list so new tracking numbers
    land in the Desktop files and the page opens with "Changed since your last list" (NEW / REMOVED / MOVED
    orders from work/diff.json). Orders never reviewed are built in as NEW with "(not reviewed)"."""
    name = os.path.basename(work)
    if not os.path.exists(os.path.join(work, 'notes.tsv')): return
    ids = [r[0] for r in read_rows(os.path.join(work, 'rows.tsv'))]
    reviewed = {r[0] for r in read_rows(os.path.join(work, 'notes.tsv'))}
    new = [i for i in ids if i not in reviewed]
    if new:
        log(f'{name}: {len(new)} order(s) in the resend were never reviewed; they go in the list as NEW "(not reviewed)" (run.py wav/transcribe/tiles --rows {",".join(new[:40])}, review, merge, build)')
    r = subprocess.run([sys.executable, RUN_PY, '--work', work, 'build'], capture_output=True, text=True, env=PREP_ENV, timeout=600)
    outp = (r.stdout + r.stderr).strip().splitlines()
    if r.returncode != 0:
        log(f'{name}: rebuild FAILED: ' + (outp[-1] if outp else f'exit {r.returncode}')); return
    summary = diff_summary(diff) if diff else 'no diff (first send)'
    log(f'{name}: packing list rebuilt ({outp[0] if outp else ""}); {summary}. Republish the artifact from the same file path.')
    notify(f'{name}: {summary}.', 'Packing list updated')

def prep_worker():
    while True:
        work, log = PREP_Q.get()
        PREP_CURRENT[0] = work
        try: run_prep(work, log)
        finally: PREP_CURRENT[0] = None; PREP_Q.task_done()

def enqueue_prep(work, log):
    if A.no_prep or os.path.exists(os.path.join(work, 'PREP_DONE')): return
    if PREP_WORKER[0] is None:
        PREP_WORKER[0] = threading.Thread(target=prep_worker, daemon=True); PREP_WORKER[0].start()
    waiting = PREP_Q.qsize() + (1 if PREP_CURRENT[0] else 0)
    log(f'prep queued for {os.path.basename(work)}' + (f' (behind {waiting} other show{"s" if waiting > 1 else ""})' if waiting else ''))
    PREP_Q.put((work, log))

def work_dir(base, log):
    """The scratch folder for a show lives under --work-root (not synced by iCloud); <show>/work links to it.
    iCloud evicts big files when the disk is nearly full and blocks reads while it re-downloads them
    (Sep 29, 2026: an hour lost on a 963-order show)."""
    link = os.path.join(base, 'work')
    if os.path.islink(link) and not os.path.exists(link): os.remove(link)          # dangling link from a deleted run
    # a show folder renamed by hand keeps the scratch its work/ link already points at (a resend must not re-download)
    work = os.path.realpath(link) if os.path.islink(link) else os.path.join(A.work_root, os.path.basename(base))
    os.makedirs(os.path.join(work, 'seg'), exist_ok=True)
    makedirs_retry(base, log)
    if os.path.isdir(link) and not os.path.islink(link):
        log(f'{base}/work is a real folder (old layout); leaving it, scratch goes to {work}')
    elif not os.path.lexists(link):
        os.symlink(work, link)
    open(os.path.join(work, 'show_folder.txt'), 'w').write(base + '\n')
    return work

def process(b, log, client='master', code=None, reuse=None, playlist=None):
    # playlist: the m3u8 body the handler already fetched to check the link (bytes); fetched here only when absent (ingest.py).
    # One send of a show at a time: a second click while the first is downloading waits, then finds DOWNLOAD_DONE
    # and only rewrites the order files (no second download into the same seg/, no renumbered rows).
    with show_lock(b): _process(b, log, client, code, reuse, playlist)

def _process(b, log, client='master', code=None, reuse=None, playlist=None):
    # Keep a copy outside the Desktop first, so a bundle is never lost to a folder problem.
    os.makedirs(INBOX, exist_ok=True)
    stamp = datetime.datetime.now(TZ).strftime('%Y%m%d-%H%M%S')
    json.dump(b, open(os.path.join(INBOX, f'{stamp}-{client}.json'), 'w'))
    with DIR_LOCK:   # folder choice and the show.json that claims it, atomically
        base = show_dir(b, client, reuse); work = work_dir(base, log)
        remember_folder(code, b, base)
        # A resend: keep the previous order list as rows.prev.tsv (one file, ~100 KB) for the diff, and keep row ids stable.
        rows_f = os.path.join(work, 'rows.tsv'); prev = read_rows(rows_f)
        prev_ship = {r[0]: r[2] for r in read_rows(os.path.join(work, 'ship.tsv')) if len(r) > 2}
        if prev: stable_ids(b['items'], prev)
        json.dump(b, open(os.path.join(work, 'show.json'), 'w'), indent=1)
    with open(rows_f + '.tmp', 'w') as f:   # the old rows.tsv stays in place until the new one is complete
        for it in b['items']:
            f.write('\t'.join(str(x) for x in [it['i'], it['shipmentId'], it['buyer'], it['orderNo'], (it['listing'] or '').replace('\t', ' '), it.get('cents') or 0, it.get('vttStart') if it.get('vttStart') is not None else '', it.get('createdAt') or '']) + '\n')
    if prev: os.replace(rows_f, os.path.join(work, 'rows.prev.tsv'))
    os.replace(rows_f + '.tmp', rows_f)
    with open(os.path.join(work, 'ship.tsv'), 'w') as f:
        for s in b.get('shipments', []):
            f.write('\t'.join(str(x) for x in [s['shipmentId'], s['buyer'], s['tracking'], s['courier'], s['method'], s['qty'], 'true' if s['isFirst'] else 'false']) + '\n')
    json.dump(b.get('livestream'), open(os.path.join(work, 'livestream.json'), 'w'))
    diff = resend_diff(work, prev, prev_ship, b) if prev else None
    log(f'saved bundle to {work}' + (f'; since the last send: {diff_summary(diff)} -> diff.json' if diff else ''))
    if os.path.exists(os.path.join(work, 'DOWNLOAD_DONE')):
        log('recording already downloaded for this show; order files updated, no re-download')
        rebuild_after_resend(work, log, diff); enqueue_prep(work, log)
        left = [str(e['i']) for e in diff['added']] if diff and os.path.exists(os.path.join(work, 'PREP_DONE')) and not os.path.exists(os.path.join(work, 'notes.tsv')) else []
        if left: log(f'{len(left)} order(s) in the resend have no transcript or tile (prep finished before they arrived): run.py wav/transcribe/tiles --rows {",".join(left[:40])}')
        return
    if os.path.exists(os.path.join(base, 'packing_list.html')):   # Step 6 deleted work/ after the list was delivered: never re-download 3-8 GB for it
        log(f'list already delivered and work/ cleaned for {os.path.basename(base)}: order files updated, NO re-download; a new list needs a manual rebuild (notes.tsv went with work/)')
        notify(f'{os.path.basename(base)}: resend after cleanup; order files saved, nothing re-downloaded.', 'Packing list resend'); return
    pl = (playlist if playlist is not None else get(b['showUrl'])).decode()
    lines = pl.split('\n'); t = 0.0; dur = None; st = []; urls = []; pdt = ''
    for l in lines:
        if l.startswith('#EXTINF:'): dur = float(l[8:].rstrip(','))
        elif l.startswith('#EXT-X-PROGRAM-DATE-TIME:') and not pdt: pdt = l[25:].strip()
        elif l and not l.startswith('#'):
            st.append(f'{len(urls)}\t{t:.3f}\t{dur}'); urls.append(l.strip()); t += dur or 0
    if len(urls) > MAX_SEGS: raise ValueError(f'playlist has {len(urls)} segments (limit {MAX_SEGS})')
    bad = next((bad_url(u) for u in urls if bad_url(u)), None)
    if bad: raise ValueError(f'playlist refused: {bad}')
    open(os.path.join(work, 'segtimes.tsv'), 'w').write('\n'.join(st) + '\n')
    open(os.path.join(work, 'seg_urls.txt'), 'w').write('\n'.join(urls) + '\n')
    open(os.path.join(work, 'stream_start.txt'), 'w').write(pdt + '\n')
    log(f'playlist: {len(urls)} segments, {t/60:.1f} min, starts {pdt}')
    done = [0]; total = [0]
    def dl(n):
        p = os.path.join(work, 'seg', f'{n}.ts')
        if os.path.exists(p) and os.path.getsize(p) > 0: return
        for attempt in range(3):
            if total[0] > MAX_BYTES: return    # past the cap: stop writing, the rest stay missing and process() fails below
            try:
                data = get(urls[n], 120)
                if data: open(p, 'wb').write(data); total[0] += len(data); break
            except Exception as e:
                if attempt == 2: log(f'segment {n} failed: {e}')
        done[0] += 1
        if done[0] % 250 == 0: log(f'{done[0]}/{len(urls)} segments')
    with ThreadPoolExecutor(16) as ex: list(ex.map(dl, range(len(urls))))
    if total[0] > MAX_BYTES: raise ValueError(f'download stopped at {total[0] >> 30} GB (limit {MAX_BYTES >> 30} GB); seg/ kept, no DOWNLOAD_DONE')
    missing = [n for n in range(len(urls)) if not os.path.exists(os.path.join(work, 'seg', f'{n}.ts'))]
    log(f'download finished: {len(urls)-len(missing)}/{len(urls)} segments in {work}/seg' + (f', MISSING {len(missing)}' if missing else ''))
    if missing and len(missing) == len(urls):   # nothing arrived (expired signatures, full disk): no DOWNLOAD_DONE, so the next Send tries again
        log('NOTHING downloaded; a fresh Send from the Shipments page will try again'); refund(code, b, log); return
    open(os.path.join(work, 'DOWNLOAD_DONE'), 'w').write(f'{len(missing)} missing\n')
    enqueue_prep(work, log)

NONE = type(None)
ITEM = {'i': int, 'shipmentId': (str, int), 'buyer': str, 'orderNo': (str, int), 'listing': (str, NONE), 'vttStart': (int, float, NONE), 'cents': (int, float, NONE)}
SHIP = {'shipmentId': (str, int), 'buyer': (str, NONE), 'tracking': (str, NONE), 'courier': (str, NONE), 'method': (str, NONE), 'qty': (int, NONE), 'isFirst': bool}
OPTIONAL = {'vttStart', 'cents'}   # read with .get(); every other key is indexed with [] by process() or completeness_problem()

def validate(b):
    """The first thing wrong with the bundle's shape (keys and types that process(), charge() and completeness_problem()
    rely on), else None. Runs before the show link is fetched, before the credit is charged and before the 200."""
    if not isinstance(b, dict): return 'not an object'
    if not isinstance(b.get('showUrl'), str) or not b['showUrl']: return 'no showUrl'
    if not isinstance(b.get('showTitle'), (str, NONE)): return 'showTitle is not text'
    live = b.get('livestream')
    if not isinstance(live, (dict, NONE)): return 'livestream is not an object'
    if live and (not isinstance(live.get('id'), (str, int, NONE)) or not isinstance(live.get('startTime'), (int, float, NONE))): return 'livestream id or startTime has the wrong type'
    if not isinstance(b.get('items'), list) or not b['items']: return 'no items'
    if not isinstance(b.get('shipments', []), list): return 'shipments is not a list'
    for what, rows, spec in (('item', b['items'], ITEM), ('package', b.get('shipments', []), SHIP)):
        for n, r in enumerate(rows):
            if not isinstance(r, dict): return f'{what} {n} is not an object'
            for k, t in spec.items():
                if k not in r and k not in OPTIONAL: return f'{what} {n} has no {k}'
                v = r.get(k)
                if not isinstance(v, t) or (t is not bool and isinstance(v, bool)): return f'{what} {n}: {k} is {type(v).__name__}'
    return None

def completeness_problem(b):
    """Return a short reason if the bundle cannot be complete, else None."""
    items, ships = b['items'], b.get('shipments') or []
    if not ships: return 'no package list'
    expected = sum(int(s.get('qty') or 0) for s in ships)
    if expected and len(items) != expected:
        return f'{len(items)} orders received but the packages hold {expected}'
    per = {}
    for it in items: per[str(it['shipmentId'])] = per.get(str(it['shipmentId']), 0) + 1
    for s in ships:
        got, want = per.get(str(s['shipmentId']), 0), int(s.get('qty') or 0)
        if want and got != want: return f"package for {s.get('buyer')} has {got} of {want} orders"
    missing_vtt = sum(1 for it in items if it.get('vttStart') is None)
    if missing_vtt: return f'{missing_vtt} orders have no receipt timestamp'
    return None

MAX_BODY = 8 * 1024 * 1024          # a 400-order bundle is ~100 KB; anything near this is not a bundle
RATE_N, RATE_WINDOW = 10, 60.0      # POST /show per client IP per minute
RATE = {}; RATE_LOCK = threading.Lock()

def rate_limited(ip, now=None):
    """True if this IP already sent RATE_N bundles in the last RATE_WINDOW seconds. In memory, a few entries."""
    now = now if now is not None else time.monotonic()
    with RATE_LOCK:
        hits = [t for t in RATE.get(ip, []) if now - t < RATE_WINDOW]
        if len(hits) >= RATE_N: RATE[ip] = hits; return True
        hits.append(now); RATE[ip] = hits
        if len(RATE) > 1000:  # forget idle IPs so the dict cannot grow
            for k in [k for k, v in RATE.items() if now - v[-1] >= RATE_WINDOW]: del RATE[k]
        return False

class H(BaseHTTPRequestHandler):
    """Public server: POST /show from the extension, GET / as a liveness check. Nothing under /admin."""
    def ip(self):
        # Tailscale (Funnel or Serve) proxies from 127.0.0.1 and appends the real client to X-Forwarded-For;
        # the last entry is the one the proxy added, earlier ones could be made up by the client. A direct
        # connection (LAN, tailnet: the server binds 0.0.0.0) gets no say: its header would give it a fresh
        # rate-limit bucket per request and a made-up address in every log line.
        xff = self.headers.get('X-Forwarded-For', '') if self.client_address[0] in ('127.0.0.1', '::1') else ''
        return (xff.rsplit(',', 1)[-1].strip() if xff else '') or self.client_address[0]
    def log(self, m): print(datetime.datetime.now().strftime('%H:%M:%S'), f'[{self.ip()}]', m, flush=True)
    def _cors(self):
        origin = self.headers.get('Origin', '')
        if origin in ('https://www.whatnot.com', 'https://whatnot.com'):
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Packing-Token, X-Packing-Folder')
            self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.send_header('Content-Length', '0'); self.end_headers()
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self._cors(); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(body)))
        if self.close_connection: self.send_header('Connection', 'close')
        self.end_headers(); self.wfile.write(body)
    def _body(self):
        """The request body, or None after answering 411/413 (the body is never read when it is too big)."""
        try: n = int(self.headers.get('Content-Length', ''))
        except ValueError: n = -1
        if n < 0: self.close_connection = True; self._send(411, {'ok': False, 'message': 'Content-Length required'}); return None
        if n > MAX_BODY:
            self.close_connection = True  # the unread body would otherwise be parsed as the next request
            self.log(f'REFUSED body of {n/1e6:.0f} MB on {self.path} (limit {MAX_BODY >> 20} MB)')
            self._send(413, {'ok': False, 'message': f'Bundle too large (over {MAX_BODY >> 20} MB).'}); return None
        return self.rfile.read(n)
    def do_POST(self):
        if not self.path.startswith('/show'): return self._send(404, {'ok': False, 'message': 'not found'})
        if rate_limited(self.ip()):
            self.log(f'RATE LIMITED: more than {RATE_N} sends in {RATE_WINDOW:.0f} s')
            return self._send(429, {'ok': False, 'message': 'Too many sends in a minute. Wait a moment and click again.'})
        code = self.headers.get('X-Packing-Token', '')
        client, _rec = client_for(code)
        if not client:
            self.log(f'REFUSED: wrong or disabled access code {code[:4] + "..." if code else "(none)"}')
            return self._send(403, {'ok': False, 'message': 'wrong or disabled access code'})
        raw = self._body()
        if raw is None: return
        try: b = json.loads(raw); problem = validate(b)
        except Exception as e: problem = f'not JSON ({e})'
        if problem:
            self.log(f'REFUSED {client}: bad bundle: {problem}')
            return self._send(400, {'ok': False, 'message': f'bad bundle: {problem}'})
        problem = completeness_problem(b)
        if problem:
            self.log(f"REJECTED incomplete bundle from {client} for {b.get('showTitle')!r}: {problem}")
            return self._send(422, {'ok': False, 'message': f'Not all orders were on the page: {problem}. Reload the Shipments page and click again.'})
        bad = bad_url(b['showUrl'])
        if bad:
            self.log(f'REFUSED {client}: show link {bad}')
            return self._send(400, {'ok': False, 'message': 'The show link is not a Whatnot video link. Reload the Shipments page and click again.'})
        try:
            pl = get(b['showUrl'], 10)   # 10 s: the extension gives up at 30 s (background.js); the body goes to process(), one fetch per send
        except Exception as e:
            self.log(f'REFUSED {client}: show link did not work: {e!r}')
            return self._send(409, {'ok': False, 'message': 'The show link did not work (expired?). Reload the Shipments page and click again.'})
        if not show_key(b):   # no livestream on the rows and no show filter in the page address: nothing to key the ledger or the folder on
            self.log(f"REFUSED {client}: bundle has no show id" if client != 'master' else 'WARNING: bundle has no show id; a resend will land in a new folder')
            if client != 'master': return self._send(400, {'ok': False, 'message': 'The page did not say which show this is. Open the Shipments page from the show (filtered to that show) and click Send again.'})
        ok, note, reuse = charge(code, client, b)
        hint = self.headers.get('X-Packing-Folder', '').strip()
        if client == 'master' and hint and re.fullmatch(r'[A-Za-z0-9 _-]{1,60}', hint): reuse = os.path.join(A.out, hint)
        if not ok:
            self.log(f"REFUSED {client}: {note}")
            return self._send(402, {'ok': False, 'message': note})
        self.log(f"received from {client}: {b.get('showTitle')!r}: {len(b['items'])} items, {len(b.get('shipments', []))} packages" + (f"  WARNING: page reported {b['itemsSold']} items sold" if b.get('itemsSold') and b['itemsSold'] != len(b['items']) else '') + (f"  WARNING: status filter {b['statusFilter']!r}, shipped orders included; folder marked (all orders)" if all_orders(b) else '') + (f'  ({note})' if note else ''))
        log = self.log; cc = code if client != 'master' else None
        def run():
            try: process(b, log, client, cc, reuse, pl)
            except Exception as e: log(f'PROCESSING FAILED: {e!r} (bundle kept in {INBOX})'); refund(cc, b, log)
        threading.Thread(target=run, daemon=True).start()
        self._send(200, {'ok': True, 'message': f"Got it: {len(b['items'])} orders. Your packing list is on its way. {note}".strip()})
    def do_GET(self):
        if self.path.startswith('/admin'): return self._send(404, {'ok': False, 'message': 'not found'})
        self._send(200, {'ok': True, 'message': 'packing-list receiver up'})
    def log_message(self, *a): pass

class HAdmin(H):
    """Admin server, bound to 127.0.0.1:--admin-port. Not on the Funnel: reach it on this Mac, or from the tailnet
    through `tailscale serve` on a port Funnel is not enabled for (README). Only /admin and /admin/api/*."""
    def _admin_ok(self):
        return bool(A.token) and same_token(self.headers.get('X-Packing-Token', ''), A.token)
    def do_OPTIONS(self): self._send(404, {'ok': False, 'message': 'not found'})
    def do_GET(self):
        if self.path == '/admin' or self.path == '/admin/':
            body = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'admin.html'), 'rb').read()
            self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body); return
        if self.path.startswith('/admin/api/'):
            if not self._admin_ok(): self.log('admin: wrong master code'); return self._send(403, {'ok': False, 'message': 'wrong master code'})
            if self.path == '/admin/api/state':
                try: log = open(os.path.expanduser('~/Library/Logs/whatnot-receiver.log')).read().splitlines()[-60:]
                except Exception: log = []
                return self._send(200, {'ok': True, 'clients': load_ledger()['clients'], 'log': log})
        self._send(404, {'ok': False, 'message': 'not found'})
    def do_POST(self):
        if not self.path.startswith('/admin/api/'): return self._send(404, {'ok': False, 'message': 'not found'})
        if not self._admin_ok(): self.log('admin: wrong master code'); return self._send(403, {'ok': False, 'message': 'wrong master code'})
        raw = self._body()
        if raw is None: return
        try: body = json.loads(raw or b'{}')
        except Exception: return self._send(400, {'ok': False, 'message': 'bad request'})
        import secrets
        with LOCK:
            d = load_ledger(); cl = d['clients']
            if self.path == '/admin/api/add':
                name = (body.get('name') or '').strip()
                if not name: return self._send(400, {'ok': False, 'message': 'name is required'})
                code = secrets.token_hex(8)
                cl[code] = {'name': name, 'credits': max(0, int(body.get('credits') or 0)), 'used': 0, 'active': True, 'created': datetime.date.today().isoformat(), 'shows': {}}
                save_ledger(d); self.log(f'admin: added client {name!r}'); return self._send(200, {'ok': True, 'code': code})
            c = cl.get(body.get('code', ''))
            if not c: return self._send(404, {'ok': False, 'message': 'no such client'})
            if self.path == '/admin/api/credit':
                c['credits'] = max(0, c.get('credits', 0) + int(body.get('n') or 0)); save_ledger(d)
                self.log(f"admin: {c['name']} credits -> {c['credits']}"); return self._send(200, {'ok': True, 'credits': c['credits']})
            if self.path == '/admin/api/toggle':
                c['active'] = not c.get('active', True); save_ledger(d)
                self.log(f"admin: {c['name']} {'enabled' if c['active'] else 'disabled'}"); return self._send(200, {'ok': True, 'active': c['active']})
        self._send(404, {'ok': False, 'message': 'not found'})

if __name__ == '__main__':
    if not A.token: A.token = load_token(A.token_file)
    print('master code: ' + ('from --token (visible in ps; use --token-file)' if '--token' in sys.argv else f'from {A.token_file}' if A.token else f'NONE ({A.token_file} missing or empty): admin API off, only client codes accepted'), flush=True)
    print(f'listening on http://0.0.0.0:{A.port}/show  -> {A.out}  (scratch in {A.work_root})' + ('  (prep off)' if A.no_prep else f'  (prep: {os.path.normpath(RUN_PY)})'), flush=True)
    if A.admin_port:
        admin = ThreadingHTTPServer(('127.0.0.1', A.admin_port), HAdmin)
        threading.Thread(target=admin.serve_forever, daemon=True).start()
        print(f'admin on http://127.0.0.1:{A.admin_port}/admin  (localhost only; tailnet via tailscale serve, see README)', flush=True)
    ThreadingHTTPServer(('0.0.0.0', A.port), H).serve_forever()
