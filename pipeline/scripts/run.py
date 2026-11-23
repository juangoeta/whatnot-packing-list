#!/usr/bin/env python3
"""Whatnot packing-list pipeline, one entry point. Run from a show's work/ folder (or pass --work).

  python3 run.py status                 what is done in this work/ folder
  python3 run.py wav [--rows 0-19,40]   wav/NNN.wav, P-45..P+35 (t0 = P-45), for lag measurement + review
  python3 run.py transcribe             whisper-cli on every wav lacking a .json (one call, model loads once)
  python3 run.py lag [--all]            match callouts to orders, print median lag, write lag.txt + lag_report.txt + ghosts/silent/chains.tsv
  python3 run.py tiles [--lag N]        tiles/NNN.jpg 4x3 + frames/NNN_{1,2,3}.jpg (3 sharp native frames); offset from lag.txt
  python3 run.py recut [--budget S]     rc/NNN.wav.txt + rc/NNN.jpg (wider window) for the rows in recut.tsv, re-match them, re-tile the matched; capped at 2 min
  python3 run.py prep                   wav + transcribe + lag + recut + tiles
  python3 run.py show 12 13 14          transcript of those rows with offsets from P; >> marks buyer/number lines
  python3 run.py chunks [--size 20]     notes_fixed.tsv (fixed-title rows + giveaways, no review) and chunk1..N.txt of ~20 rows, sorted by sale time + REVIEW.md
  python3 run.py watch                  one line per event while agents write notes_N.tsv: DONE / STALLED / ALL DONE / TIMEOUT (P17)
  python3 run.py missing                chunk rows no notes file has yet -> missing.txt (finish them by hand after a STALLED agent)
  python3 run.py merge                 notes_*.tsv -> notes.tsv; fails if a row is missing, duplicated, or flagged without a category
  python3 run.py categorize [--write]   turn old free-text flags into 'category: detail' (data/flag_categories.json)
  python3 run.py fetch 120-200,3400-60  download missing segments for stream-time spans (seg_urls.txt link must be fresh)
  python3 run.py normalize [--dry-run]  rewrite notes.tsv product texts to catalog names; list the unmatched
  python3 run.py catalog list|add|test  the product catalog (data/catalog.json)
  python3 run.py learn-buyers [--write] propose whisper spellings for unmatched buyers (data/buyers.json)
  python3 run.py build [--title ..]     packing_list.{csv,md,html} in the show folder (parent of work/)

Files in work/ (written by the receiver): rows.tsv  i shipmentId buyer orderNo listing cents vttStart createdAt
  ship.tsv  shipmentId buyer tracking courier method qty isFirst      segtimes.tsv  n start dur (cumulative)
  seg/N.ts  show.json  livestream.json  stream_start.txt (HLS program date time)
Purchase time on the stream timeline: P = vttStart + 30.
Every stage skips work that already exists and says so. Python 3.9+, ffmpeg and whisper-cli on PATH
(or in /opt/homebrew/bin).
"""
import argparse, bisect, csv, datetime, glob, json, os, re, shutil, statistics, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.expanduser('~/.cache/whisper/ggml-small.en.bin')
TZ_NAME = 'America/New_York'
WAV_T0 = -45          # wav clip starts at P-45
WAV_LEN = 80          # ... and lasts 80 s (to P+35)
TILE_SPAN = 18        # tiles start at P + lag_arg - 18, one frame every 3 s, 12 frames (36 s)
FRAME_WIN = (-9, 3)   # candidate frames, 1/s, from P + lag_arg - 9 to P + lag_arg + 3 (the item is on camera just before the callout)
N_FRAMES = 3          # frames kept per order: the sharpest three, 2 s apart, visibly different
RECUT_WIN = {'late': (-200, 45), 'bundle': (-300, 25)}   # P06: wider audio (from P) for a row whose callout was never heard (late payment) / a mystery bag listed for minutes
RECUT_BUDGET = 120    # P06: seconds of prep the recut step may add; clips past it are left to the agents (`recut --budget 0` cuts them all)
RECUT_RATE = 0.05     # P06: whisper-cli seconds per clip second, small.en on 4 threads (Oct 5, 2026: 11.2 s for a 245 s clip)
RECUT_THREADS = 4
ENV = dict(os.environ, PATH='/opt/homebrew/bin:/usr/local/bin:' + os.environ.get('PATH', ''))

DATA = os.path.join(os.path.dirname(HERE), 'data')
BUYERS_F = os.path.join(DATA, 'buyers.json'); CATALOG_F = os.path.join(DATA, 'catalog.json')
SLIP_NOTE_F = os.path.join(DATA, 'slip_note.txt')   # the thank-you line at the foot of every packing slip (build --slip-note sets it once)
SLIP_NOTE_DEFAULT = 'Thank you for your order!'
BUNDLE_WORDS_F = os.path.join(DATA, 'bundle_words.json')   # P06: phrases that mean "opaque bag, contents listed before the sale" (not bare "bundle": Oct 5 said "bundle of five" on 159 rows)

def load_buyers():
    """username -> list of regex alternatives whisper produced for it (data/buyers.json)."""
    try: return json.load(open(BUYERS_F))
    except FileNotFoundError: return {}
BUYERS = load_buyers()
NUM2WORD = {str(k): v for k, v in enumerate(['zero','one','two','three','four','five','six','seven','eight','nine','ten','eleven','twelve','thirteen','fourteen','fifteen','sixteen','seventeen','eighteen','nineteen','twenty'])}
def buyer_rx(u, alts):
    """Regex for a buyer with learned spellings: those, plus the 4-letter prefix (unanchored, the same fallback an unknown buyer gets)
    and the username as spoken (words split on _, digits and camel case; a digit run is its number word: 4_roses -> "four roses";
    under 4 letters gets \\b anchors like spelling_rx), so a clear callout matches when buyers.json holds only mishearings (P16).
    Each extra is left out when the spellings so far already match it: most patterns (and the lag runtime) stay as they were."""
    ws = [NUM2WORD.get(x, x) for x in re.findall(r'[A-Z]?[a-z]+|\d+', u) if not x.isdigit() or x in NUM2WORD]; s = ' '.join(ws).lower(); p = u[:4].lower()
    out = list(alts)
    for plain, rx, ok in ((p, re.escape(p), len(p) == 4 and p.isalpha()), (s, (r'\b' + s + r'\b') if len(re.sub(r'[^a-z]', '', s)) < 4 else s, re.search(r'[a-z]', s))):
        if ok and not (out and re.search('|'.join(out), plain)): out.append(rx)
    return '|'.join(out)
class BuyerPat(dict):
    """username -> regex used by lag/show/evidence (buyer_rx); a buyer with no entry matches by the raw 4-letter prefix."""
    def __missing__(self, u): return re.escape(u[:4])
def buyer_pat(buyers): return BuyerPat({u: buyer_rx(u, alts) for u, alts in buyers.items() if alts})
BUYER_PAT = buyer_pat(BUYERS)

def load_catalog():
    try: return json.load(open(CATALOG_F))
    except FileNotFoundError: return {'sections': [], 'products': {}, 'rules': []}
def save_catalog(c): json.dump(c, open(CATALOG_F, 'w'), indent=1, ensure_ascii=False)
QTY_RX = re.compile(r'^\s*(\d+)\s*x\s+', re.I)
def catalog_match(cat, text):
    """First rule whose regex matches the product text (leading 'Nx ' stripped). Returns (rule, qty_prefix) or (None, qty)."""
    m = QTY_RX.match(text); body = text[m.end():] if m else text; qty = int(m.group(1)) if m else None
    for rule in cat['rules']:
        pats = rule['match'] if isinstance(rule['match'], list) else [rule['match']]
        if any(re.search(rx, body) or re.search(rx, text) for rx in pats): return rule, qty
    return None, qty

FLAGCAT_F = os.path.join(DATA, 'flag_categories.json')
def load_flagcats():
    try: d = json.load(open(FLAGCAT_F))
    except Exception: d = {'revise': {}, 'note': {}}
    return {c: ('revise', lbl) for c, lbl in d.get('revise', {}).items()} | {c: ('note', lbl) for c, lbl in d.get('note', {}).items()}
FLAGCATS = load_flagcats()
def parse_flag(text):
    """'shade: not readable' -> ('shade', 'revise', 'not readable'). A bare known category ('giveaway') works too.
    Unknown or uncategorised text -> ('item', 'revise', text): old notes still land in the revise list."""
    t = (text or '').strip()
    if not t: return None
    head, sep, detail = t.partition(':')
    key = head.strip().lower()
    if key in FLAGCATS: return key, FLAGCATS[key][0], detail.strip() if sep else ''
    return 'item', 'revise', t
# keyword rules that turn old free-text flags into categories (run.py categorize); first match wins
CATEGORIZE_RULES = [('giveaway', r'giveaway|givy'), ('expiry', r'expir|best by|exp\b'), ('bag', r'in bag|in basket|basket|bag:|uncounted'),
                    ('cancel', r'cancel'), ('missing', r'no video|black|dropout|no frames|stream (drop|cut)'),
                    ('shade', r'shade|colou?r not|scent not|variant|which one'), ('qty', r'\bqty\b|quantit|how many|bundle of|count'),
                    ('timing', r'number not (said|heard)|matched by time|not in tile|callout at'), ('item', r'.')]

# ---------- shared helpers ----------
def say(*a): print(*a, flush=True)
def die(*a): say('ERROR:', *a); sys.exit(1)

def read_tsv(path):
    return [l.rstrip('\n').split('\t') for l in open(path) if l.strip()]

class Work:
    """The work/ folder: rows, segments, and paths derived from them."""
    def __init__(self, d, need_rows=True):
        self.dir = os.path.abspath(d)
        if not os.path.exists(self.p('rows.tsv')):
            if need_rows: die(f'no rows.tsv in {self.dir}; run from a show work/ folder or pass --work')
            self.rows = []
        else: self.rows = read_tsv(self.p('rows.tsv'))
        self._byid = {r[0]: r for r in self.rows}   # row ids stay stable across resends, so they can have gaps
        self._segs = None
    def p(self, *a): return os.path.join(self.dir, *a)
    @property
    def segs(self):
        if self._segs is None:
            if not os.path.exists(self.p('segtimes.tsv')): die('no segtimes.tsv (the recording was not downloaded)')
            self._segs = [(int(n), float(s), float(d)) for n, s, d in read_tsv(self.p('segtimes.tsv'))]
        return self._segs
    @property
    def starts(self): return [s for _, s, _ in self.segs]
    def P(self, row): return float(row[6]) + 30
    def row(self, i):
        try: return self._byid[str(int(i))]
        except KeyError: die(f'no row {i} in rows.tsv')
    def show_folder(self):
        """The Desktop show folder (deliverables go there). work/ lives outside iCloud, so the receiver
        leaves its address in show_folder.txt; fall back to the parent folder for the old layout."""
        try:
            p = open(self.p('show_folder.txt')).read().strip()
            if p: return p
        except FileNotFoundError: pass
        return os.path.dirname(self.dir)
    def meta(self):
        """show title, date string, localStorage key, stream start (aware datetime) from the receiver's files."""
        show = {}; ls = {}
        try: show = json.load(open(self.p('show.json')))
        except Exception: pass
        try: ls = json.load(open(self.p('livestream.json'))) or {}
        except Exception: pass
        title = (show.get('showTitle') or ls.get('title') or os.path.basename(self.show_folder())).strip()
        start = None
        try:
            iso = open(self.p('stream_start.txt')).read().strip()
            if iso: start = datetime.datetime.fromisoformat(iso.replace('Z', '+00:00'))
        except Exception: pass
        ms = ls.get('startTime')
        d = datetime.datetime.fromtimestamp(ms / 1000, tz()) if ms else (start.astimezone(tz()) if start else None)
        date = d.strftime('%b %-d, %Y') if d else os.path.basename(self.show_folder())
        key = d.strftime('%m%d') if d else re.sub(r'\W+', '', date)
        # whatever the folder name carries past <Mon D> ('Sep 24 Makeup', 'Sep 25 - Maria', 'Sep 23 show2') joins the key,
        # so two lists of one day never share their packed checkboxes: 0924-makeup, 0925-maria, 0923-show2
        name = os.path.basename(self.show_folder()); ds = d.strftime('%b %-d') if d else ''
        rest = re.sub(r'\W+', '', name[len(ds):] if ds and name.startswith(ds) else name if d else '').lower()
        if rest: key += '-' + rest
        return title, date, key, start

def tz():
    import zoneinfo
    return zoneinfo.ZoneInfo(TZ_NAME)

def hms(s):
    """Seconds on the stream timeline -> h:mm:ss, the position on Whatnot's replay scrubber (D02)."""
    s = int(s); return f'{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}'

def sale_clock(start, P, created, local):
    """'10:13:02 AM' in America/New_York: stream start + P when the receiver saved the HLS program date time,
    else Whatnot's createdAt header (RFC 2822) converted; the raw string if neither parses."""
    if start: return (start + datetime.timedelta(seconds=P)).astimezone(local).strftime('%-I:%M:%S %p')
    try:
        import email.utils
        return email.utils.parsedate_to_datetime(created).astimezone(local).strftime('%-I:%M:%S %p')
    except Exception: return created

def replay_parts(w):
    """How many recordings the HLS playlist stitched together: a replay restart shows up in seg_urls.txt as a new
    path (Sep 23 sunglasses: 3 parts). 1 when the file is missing. Replay positions count from the first start."""
    try: dirs = [l.rsplit('/', 1)[0] for l in open(w.p('seg_urls.txt')).read().split('\n') if l.strip()]
    except FileNotFoundError: return 1
    return 1 + sum(1 for a, b in zip(dirs, dirs[1:]) if a != b)

def parse_rows(spec, n):
    """'0-19,40' -> set of ints; None -> all rows. (n is unused: ids added by a resend can exceed the row count.)"""
    if not spec: return None
    out = set()
    for part in spec.split(','):
        a, _, b = part.partition('-')
        out.update(range(int(a), int(b or a) + 1))
    return {i for i in out if i >= 0}

def cut(w, i, a, b, tag):
    """Concatenate the segments covering stream time [a, b] into cat/<i>_<tag>.ts. Returns (path, base_time)."""
    starts = w.starts; segs = w.segs
    lo = max(bisect.bisect_right(starts, a) - 1, 0); hi = min(bisect.bisect_right(starts, b) - 1, len(segs) - 1)
    os.makedirs(w.p('cat'), exist_ok=True)
    path = w.p('cat', f'{i}_{tag}.ts'); missing = []
    with open(path, 'wb') as out:
        for n in range(lo, hi + 1):
            sp = w.p('seg', f'{n}.ts')
            if os.path.exists(sp): out.write(open(sp, 'rb').read())
            else: missing.append(n)
    if missing: say(f'  row {i}: {len(missing)} segment(s) missing ({missing[0]}..{missing[-1]}); clip may be short')
    return path, starts[lo]

def ffmpeg(args):
    r = subprocess.run(['ffmpeg', '-v', 'error', '-y'] + args, check=False, env=ENV, capture_output=True, text=True)
    noise = re.compile(r'co located POCs|mmco: unref|reference picture missing|Missing reference picture|Application provided invalid')  # harmless h264 decoder chatter
    for l in r.stderr.splitlines():
        if l.strip() and not noise.search(l): say('  ffmpeg:', l)
    return r.returncode   # callers count failures; 0 = ok

def tile_arg_from_lag(lag):
    """Tile offset used on past shows: lag +6 -> 5, +5.5 -> 5, +6.4 -> 5, -10 -> -11; small lags (-2..+2) -> -3
    so the sale lands on frame 7-8 with extra context before it."""
    if -2 <= lag < 2: return -3
    return int(round(lag)) - 1

def read_lag(w):
    try: return float(open(w.p('lag.txt')).read().split()[0])
    except Exception: return None

def read_row_lags(w):
    """{row index: lag} from lag_rows.tsv, or None when the lag did not drift (all rows share lag.txt)."""
    try: per = {int(i): float(v) for i, v in read_tsv(w.p('lag_rows.tsv'))}
    except Exception: return None
    return per if per and max(per.values()) - min(per.values()) > 6 else None

# ---------- stages ----------
def cmd_status(w, a):
    n = len(w.rows)
    def count(pat): return len(glob.glob(w.p(pat)))
    lag = read_lag(w)
    say(f'{w.dir}')
    say(f'rows          {n}   (ship.tsv: {len(read_tsv(w.p("ship.tsv"))) if os.path.exists(w.p("ship.tsv")) else "missing"} packages)')
    say(f'review        {split_text(w.rows)}' + (f'   notes_fixed.tsv {len(read_tsv(w.p("notes_fixed.tsv")))} rows' if os.path.exists(w.p('notes_fixed.tsv')) else ''))   # P05
    say(f'segments      {count("seg/*.ts")}' + ('  DOWNLOAD_DONE' if os.path.exists(w.p('DOWNLOAD_DONE')) else '  (download not finished)'))
    say(f'audio         {len({f.split(".")[0] for f in os.listdir(w.p("wav"))} if os.path.isdir(w.p("wav")) else [])} / {n} cut (wav files are deleted once transcribed)')
    say(f'transcripts   {count("wav/*.wav.json")} / {n}')
    say(f'lag           {"%+.1f s" % lag if lag is not None else "not measured"}')
    say(f'tiles         {count("tiles/*.jpg")} / {n}   frames {count("frames/*_1.jpg")} / {n}')
    drop, gaps = dropout_rows(w); say('dropouts      ' + (f'{len(gaps)} gaps, {len(drop)} rows inside -> dropouts.tsv' if os.path.exists(w.p('dropouts.tsv')) else 'not checked (lag or tiles has not run)'))   # P10
    ch = sorted(glob.glob(w.p('chunk[0-9]*.txt'))); nt = sorted(glob.glob(w.p('notes_[0-9]*.tsv')))
    prog = chunk_progress(w); ptxt = ' '.join(f'{n}:{len(h)}/{len(i)}' for n, (i, h, _, _) in prog.items())   # P17
    say(f'chunks        {len(ch)}   notes_N.tsv {len(nt)}   notes.tsv {"%d rows" % len(read_tsv(w.p("notes.tsv"))) if os.path.exists(w.p("notes.tsv")) else "missing"}' + (f'\n review done   {ptxt}' if prog else ''))
    out = os.path.join(w.show_folder(), 'packing_list.html')
    say(f'packing list  {"built " + datetime.datetime.fromtimestamp(os.path.getmtime(out)).strftime("%b %-d %H:%M") if os.path.exists(out) else "not built"}')

def cmd_wav(w, a):
    w.segs  # fail once, here, if the recording is missing (not inside every worker thread)
    only = parse_rows(a.rows, len(w.rows)); os.makedirs(w.p('wav'), exist_ok=True)
    sel = [r for r in w.rows if only is None or int(r[0]) in only]
    todo = [r for r in sel if not os.path.exists(w.p('wav', f'{int(r[0]):03d}.wav')) and not os.path.exists(w.p('wav', f'{int(r[0]):03d}.wav.json'))]
    say(f'wav: {len(todo)} to cut, {len(sel) - len(todo)} exist (or already transcribed)')
    failed = []
    def work(r):
        i = int(r[0]); P = w.P(r); dest = w.p('wav', f'{i:03d}.wav')
        cat, base = cut(w, i, P + WAV_T0 - 3, P + WAV_T0 + WAV_LEN + 3, 'wav')
        if ffmpeg(['-ss', f'{max(P + WAV_T0 - base, 0):.3f}', '-t', str(WAV_LEN), '-i', cat, '-vn', '-ac', '1', '-ar', '16000', dest]) or not os.path.exists(dest):
            failed.append(i)
            try: os.remove(dest)   # a partial wav would be transcribed as silence; leave the row undone so the next `wav` retries it
            except FileNotFoundError: pass
        os.remove(cat)
    with ThreadPoolExecutor(4) as ex: list(ex.map(work, todo))
    say('wav done' + (f'; {len(failed)} clips FAILED (rows {",".join(map(str, sorted(failed)))}): no transcript for them, see ffmpeg lines above' if failed else ''))
    return sorted(failed)

def cmd_transcribe(w, a):
    wavs = sorted(glob.glob(w.p('wav', '[0-9]*.wav')))   # ids run past 999 on big shows and after resends
    todo = [p for p in wavs if not os.path.exists(p + '.json')]
    say(f'transcribe: {len(todo)} to do, {len(wavs) - len(todo)} exist')
    if todo:
        if not os.path.exists(a.model): die(f'whisper model not found at {a.model}')
        subprocess.run(['whisper-cli', '-m', a.model, '-t', str(a.threads), '-np', '-otxt', '-oj'] + todo, check=False, env=ENV,
                       stdout=subprocess.DEVNULL if not a.verbose else None, stderr=subprocess.DEVNULL if not a.verbose else None)
        left = [p for p in todo if not os.path.exists(p + '.json')]
        if left: die(f'{len(left)} transcripts missing after whisper-cli (first: {left[0]}); run with --verbose')
    if not a.keep_wav:
        freed = 0
        for p in wavs:
            if os.path.exists(p + '.json'):
                try: freed += os.path.getsize(p); os.remove(p)
                except FileNotFoundError: pass   # Oct 9, 2026: two agents' `recut` runs raced on rc/*.wav and one crashed here
        say(f'transcribe done; {len(wavs)} wav files deleted ({freed / 1e9:.1f} GB), transcripts kept (--keep-wav to keep audio)')
    else: say('transcribe done')

def fix_nums(txt):
    """Oct 9, 2026: whisper writes a 3-digit listing number as "3.66", "$3.63", "3-11" or "1 0 2"; the matcher then hears
    "3". Join them before matching (only 1-3 hundreds, so prices like $12.50 are untouched). Round cents stay a price:
    "$1.50", "2.99", "$3.00" are what this seller charges, not listing numbers; "3 30 pm" is a time."""
    txt = re.sub(r'\$?\b([1-3])[.\-/](?!(?:00|50|99)\b)(\d{2})\b(?!\d)', r'\1\2', txt)   # Oct 13, 2026: also "1/24"
    txt = re.sub(r'\b([1-3]) (\d{2})\b(?! ?[ap]\.?m\b)(?!\d)', r'\1\2', txt)              # Oct 13, 2026: "2 22"
    return re.sub(r'\b([1-3]) (\d) (\d)\b', r'\1\2\3', txt)

def transcript_segments(w):
    """All transcript lines as (stream_time, text_lower, row) sorted by time; numbers joined by fix_nums."""
    P = {int(r[0]): w.P(r) for r in w.rows}; out = []
    for f in sorted(glob.glob(w.p('wav', '[0-9]*.wav.json'))):
        i = int(os.path.basename(f).split('.')[0])
        if i not in P: continue
        for s in json.load(open(f))['transcription']:
            out.append((P[i] + WAV_T0 + s['offsets']['from'] / 1000, fix_nums(s['text'].strip().lower()), i))
    out.sort(); return out

def match_rows(w, segs):
    """For each row, the best transcript line naming its buyer and/or listing number near P."""
    res = []; times = [s[0] for s in segs]
    for r in w.rows:
        i = int(r[0]); P = w.P(r); m_ = re.search(r'#(\d+)', r[4]); n = m_.group(1) if m_ else '-1'; givy = is_giveaway(r[4], r[5])
        bp = BUYER_PAT[r[2]]
        cands = []
        for t, txt, src in segs[bisect.bisect_left(times, P - 40):bisect.bisect_right(times, P + 45)]:
            d = t - P
            nums = set(re.findall(r'(?<![\d.$])(\d{1,3})(?![\d%])', txt))
            if n in NUM2WORD and re.search(r'number ' + NUM2WORD[n] + r'\b', txt): nums.add(n)
            hasn = n in nums; hasb = bool(re.search(bp, txt))
            if givy:
                if hasb: cands.append((d, txt, 'B'))
            elif hasn and hasb: cands.append((d, txt, 'NB'))
            elif hasn: cands.append((d, txt, 'N'))
        cands = [c for c in cands if c[2] != 'N'] or cands
        best = min(cands, key=lambda c: abs(c[0] - 6)) if cands else None
        res.append((i, r[2], r[4], best))
    return res

CALLOUT_PHRASE = re.compile(r"(?:thank you|thanks|congrats|congratulations|goes to|sold to|sold)\s+(?:so much\s+)?([a-z][a-z0-9_' ]{1,24}?)\s+(?:number|no\.?|#)?\s*(\d{1,3})\b")
def listing_num(listing):
    m = re.search(r'#(\d+)', listing or ''); return m.group(1) if m else None
def is_giveaway(listing, cents):
    """A giveaway row: the listing is a GIVY draw or the sale price is 0. One place for both match_rows and build."""
    return 'GIVY' in (listing or '').upper() or int(cents or 0) == 0

# P05: rows that need no video review. data/listing_patterns.json holds two regex lists: 'review' (random pulls, the
# product is only on the video) and 'fixed' (the title IS the product). GIVY/$0 rows are giveaways. A title in neither
# list goes to review and is named by `chunks`, so a new seller's naming is one line in the json, never a wrong pre-fill.
PATTERNS_F = os.path.join(DATA, 'listing_patterns.json')
def load_patterns():
    try: d = json.load(open(PATTERNS_F))
    except Exception: d = {}
    return {k: [re.compile(p, re.I) for p in d.get(k, [])] for k in ('review', 'fixed')}
PATTERNS = load_patterns()
GIVEAWAY_TEXT = 'Giveaway prize (see show setup)'
def listing_title(listing): return re.sub(r'\s*#\d+\s*$', '', listing or '').strip()   # the trailing #N is the item number, not the product
def row_kind(listing, cents):
    """'giveaway', 'fixed' (title is the product) or 'review'; None when the title matches no pattern (treated as review)."""
    if is_giveaway(listing, cents): return 'giveaway'
    t = listing_title(listing)
    if any(p.search(t) for p in PATTERNS['review']): return 'review'
    return 'fixed' if any(p.search(t) for p in PATTERNS['fixed']) else None
def split_rows(rows):
    """{'review': [...], 'fixed': [...], 'giveaway': [...], 'unknown': [titles]}: unknown titles are in 'review' too."""
    out = {'review': [], 'fixed': [], 'giveaway': [], 'unknown': []}
    for r in rows:
        k = row_kind(r[4], r[5])
        if k is None and listing_title(r[4]) not in out['unknown']: out['unknown'].append(listing_title(r[4]))
        out[k or 'review'].append(r)
    return out
def split_text(rows):
    s = split_rows(rows); return f"{len(rows)} rows: {len(s['review'])} to review, {len(s['fixed'])} fixed, {len(s['giveaway'])} giveaways"

def find_ghosts(w, segs, res):
    """Callouts on the mic that match no order. A callout is a known buyer's name (data/buyers.json pattern)
    directly followed by a listing number ("bar susan 122", "desi thank you number 118"), or a 'thank you NAME
    number N' phrase for a name the pipeline does not know. It counts as a ghost when no order for that
    buyer/number exists within +-4 min AND the number is in the range being sold around that time (within 12
    of the listing numbers of orders +-5 min away), which keeps prices, countdowns and "12 months" out.
    P17: an unknown name needs a number that matches NO order at all; "sold by daisy fuentes 39" and "your
    first purchase number 18" are product names and asides, and #39/#18 were orders (Oct 5, 2026).
    Overlapping name patterns on the same number become one ghost ("lexidrz/lexi_120"). Also returns the
    silent orders as (row, kind): rows whose buyer was never heard around the sale, with why (P17):
    N number heard without the buyer, B buyer heard without the number, G giveaway with no winner read out
    (Whatnot draws it; no callout expected), - nothing heard at all (the only kind the seller's page counts)."""
    orders = {}; nums = {}; byP = []
    for r in w.rows:
        n = listing_num(r[4]); P = w.P(r)
        orders.setdefault((r[2], n), []).append(P); nums.setdefault(n, []).append(P)
        if n: byP.append((P, int(n)))
    byP.sort(); Ps = [p for p, _ in byP]
    near = lambda lst, t, win=240: any(abs(P - t) <= win for P in lst)
    def in_range(n, t):
        lo = bisect.bisect_left(Ps, t - 300); hi = bisect.bisect_right(Ps, t + 300)
        around = [m for _, m in byP[lo:hi]] or [m for _, m in byP[max(0, bisect.bisect_left(Ps, t) - 6):bisect.bisect_left(Ps, t) + 6]]
        return any(abs(int(n) - m) <= 12 for m in around)
    matched_lines = {(round(b[0] + w.P(w.row(i)), 1), b[1]) for i, _, _, b in res if b}
    found = {}   # (round(t), n) -> ghost
    def add(t, user, heard, n, txt, src):
        key = (round(t), n)
        for (kt, kn), g in found.items():
            if kn == n and abs(kt - t) <= 120 and (user in g['users'] or not user):
                return
        if key in found:
            if user and user not in found[key]['users']: found[key]['users'].append(user)
            return
        found[key] = {'t': round(t, 1), 'users': [user] if user else [], 'heard': heard, 'num': n, 'text': txt, 'src': src}
    for t, txt, src in segs:
        if (round(t, 1), txt) in matched_lines: continue
        hit = False
        for user, pat in BUYER_PAT.items():
            for m in re.finditer(r'(?:' + pat + r')\W+(?:thank you\W+|thanks\W+|number\W+|no\.?\W+|#\s*)?(\d{1,3})(?![\d%])', txt):
                n = m.group(1)
                if (user, n) in orders and near(orders[(user, n)], t): hit = True; continue
                if n in nums and near(nums[n], t): hit = True; continue           # that number IS an order nearby: a misheard name
                if not in_range(n, t): continue
                add(t, user, user, n, txt, src); hit = True
        if hit: continue
        for m in CALLOUT_PHRASE.finditer(txt):
            name, n = m.group(1).strip(), m.group(2)
            if n in nums: continue   # P17: no known buyer heard and the number IS an order (any time): a product name or an aside, not a ghost
            if not in_range(n, t): continue
            if re.fullmatch(r'(so much|guys|everyone|you|all|number|to|for)', name): continue
            add(t, None, name, n, txt, src)
    ghosts = sorted(found.values(), key=lambda g: g['t'])
    for g in ghosts: g['user'] = '/'.join(g['users'])
    times = [s[0] for s in segs]
    def kind(i, b):   # P17: why the row is silent; same +-40/45 s window as match_rows, which drops buyer-only lines for priced rows
        if b: return 'N'
        r = w.row(i); P = w.P(r); bp = BUYER_PAT[r[2]]
        if any(re.search(bp, txt) for _, txt, _ in segs[bisect.bisect_left(times, P - 40):bisect.bisect_right(times, P + 45)]): return 'B'
        return 'G' if is_giveaway(r[4], r[5]) else '-'
    silent = [(i, kind(i, b)) for i, _, _, b in res if not b or b[2] == 'N']
    return ghosts, silent

def row_lags(w, res, med):
    """Lag per row: median of the NB match offsets within +-10 min of the row's sale (at least 15 of them),
    because the recording's lag drifts over a long stream (Sep 26, 2026: -26 s at the start, +28 s at the end)."""
    pts = sorted((w.P(w.row(i)), b[0]) for i, _, _, b in res if b and b[2] == 'NB')
    Ps = [p for p, _ in pts]; out = {}
    for r in w.rows:
        i = int(r[0]); P = w.P(r); lo = bisect.bisect_left(Ps, P - 600); hi = bisect.bisect_right(Ps, P + 600)
        if hi - lo < 15:
            k = bisect.bisect_left(Ps, P); lo = max(0, k - 8); hi = min(len(pts), k + 8)
        vals = [d for _, d in pts[lo:hi]]
        out[i] = statistics.median(vals) if vals else med
    return out

CHAIN_FLAG = 'possible off-by-one'   # P12: the flag detail every row in a chain gets ("item: possible off-by-one (items #50-#70 shifted by -1)")
CHAIN_MIN = 3                        # rows heard at the same offset before a stretch counts as a chain (whisper mishears single numbers)
CHAIN_GAP = 3                        # unheard rows allowed between two heard rows of the same chain (whisper misses about a third of callouts)

def find_chains(w, segs, per):
    """P12: stretches of orders whose callouts are off by one listing number, the sign of a systematic shift
    (a failed payment, a skipped sticker: "every order was for the previous person"). For each numbered,
    non-giveaway row, the transcript line WITH THE BUYER'S NAME nearest the expected callout (P + lag, within
    15 s) that carries the order's number or a neighbour of it gives the offset: 0 for the own number, +-1 for
    the neighbour, None when no such line exists. Nearest line, not the whole window: a buyer who takes three
    items in a row has her next number on the mic 12 s later, and pooling the window would hide the shift.
    Rows are walked in sale order within one listing series (counters
    restart per series); CHAIN_MIN+ rows at the same non-zero offset, with at most CHAIN_GAP unheard rows
    between them and no row at offset 0 or the opposite sign inside, form a chain. Returns
    [{first, last, dir, series, first_item, last_item, rows, heard}] with row ids in sale order."""
    times = [t for t, _, _ in segs]; by_series = {}
    for r in w.rows:
        n, series = item_number(r[4])
        if n is None or is_giveaway(r[4], r[5]): continue
        by_series.setdefault(series, []).append((w.P(r), int(r[0]), n, r[2]))
    off = {}
    for lst in by_series.values():
        for P, i, n, buyer in lst:
            bp = BUYER_PAT[buyer]; c = P + per.get(i, 0); off[i] = None
            win = segs[bisect.bisect_left(times, c - 15):bisect.bisect_right(times, c + 15)]
            for t, txt, _ in sorted(win, key=lambda x: abs(x[0] - c)):
                if not re.search(bp, txt): continue
                heard = {int(m) for m in re.findall(r'(?<![\d.$])(\d{1,3})(?![\d%])', txt)} | {int(k) for k, wd in NUM2WORD.items() if re.search(r'number ' + wd + r'\b', txt)}
                d = 0 if n in heard else 1 if n + 1 in heard and n - 1 not in heard else -1 if n - 1 in heard and n + 1 not in heard else None
                if d is not None: off[i] = d; break
    chains = []
    for series, lst in by_series.items():
        lst.sort(); k = 0
        while k < len(lst):
            d = off.get(lst[k][1])
            if not d: k += 1; continue
            last = k; j = k + 1
            while j < len(lst) and j - last <= CHAIN_GAP:
                dj = off.get(lst[j][1])
                if dj == d: last = j
                elif dj is not None: break
                j += 1
            members = lst[k:last + 1]; heard = sum(1 for m in members if off.get(m[1]) == d)
            if heard >= CHAIN_MIN:
                chains.append({'first': members[0][1], 'last': members[-1][1], 'dir': d, 'series': series, 'first_item': members[0][2],
                               'last_item': members[-1][2], 'rows': [m[1] for m in members], 'heard': heard})
            k = last + 1
    chains.sort(key=lambda c: w.P(w.row(c['first'])))
    return chains

def chain_tag(c):
    return f"{CHAIN_FLAG} (items #{c['first_item']}-#{c['last_item']} shifted by {c['dir']:+d})"

def read_chains(w):
    """chains.tsv -> the list find_chains wrote (empty when lag has not run or found none)."""
    out = []
    try: lines = read_tsv(w.p('chains.tsv'))
    except FileNotFoundError: return out
    for f in lines:
        first, last, d, series, fi, li, nrows, heard, rows = (f + [''] * 9)[:9]
        out.append({'first': int(first), 'last': int(last), 'dir': int(d), 'series': series, 'first_item': int(fi), 'last_item': int(li),
                    'rows': [int(x) for x in rows.split(',') if x], 'heard': int(heard or 0)})
    return out

# ---------- P10: stream dropouts (no decode: Pillow over the existing tiles, the transcript JSON as is) ----------
DROP_LUMA = 20   # a tile cell (one frame, 3 s apart) is black when its mean luminance is under this of 255; Oct 5 (335 tiles): the darkest real cell was 86
BLANK_RX = re.compile(r'[\[(]\s*(blank[ _]?audio|silence|silent|no audio)\s*[\])]', re.I)   # whisper.cpp marks audio with nothing in it as [BLANK_AUDIO]

def tile_black_cells(path):
    """[bool] * 12 for the 4x3 cells of a tile in reading order. JPEG draft mode decodes at 1/8 size and a 4x3 box resize
    gives each cell's mean luminance: about 1 ms per tile."""
    from PIL import Image
    im = Image.open(path); im.draft('L', (im.width // 8, im.height // 8))
    return [v < DROP_LUMA for v in im.convert('L').resize((4, 3), Image.BOX).getdata()]

def transcript_blank(path):
    """True when the transcript JSON has no segment or only whisper's [BLANK_AUDIO] markers."""
    return all(not BLANK_RX.sub('', s['text']).strip() for s in json.load(open(path))['transcription'])

def find_dropouts(w):
    """Rows whose tile is black in all 12 frames AND whose transcript is empty: the stream dropped there (Sep 23: 3340-3462 s).
    Only the windows around orders are looked at: a gap between sales is invisible and does not matter for packing. Returns
    [(i, P, a, b)], a..b the black stretch on the stream timeline: the row's tile window, widened by black cells at the edges of
    the neighbouring tiles (3 s resolution). A missing tile or transcript is unknown, not a dropout."""
    targs = {}
    try: targs = {int(i): int(v) for i, v in read_tsv(w.p('tiles_rows.tsv'))}
    except Exception: pass
    arg = int(open(w.p('tiles_arg.txt')).read()) if os.path.exists(w.p('tiles_arg.txt')) else 0
    black = []; drops = []   # black: (a, b) stream spans of black cells in any tile; drops: rows black and silent
    for r in w.rows:
        i = int(r[0]); tile = w.p('tiles', f'{i:03d}.jpg'); tj = w.p('wav', f'{i:03d}.wav.json')
        if not os.path.exists(tile): continue
        cells = tile_black_cells(tile); t0 = w.P(r) + targs.get(i, arg) - TILE_SPAN
        if not any(cells): continue
        black += [(t0 + 3 * k, t0 + 3 * k + 3) for k, c in enumerate(cells) if c]
        if all(cells) and os.path.exists(tj) and transcript_blank(tj): drops.append((i, w.P(r), t0, t0 + 36))
    if not drops: return []
    black.sort(); spans = []
    for a, b in black:
        if spans and a <= spans[-1][1] + 0.01: spans[-1][1] = max(spans[-1][1], b)
        else: spans.append([a, b])
    return [(i, P, *next(([round(s[0], 1), round(s[1], 1)] for s in spans if s[0] <= a and b <= s[1]), [round(a, 1), round(b, 1)])) for i, P, a, b in drops]

def dropout_rows(w):
    """({row id: 'missing: no video A-B s'}, [(a, b)]): every row whose sale window (P+lag-10 .. P+lag+5) overlaps a gap in
    dropouts.tsv, and the distinct gaps. Empty when lag/tiles have not run or the show was clean."""
    try: gaps = sorted({(float(a), float(b)) for _, _, _, a, b in read_tsv(w.p('dropouts.tsv'))})
    except Exception: return {}, []
    per = read_row_lags(w); lag = read_lag(w) or 0.0; out = {}
    for r in w.rows:
        c = w.P(r) + (per.get(int(r[0]), lag) if per else lag)   # .get: a row added by a resend is not in lag_rows.tsv
        g = next((g for g in gaps if c - 10 < g[1] and g[0] < c + 5), None)
        if g: out[r[0]] = f'missing: no video {g[0]:.0f}-{g[1]:.0f} s'
    return out, gaps

def dropout_report(w):
    """Writes dropouts.tsv (i P kind a b) and returns report lines: none on a clean show, so lag_report.txt stays as it was.
    lag runs it (tiles may not exist yet on a fresh prep) and tiles runs it again at the end."""
    drops = find_dropouts(w)
    open(w.p('dropouts.tsv'), 'w').write(''.join(f'{i}\t{P:.1f}\tblack+silent\t{a:.1f}\t{b:.1f}\n' for i, P, a, b in drops))
    if not drops: return []
    rows, gaps = dropout_rows(w)
    return [f'STREAM DROPOUTS (black tile, empty transcript): {len(gaps)} gap(s), {len(rows)} orders inside -> dropouts.tsv; chunks pre-fills them as missing, the recap may recover them'] + \
           [f'   {a:.0f}-{b:.0f} s ({b - a:.0f} s): rows ' + ' '.join(i for i, f in rows.items() if f.endswith(f'{a:.0f}-{b:.0f} s')) for a, b in gaps]

def load_bundle_words():
    try: return json.load(open(BUNDLE_WORDS_F))['words']
    except FileNotFoundError: return []
BUNDLE_RX = re.compile('|'.join(r'\b' + re.escape(x) + r'\b' for x in load_bundle_words()) or r'(?!x)x')
def rc_reason(own_text, best, giveaway):
    """P06: why a row needs a wider clip: 'bundle' when its own transcript (P-45..P+35) has a mystery-bag phrase, 'late' when
    nothing was heard there (no number, no buyer; giveaways excluded, the draw says nothing about the prize), else None.
    A function of wav/ only, so every `lag` run computes the same window and `show` can place an existing clip."""
    if BUNDLE_RX.search(own_text): return 'bundle'
    return 'late' if best is None and not giveaway else None
def rc_lines(w, i, reason):
    """Lines of the extended clip rc/NNN.wav as (offset from P, text), or None when it is not transcribed yet."""
    p = w.p('rc', f'{i:03d}.wav.json')
    if not os.path.exists(p): return None
    a = RECUT_WIN[reason][0]
    return [(a + s['offsets']['from'] / 1000, fix_nums(s['text'].strip())) for s in json.load(open(p))['transcription']]
def rc_match(r, lines, lag):
    """Best buyer+number line of the extended clip, nearest the expected lag, as (d, text, 'NB') or None (same test as match_rows)."""
    n = listing_num(r[4]) or '-1'; bp = BUYER_PAT[r[2]]; c = []
    for d, txt in lines:
        txt = txt.lower(); nums = set(re.findall(r'(?<![\d.$])(\d{1,3})(?![\d%])', txt))
        if n in NUM2WORD and re.search(r'number ' + NUM2WORD[n] + r'\b', txt): nums.add(n)
        if n in nums and re.search(bp, txt): c.append((d, txt, 'NB'))
    return min(c, key=lambda x: abs(x[0] - lag)) if c else None

def cmd_lag(w, a):
    segs = transcript_segments(w)
    if not segs: die('no transcripts in wav/; run `transcribe` first')
    res = match_rows(w, segs)
    offs = [b[3][0] for b in res if b[3] and b[3][2] == 'NB']
    need = min(5, max(2, len(res) // 4))
    if len(offs) < need: die(f'only {len(offs)} rows matched buyer + number; cannot measure the lag (extend BUYER_PAT?)')
    med = statistics.median(offs); q = statistics.quantiles(offs, n=4)
    per = row_lags(w, res, med); drift = max(per.values()) - min(per.values())
    ok = lambda i, best: best and best[2] == 'NB' and abs(best[0] - per[i]) <= 12   # callout within 12 s of the lag around that time
    order = sorted((int(r[0]) for r in w.rows), key=lambda i: w.P(w.row(i)))
    dec = [per[order[int(k * (len(order) - 1) / 9)]] for k in range(10)]
    own = {}   # P06: each row's own transcript, for the bundle-word trigger
    for _, txt, src in segs: own.setdefault(src, []).append(txt)
    need = {i: why for i, b, l, best in res for why in [rc_reason(' '.join(own.get(i, [])), best, is_giveaway(l, w.row(i)[5]))] if why}
    rc_hit = {}   # P06: rows whose extended clip (rc/, cut by `recut`) holds the buyer+number line; their lag becomes that callout
    for k, (i, b, l, best) in enumerate(res):
        if i in need and not ok(i, best):
            ls = rc_lines(w, i, need[i]); m = rc_match(w.row(i), ls, per[i]) if ls else None
            if m: res[k] = (i, b, l, m); per[i] = m[0]; rc_hit[i] = m
    lines = [f'NB matches {len(offs)} of {len(res)}  median {med:+.1f} s  q1 {q[0]:+.1f}  q3 {q[2]:+.1f}  tile arg {tile_arg_from_lag(med)}',
             f'lag over the show (start -> end, deciles): ' + ' '.join(f'{d:+.0f}' for d in dec) + f'   drift {drift:.0f} s' + ('  -> per-row offsets in lag_rows.tsv; tiles use them' if drift > 6 else ''),
             'CHECK = no buyer+number match within 12 s of the lag around that time; read those rows from the callout time, not from P']
    for i, b, l, best in res:
        tag = '' if ok(i, best) else 'CHECK'
        if tag or a.all:
            lines.append(f"{i:3d} {b:20s} {l:24s} | lag {per[i]:+5.1f} | " + (f"{best[0]:+5.1f}s {best[2]:2s} {best[1][:90]}" if best else 'NO MATCH') + ' ' + tag)
    lines.append(f'flagged {sum(1 for i, _, _, best in res if not ok(i, best))}')
    ghosts, silent = find_ghosts(w, segs, res)
    with open(w.p('ghosts.tsv'), 'w') as f:
        for g in ghosts: f.write('\t'.join(str(x) for x in [g['t'], g['user'], g['heard'], g['num'], g['src'], g['text'].replace('\t', ' ')]) + '\n')
    open(w.p('silent.tsv'), 'w').write(''.join(f'{i}\t{k}\n' for i, k in silent))
    covered = sum(min(w.P(r) + 35, 10**9) - (w.P(r) - 45) for r in w.rows)
    sk = {k: sum(1 for _, x in silent if x == k) for k in '-NBG'}   # P17: the page counts only '-'; the rest is a vocabulary gap or a giveaway
    lines.append(f"ghost sales (callout heard, no order): {len(ghosts)}  -> ghosts.tsv; silent orders (buyer never heard): {len(silent)} -> silent.tsv: {sk['-']} nothing heard (the page's count), {sk['N']} number heard without the buyer, {sk['B']} buyer heard without the number, {sk['G']} giveaways with no winner read out")
    for g in ghosts[:20]: lines.append(f"   {g['t']:8.1f}s  {g['user'] or ('?' + g['heard'])} #{g['num']}  {g['text'][:80]}")
    chains = find_chains(w, segs, per)
    with open(w.p('chains.tsv'), 'w') as f:
        for c in chains: f.write('\t'.join(str(x) for x in [c['first'], c['last'], c['dir'], c['series'], c['first_item'], c['last_item'], len(c['rows']), c['heard'], ','.join(map(str, c['rows']))]) + '\n')
    lines.append(f'off-by-one chains (3+ orders in a row whose callout number is the order number +-1): {len(chains)} -> chains.tsv' + ('  CHECK THE FIRST AND LAST ORDER OF EACH BEFORE REVIEW' if chains else ''))
    for c in chains: lines.append(f"   rows {c['first']}-{c['last']} ({len(c['rows'])} orders, {c['heard']} heard): {c['series']} #{c['first_item']}-#{c['last_item']} heard as {c['dir']:+d}")
    # P06: recut.tsv = i reason from to run clip. Rows still wanting a wider clip: every 'bundle' row (the clip lists the contents)
    # and the 'late' rows no clip matched. run = rows in the unmatched stretch (by sale time) around it; `recut` cuts the longest first.
    bestof = {i: best for i, _, _, best in res}; run = {}; k = 0
    while k < len(order):
        j = k
        while j < len(order) and not ok(order[j], bestof[order[j]]): j += 1
        for x in order[k:j]: run[x] = j - k
        k = j + 1
    has_rc = lambda i: os.path.exists(w.p('rc', f'{i:03d}.wav.json'))
    left = sorted((i for i in need if need[i] == 'bundle' or i not in rc_hit), key=lambda i: w.P(w.row(i)))
    open(w.p('recut.tsv'), 'w').write(''.join(f"{i}\t{need[i]}\t{w.P(w.row(i)) + RECUT_WIN[need[i]][0]:.1f}\t{w.P(w.row(i)) + RECUT_WIN[need[i]][1]:.1f}\t{run.get(i, 0)}\t{'rc/%03d.wav.txt' % i if has_rc(i) else '-'}\n" for i in left))
    lines.append(f"recut (wider clips): {len(need)} rows want one ({sum(1 for v in need.values() if v == 'late')} nothing heard around P, {sum(1 for v in need.values() if v == 'bundle')} bundle words); {sum(1 for i in need if has_rc(i))} have rc/ clips, {len(rc_hit)} matched from them -> {len(left)} rows in recut.tsv" + (' (`-` = no clip yet; `recut` cuts them, longest unmatched stretch first)' if any(not has_rc(i) for i in left) else ''))
    for i in rc_hit: lines.append(f"   row {i}: callout at P{rc_hit[i][0]:+.0f} s in rc/{i:03d}.wav.txt: {rc_hit[i][1][:80]}")
    open(w.p('lag.txt'), 'w').write(f'{med:.1f}\n')
    open(w.p('lag_rows.tsv'), 'w').write(''.join(f'{int(r[0])}\t{per[int(r[0])]:.1f}\n' for r in w.rows))
    lines += dropout_report(w)   # P10: dropouts.tsv; adds lines only when the recording has gaps
    open(w.p('lag_report.txt'), 'w').write('\n'.join(lines) + '\n')
    say('\n'.join(lines)); say(f'wrote lag.txt ({med:+.1f}), lag_report.txt, ghosts.tsv, silent.tsv, chains.tsv, recut.tsv')

def cmd_tiles(w, a):
    w.segs  # fail once, here, if the recording is missing (not inside every worker thread)
    per = None if a.lag is not None else read_row_lags(w)
    if a.lag is not None: arg = a.lag
    else:
        lag = read_lag(w)
        if lag is None: die('no lag.txt; run `lag` first or pass --lag N')
        arg = tile_arg_from_lag(lag)
    only = parse_rows(a.rows, len(w.rows))
    for d in ('tiles', 'frames', 'cand'): os.makedirs(w.p(d), exist_ok=True)
    sel = [r for r in w.rows if only is None or int(r[0]) in only]
    todo = [r for r in sel if a.force or not (os.path.exists(w.p('tiles', f'{int(r[0]):03d}.jpg')) and os.path.exists(w.p('frames', f'{int(r[0]):03d}.json')))]
    argof = (lambda i: tile_arg_from_lag(per.get(i, lag))) if per else (lambda i: arg)   # .get: rows added by a resend
    if per: say(f'tiles: per-row offsets from lag_rows.tsv ({min(per.values()):+.0f} .. {max(per.values()):+.0f} s); frame 7 = P + offset; {len(todo)} to cut, {len(sel) - len(todo)} exist')
    else: say(f'tiles: offset {arg:+d} (frames P{arg - TILE_SPAN:+d} .. P{arg - TILE_SPAN + 33:+d}, frame 7 = P{arg:+d}); {len(todo)} to cut, {len(sel) - len(todo)} exist')
    if only is None or not os.path.exists(w.p('tiles_arg.txt')): open(w.p('tiles_arg.txt'), 'w').write(f'{arg}\n')   # a --rows run keeps the show-wide fallback
    try: targs = dict(read_tsv(w.p('tiles_rows.tsv')))
    except Exception: targs = {}
    targs.update({r[0]: str(argof(int(r[0]))) for r in todo})   # only the rows cut now: the other rows' tiles keep the offset they were cut with
    open(w.p('tiles_rows.tsv'), 'w').write(''.join(f'{i}\t{v}\n' for i, v in targs.items()))
    failed = []
    def work(r):
        i = int(r[0]); P = w.P(r); arg = argof(i); t0 = P + arg - TILE_SPAN; tile = w.p('tiles', f'{i:03d}.jpg')
        cat, base = cut(w, i, t0 - 3, t0 + 36 + 3, 'tiles')
        # One decode of the 36 s window, two outputs: the 4x3 review tile and 1 frame/s thumbnails (180 px,
        # cheap to encode and score) over FRAME_WIN (relative to P + arg = frame 7 of the tile). The three
        # sharpest, 2 s apart and visibly different, are then cut at native size with one keyframe seek each
        # (frames/NNN_1..3.jpg). Replaces the two full-frame seeks the old tiles stage did per row.
        c0, c1 = TILE_SPAN + FRAME_WIN[0], TILE_SPAN + FRAME_WIN[1] + 1
        for f in glob.glob(w.p('cand', f'{i:03d}_*.jpg')): os.remove(f)
        rc = ffmpeg(['-ss', f'{t0 - base:.3f}', '-t', '36', '-i', cat, '-filter_complex',
                f'[0:v]split=2[a][b];[a]fps=1/3,scale=360:-1,tile=4x3,format=yuvj420p[tile];[b]trim=start={c0}:end={c1},setpts=PTS-STARTPTS,fps=1,scale=180:-1,format=yuvj420p[cand]',
                '-map', '[tile]', '-frames:v', '1', '-q:v', '3', tile,
                '-map', '[cand]', '-q:v', '4', w.p('cand', f'{i:03d}_%02d.jpg')])
        cands = sorted(glob.glob(w.p('cand', f'{i:03d}_*.jpg')))
        picks = pick_frames(cands)
        for f in cands: os.remove(f)
        if rc or not cands or not os.path.exists(tile):
            # ffmpeg failed, or the clip gave a tile but no frames to pick from (recording ends inside the window): do not
            # write frames/NNN.json, so the row stays in `todo` for the next `tiles` run instead of being done forever with no pictures
            failed.append(i); os.remove(cat)
            if rc:
                try: os.remove(tile)
                except FileNotFoundError: pass
            return
        for n_, k in enumerate(picks, 1):
            ffmpeg(['-ss', f'{t0 + c0 + k + 0.5 - base:.3f}', '-i', cat, '-frames:v', '1', '-q:v', '2', w.p('frames', f'{i:03d}_{n_}.jpg')])
        os.remove(cat)
        # offsets are seconds from P (the countdown hitting zero), like the transcript lines
        json.dump({'offsets': [arg + FRAME_WIN[0] + k for k in picks], 'arg': arg}, open(w.p('frames', f'{i:03d}.json'), 'w'))
    with ThreadPoolExecutor(4) as ex: list(ex.map(work, todo))
    try: os.rmdir(w.p('cand'))
    except OSError: pass
    for l in dropout_report(w): say(l)   # P10: the tiles exist now; prep.log carries the gaps
    say('tiles done' + (f'; {len(failed)} rows FAILED (rows {",".join(map(str, sorted(failed)))}): no tile or no frames, left undone for the next `tiles` run' if failed else ''))
    return sorted(failed)

def pick_frames(cands, n=N_FRAMES, min_gap=2, min_diff=6.0):
    """Indices (sorted by time) of the n sharpest candidate frames that are at least min_gap seconds apart and
    whose small grayscale thumbnails differ by at least min_diff (mean abs pixel difference, 0-255), so a hand
    frozen over the same product does not fill all three slots. Pillow only; ~20 ms per candidate."""
    from PIL import Image, ImageFilter, ImageStat, ImageChops
    if not cands: return []
    sharp, thumbs = [], []
    for f in cands:
        try:
            im = Image.open(f).convert('L'); im.thumbnail((240, 240))
            sharp.append(ImageStat.Stat(im.filter(ImageFilter.FIND_EDGES)).var[0])
            t = im.copy(); t.thumbnail((48, 48)); thumbs.append(t)
        except Exception: sharp.append(-1); thumbs.append(None)
    order = sorted(range(len(cands)), key=lambda k: -sharp[k])
    best = sharp[order[0]] if order else 0
    good = [k for k in order if sharp[k] >= 0.5 * best]      # a blurred frame "differs" from everything; keep it out
    def differs(k, chosen):
        if thumbs[k] is None: return False
        for c in chosen:
            if abs(k - c) < min_gap: return False
            if thumbs[c] is not None and ImageStat.Stat(ImageChops.difference(thumbs[k], thumbs[c].resize(thumbs[k].size))).mean[0] < min_diff: return False
        return True
    chosen = []
    for k in good:
        if sharp[k] >= 0 and differs(k, chosen): chosen.append(k)
        if len(chosen) == n: break
    if len(chosen) < n:                       # relax the difference rule, then the gap rule
        for k in good:
            if k not in chosen and sharp[k] >= 0 and all(abs(k - c) >= min_gap for c in chosen): chosen.append(k)
            if len(chosen) == n: break
    if len(chosen) < n:
        for k in order:
            if k not in chosen: chosen.append(k)
            if len(chosen) == n: break
    return sorted(chosen)

def cut_frame(w, i, t, dest):   # i may be a row index or a tag like 'g3'
    """One native-size frame at stream time t -> dest (used for the reviewer's own frame picks at build time)."""
    try:
        cat, base = cut(w, i, t - 3, t + 3, 'frame')
        ffmpeg(['-ss', f'{t - base:.3f}', '-i', cat, '-frames:v', '1', '-q:v', '2', dest]); os.remove(cat)
        return os.path.exists(dest)
    except SystemExit: return False
    except Exception: return False

def cmd_prep(w, a):
    bad = cmd_wav(w, a) or []; cmd_transcribe(w, a); cmd_lag(w, a); cmd_recut(w, a); bad += cmd_tiles(w, a) or []   # recut before tiles: a row it matches is tiled once, at its callout
    if bad: say(f'PREP WARNING: {len(set(bad))} rows have a failed clip (rows {",".join(map(str, sorted(set(bad))))}); rerun `prep` to retry them, or see prep.log')

class RcWork:
    """P06: the work/ folder with wav/ mapped to rc/, so cmd_transcribe (and its whisper prompt) runs unchanged on the extended clips."""
    def __init__(self, w): self.w = w
    def p(self, *a): return self.w.p('rc', *a[1:]) if a and a[0] == 'wav' else self.w.p(*a)
    def __getattr__(self, k): return getattr(self.w, k)

def retile(w, a, rows):
    """Re-cut tiles/ and frames/ of rows whose lag changed (P06), leaving the other rows' tiles_rows.tsv / tiles_arg.txt entries as they were."""
    try: keep = dict(read_tsv(w.p('tiles_rows.tsv'))); arg0 = open(w.p('tiles_arg.txt')).read()
    except Exception: keep, arg0 = {}, None
    cmd_tiles(w, argparse.Namespace(lag=None, rows=','.join(map(str, rows)), force=True))
    if arg0 is None: return
    new = dict(read_tsv(w.p('tiles_rows.tsv'))); keep.update({str(i): new[str(i)] for i in rows if str(i) in new})
    open(w.p('tiles_rows.tsv'), 'w').write(''.join(f'{i}\t{v}\n' for i, v in keep.items())); open(w.p('tiles_arg.txt'), 'w').write(arg0)

def cmd_recut(w, a):
    """P06: for the rows in recut.tsv (written by `lag`): rc/NNN.wav over the row's window and rc/NNN.jpg (one keyframe every 6 s,
    6 per line) from one segment cut; whisper on the wavs (4 threads, one call); `lag` again so the wider window can match them; tiles
    re-cut at the callout for the rows that now match. Capped at RECUT_BUDGET s of prep: the longest unmatched stretches go first,
    the rest stay in recut.tsv with `-` for the agents (`--budget 0` cuts them all). With --rows, `lag` is not rerun: only those rows
    are re-matched from their clips (lag_rows.tsv, recut.tsv clip column, tiles), so ghosts.tsv, silent.tsv, chains.tsv and lag.txt keep
    any hand edits."""
    if not os.path.exists(w.p('recut.tsv')): die('no recut.tsv; run `lag` first')
    only = parse_rows(a.rows, len(w.rows)); force = getattr(a, 'force', False)
    todo = [(int(i), why, float(f), float(t), int(n)) for i, why, f, t, n, _ in read_tsv(w.p('recut.tsv'))
            if (only is None or int(i) in only) and (force or not os.path.exists(w.p('rc', f'{int(i):03d}.wav.json')))]
    todo.sort(key=lambda x: (-x[4], x[2]))
    est = lambda xs: sum((t - f) * RECUT_RATE + 1.5 for _, _, f, t, _ in xs)   # whisper + cut + tile per clip, 4 threads
    full = est(todo); budget = RECUT_BUDGET if getattr(a, 'budget', None) is None else a.budget
    if budget and full > budget:
        n = next((k for k in range(len(todo)) if est(todo[:k + 1]) > budget), len(todo))
        say(f'recut: {len(todo)} clips would add ~{full / 60:.1f} min; cap {budget} s -> cutting {n} (longest unmatched stretches first; `recut --budget 0` for the rest)'); todo = todo[:n]
    else: say(f'recut: {len(todo)} clips to cut (~{full:.0f} s)')
    if not todo: return
    w.segs; os.makedirs(w.p('rc'), exist_ok=True); t_start = datetime.datetime.now()
    def work(x):
        i, why, f, t, _ = x; cat, base = cut(w, i, f - 3, t + 3, 'rc'); ss = f'{max(f - base, 0):.3f}'; dur = f'{t - f:.0f}'
        for e in ('.wav.json', '.wav.txt'):   # --force: a stale transcript would make transcribe skip the new wav
            if os.path.exists(w.p('rc', f'{i:03d}{e}')): os.remove(w.p('rc', f'{i:03d}{e}'))
        ffmpeg(['-ss', ss, '-t', dur, '-i', cat, '-vn', '-ac', '1', '-ar', '16000', w.p('rc', f'{i:03d}.wav')])
        ffmpeg(['-skip_frame', 'nokey', '-ss', ss, '-t', dur, '-i', cat, '-vf', f'fps=1/6,scale=240:-1,tile=6x{-(-int(t - f) // 36)},format=yuvj420p',   # keyframes only: 4x faster than a full decode
                '-frames:v', '1', '-q:v', '5', w.p('rc', f'{i:03d}.jpg')])
        os.remove(cat)
    with ThreadPoolExecutor(4) as ex: list(ex.map(work, todo))
    t_cut = (datetime.datetime.now() - t_start).total_seconds()
    cmd_transcribe(RcWork(w), argparse.Namespace(**{**vars(a), 'threads': RECUT_THREADS}))
    t_whisper = (datetime.datetime.now() - t_start).total_seconds() - t_cut
    if only is None:
        cmd_lag(w, a)
        after = {int(l[0]) for l in read_tsv(w.p('recut.tsv'))}
        matched = [i for i, why, *_ in todo if why == 'late' and i not in after]
    else:   # Oct 17, 2026: an agent's `recut --rows` reran `lag` and rewrote a hand-filtered ghosts.tsv. --rows re-matches only its rows:
        per = read_row_lags(w) or {}; lag0 = read_lag(w) or 0.0; hit = {}   # their lag_rows.tsv entries and recut.tsv clip column; nothing else changes
        for i, why, *_ in todo:
            ls = rc_lines(w, i, why); m = rc_match(w.row(i), ls, per.get(i, lag0)) if ls else None
            if m: hit[i] = m; say(f'   row {i}: callout at P{m[0]:+.0f} s in rc/{i:03d}.wav.txt: {m[1][:80]}')
        try: lr = dict(read_tsv(w.p('lag_rows.tsv')))
        except Exception: lr = {}
        if hit and lr: lr.update({str(i): f'{m[0]:.1f}' for i, m in hit.items()}); open(w.p('lag_rows.tsv'), 'w').write(''.join(f'{i}\t{v}\n' for i, v in lr.items()))
        ids = {i for i, *_ in todo}; rc = read_tsv(w.p('recut.tsv'))
        open(w.p('recut.tsv'), 'w').write(''.join('\t'.join(l[:5] + [f'rc/{int(l[0]):03d}.wav.txt' if int(l[0]) in ids else l[5]]) + '\n' for l in rc))
        matched = [i for i, why, *_ in todo if why == 'late' and i in hit]
    if matched: retile(w, a, matched)
    say(f'recut done: {len(todo)} clips in {(datetime.datetime.now() - t_start).total_seconds():.0f} s (cut+tile {t_cut:.0f}, whisper {t_whisper:.0f}), {len(matched)} matched in the wider window' + (f'; tiles re-cut at the callout for rows {matched}' if matched else ''))

def cmd_show(w, a):
    for x in a.ids:
        i = int(x); r = w.row(i); P = w.P(r)
        num = re.search(r'#(\d+)', r[4]); num = num.group(1) if num else ''
        per = read_row_lags(w); lag_i = per.get(i, read_lag(w)) if per else read_lag(w)
        say(f"=== row {i} | {r[2]} | order {r[3]} | {r[4]} | ${int(r[5]) / 100:.2f}" + (f" | lag {lag_i:+.1f} s (callout ~P{lag_i:+.0f})" if lag_i is not None else ''))
        p = w.p('wav', f'{i:03d}.wav.json')
        if not os.path.exists(p): say('  (no transcript yet)'); continue
        bp = BUYER_PAT[r[2]]
        for s in json.load(open(p))['transcription']:
            t = s['offsets']['from'] / 1000 + WAV_T0; txt = s['text'].strip()
            mark = '>>' if (num and re.search(r'\b' + num + r'\b', txt)) or re.search(bp, txt.lower()) else '  '
            say(f"{mark}{t:+6.1f} {txt}")
        own = ' '.join(s['text'].strip().lower() for s in json.load(open(p))['transcription'])   # P06: same window rule as lag
        why = 'bundle' if BUNDLE_RX.search(own) else 'late'; ls = rc_lines(w, i, why)
        if ls is None: continue
        say(f'--- extended clip rc/{i:03d}.wav.txt (P{RECUT_WIN[why][0]:+d}..P{RECUT_WIN[why][1]:+d}, {why}; frames every 6 s in rc/{i:03d}.jpg):')
        for t, txt in ls:
            mark = '>>' if (num and re.search(r'\b' + num + r'\b', txt)) or re.search(bp, txt.lower()) else '  '
            say(f"{mark}{t:+6.1f} {txt}")

def cmd_chunks(w, a):
    # P05: fixed-title rows and giveaways are pre-filled into notes_fixed.tsv (i product flag frames) and left out of the
    # chunks. A hand-written or earlier notes_fixed.tsv is kept as is: delete it to regenerate from data/listing_patterns.json.
    s = split_rows(w.rows); say(split_text(w.rows))
    drop, gaps = dropout_rows(w)   # P10: rows inside a stream dropout get flag 'missing: no video A-B s' and skip review (nothing to see or hear)
    if drop: say(f'{len(drop)} rows fall in {len(gaps)} stream dropout(s) (dropouts.tsv): pre-filled as missing')
    if s['unknown']: say(f'  titles matching no pattern in data/listing_patterns.json, sent to review: ' + ' | '.join(s['unknown'][:10]))
    if os.path.exists(w.p('notes_fixed.tsv')): say('notes_fixed.tsv exists, kept as is')
    elif s['fixed'] or s['giveaway'] or drop:
        with open(w.p('notes_fixed.tsv'), 'w') as f:
            for r in s['fixed']: f.write(f'{r[0]}\t{listing_title(r[4])}\t{drop.get(r[0], "")}\t\n')
            for r in s['giveaway']: f.write(f'{r[0]}\t{GIVEAWAY_TEXT}\tgiveaway\t\n')
            for r in s['review']:
                if r[0] in drop: f.write(f'{r[0]}\t{listing_title(r[4])}\t{drop[r[0]]}\t\n')
        say(f'notes_fixed.tsv: {len(s["fixed"])} fixed-title rows + {len(s["giveaway"])} giveaways pre-filled' + (f' + {sum(1 for r in s["review"] if r[0] in drop)} rows in dropouts' if drop else ''))
    fixed = set()
    if os.path.exists(w.p('notes_fixed.tsv')): fixed = {l[0] for l in read_tsv(w.p('notes_fixed.tsv'))}
    rows = sorted((r for r in w.rows if r[0] not in fixed), key=w.P)
    if fixed: say(f'{len(fixed)} rows in notes_fixed.tsv are left out of the chunks; {len(rows)} to review')
    # P17 (Oct 9, 2026): chunks of ~20 rows by default. Four 43-row agents looped for 43 min re-reading the same tiles
    # (46 passes each, $378); the agents that finished read each tile once. Smaller chunks keep every tile in context.
    n = a.n or max(1, -(-len(rows) // a.size))
    size = -(-len(rows) // n)
    for old in glob.glob(w.p('chunk[0-9]*.txt')):   # stale chunk files from an earlier --n would confuse watch/merge
        if int(re.search(r'chunk(\d+)', old).group(1)) > n: os.remove(old)
    targs = {}
    try: targs = {int(i): int(v) for i, v in read_tsv(w.p('tiles_rows.tsv'))}
    except Exception: pass
    per = read_row_lags(w)
    for k in range(n):
        part = rows[k * size:(k + 1) * size]
        open(w.p(f'chunk{k + 1}.txt'), 'w').write(''.join('\t'.join(r + ([str(targs.get(int(r[0]), ''))] if per else [])) + '\n' for r in part))
    lag = read_lag(w); arg = None
    try: arg = int(open(w.p('tiles_arg.txt')).read().split()[0])
    except Exception: arg = tile_arg_from_lag(lag) if lag is not None else None
    title, date, key, _ = w.meta()
    run = os.path.join(HERE, 'run.py')   # absolute: agents run from work/, where run.py is not (P13)
    tpl = open(a.template or os.path.join(HERE, 'review_instructions.md')).read()
    if lag is None: lag_text = f'Stream lag NOT measured yet (run `python3 {run} lag`); assume the callout lands a few seconds after P.'
    elif per:
        lag_text = (f'Stream lag DRIFTS over this show from {min(per.values()):+.0f} s to {max(per.values()):+.0f} s (median {lag:+.1f}): '
                    f'the callout ("<buyer> thank you number N") lands at about P + lag for that row; the per-row lag is printed by `python3 {run} show i` '
                    'and the tile offset is the last column of your chunk file. The item on camera in the ~9 s before the callout is the item sold.')
    else:
        where = 'AFTER' if lag >= 0 else 'BEFORE'
        lag_text = (f'Stream lag measured at {lag:+.1f} s: the seller\'s callout ("<buyer> thank you number N") lands about {abs(lag):.0f} s {where} P. '
                    f'So the item on camera from about P{lag - 9:+.0f} to P{lag:+.0f} is the item sold, and the callout follows right after.')
    if arg is None: tile_text = 'Tiles not cut yet.'
    elif per:
        tile_text = ('The recording lag DRIFTS over this show, so every row has its own tile offset: the LAST column of your chunk '
                     'file (an integer, seconds). 4x3 grid, one frame every 3 s starting at P + offset - 18; frame 7 (row 2 col 3) = P + offset '
                     '= about when the callout lands; the item being held up in frames 4-7 is the sale. frames/NNN_1..3.jpg are three '
                     f'sharp native-size frames from P+offset-9 to P+offset+3. `python3 {run} show i` prints each row\'s lag.')
    else:
        f0 = arg - TILE_SPAN; frames = [f0 + 3 * k for k in range(12)]
        sale = min(range(12), key=lambda k: abs(frames[k] - (lag if lag is not None else arg))) + 1
        tile_text = (f'4x3 grid, one frame every 3 s from P{f0:+d} (top-left) to P{frames[-1]:+d} (bottom-right). '
                     f'Frame {sale} (row {(sale - 1) // 4 + 1} col {(sale - 1) % 4 + 1}) = P{frames[sale - 1]:+d} = the sale; the frames right before it show the item being held up. '
                     f'frames/NNN_1..3.jpg are three sharp native-size frames from P{arg + FRAME_WIN[0]:+d} to P{arg + FRAME_WIN[1]:+d}.')
    spell = ''
    for cand in (a.spellings, w.p('spellings.txt')):
        if cand and os.path.exists(cand): spell = open(cand).read().strip(); break
    if not spell:
        known = [(b, BUYERS[b]) for b in sorted(set(r[2] for r in w.rows)) if BUYERS.get(b)]
        def plain(x): return re.sub(r'\\b|\\', '', x)
        spell = ', '.join('%s -> "%s"' % (b, '/'.join(plain(x) for x in alts[:4])) for b, alts in known) or ''
    cat = load_catalog(); keys = sorted({k for r in cat['rules'] for k in r.get('seen', [])})[-3:]
    pids = []
    for r in cat['rules']:
        if set(r.get('seen', [])) & set(keys):
            for pid, _ in r['yields']:
                if pid not in pids: pids.append(pid)
    pids = [pid for pid in pids if pid in cat['products']]
    catalog_text = '\n'.join(f'   - {cat["products"][pid]["name"]}' for pid in pids[:200]) + (f'\n   (+{len(pids) - 200} more in pipeline/data/catalog.json)' if len(pids) > 200 else '')
    notes = ''
    for cand in (a.notes, w.p('show_notes.md')):
        if cand and os.path.exists(cand): notes = open(cand).read().strip(); break
    text = (tpl.replace('{SHOW_TITLE}', title).replace('{DATE}', date).replace('{WORK_DIR}', w.dir).replace('{RUN}', run).replace('{LAG_TEXT}', lag_text)
               .replace('{TILE_TEXT}', tile_text).replace('{SPELLINGS}', spell or '(none recorded for this show yet)').replace('{SHOW_NOTES}', notes)
               .replace('{CATALOG}', catalog_text or '   (catalog empty)'))
    text = re.sub(r'\n{3,}', '\n\n', text)
    open(w.p('REVIEW.md'), 'w').write(text)
    say(f'{n} chunks of up to {size} rows (chunk1..{n}.txt), REVIEW.md written. Hand each agent: "Read {w.p("REVIEW.md")}; your chunk is chunkN.txt; write notes_N.tsv."')
    say(f'Then run `python3 {run} watch` (Monitor tool or background) and act on its DONE / STALLED / ALL DONE lines; never wait on the agents themselves.')

def chunk_progress(w):
    """P17: per chunk, (ids in chunkN.txt, ids of those present in notes_N.tsv, duplicate ids, foreign ids) read from disk."""
    out = {}
    for f in sorted(glob.glob(w.p('chunk[0-9]*.txt')), key=lambda f: int(re.search(r'chunk(\d+)', f).group(1))):
        n = int(re.search(r'chunk(\d+)', f).group(1)); ids = [l[0] for l in read_tsv(f)]
        have, dupes, foreign = [], [], []
        if os.path.exists(w.p(f'notes_{n}.tsv')):
            for l in read_tsv(w.p(f'notes_{n}.tsv')):
                if not l or not l[0].strip(): continue
                i = l[0].strip()
                if i not in ids: foreign.append(i)
                elif i in have: dupes.append(i)
                else: have.append(i)
        out[n] = (ids, have, dupes, foreign)
    return out

def cmd_watch(w, a):
    """P17 (Oct 9, 2026): the review is finished when the notes files on disk are complete, not when the agents say so.
    One line per event (for the Monitor tool): `chunk N k/m`, `DONE chunk N`, `STALLED chunk N k/m missing ...` (no new
    row for --stall seconds), `ALL DONE` (exit 0), `TIMEOUT` (exit 2). The caller stops a stalled agent and finishes its
    rows itself (`run.py missing`); it never waits for an agent that is not writing."""
    import time
    t0 = time.time(); last = {}; grown = {}; stalled = set(); done = set(); first = True
    while True:
        prog = chunk_progress(w)
        if not prog: die('no chunkN.txt files (run `chunks` first)')
        now = time.time(); all_done = True
        for n, (ids, have, dupes, foreign) in prog.items():
            k, m = len(have), len(ids)
            if n not in grown: grown[n] = now
            if last.get(n) != k:
                if k > last.get(n, -1): grown[n] = now
                last[n] = k
                if n not in done and k < m: say(f'chunk {n} {k}/{m}')
            if dupes or foreign:
                say(f'WARN chunk {n}: {len(dupes)} duplicate id(s) {" ".join(dupes[:5])} {len(foreign)} foreign id(s) {" ".join(foreign[:5])} in notes_{n}.tsv')
            if k >= m:
                if n not in done: done.add(n); stalled.discard(n); say(f'DONE chunk {n} ({m} rows)')
                continue
            all_done = False
            if now - grown[n] >= a.stall and n not in stalled:
                stalled.add(n); miss = [i for i in ids if i not in have]
                say(f'STALLED chunk {n} {k}/{m} no new row for {int((now - grown[n]) / 60)} min, missing {len(miss)}: {" ".join(miss[:30])}')
            elif now - grown[n] < a.stall: stalled.discard(n)
        if all_done: say('ALL DONE'); return
        if now - t0 >= a.timeout:
            say('TIMEOUT ' + ' '.join(f'chunk {n} {len(h)}/{len(i)}' for n, (i, h, _, _) in prog.items() if len(h) < len(i))); sys.exit(2)
        if a.once: return
        first = False; time.sleep(a.every)

def cmd_missing(w, a):
    """P17: the chunk rows no notes file has yet, in chunk format, so the main session (or a fresh agent) finishes them."""
    prog = chunk_progress(w); n_out = 0
    with open(w.p('missing.txt'), 'w') as out:
        for n, (ids, have, _, _) in prog.items():
            miss = [i for i in ids if i not in have]
            if not miss: continue
            lines = {l[0]: l for l in read_tsv(w.p(f'chunk{n}.txt'))}
            say(f'chunk {n}: {len(miss)} missing -> notes_{n}.tsv: {" ".join(miss)}')
            for i in miss: out.write('\t'.join(lines[i]) + '\n'); n_out += 1
    say(f'{n_out} rows written to missing.txt (chunk format)' if n_out else 'nothing missing: every chunk row has a notes line')

def cmd_merge(w, a):
    files = sorted(glob.glob(w.p('notes_[0-9]*.tsv')) + glob.glob(w.p('notes_fixed.tsv')))
    if not files: die('no notes_N.tsv files to merge')
    seen = {}; dupes = []
    for f in files:
        for l in open(f):
            if not l.strip(): continue
            parts = (l.rstrip('\n').split('\t') + ['', '', ''])[:4]
            if parts[0] in seen: dupes.append((parts[0], os.path.basename(f)))
            seen[parts[0]] = parts
    ids = [r[0] for r in w.rows]; missing = [i for i in ids if i not in seen]; extra = [k for k in seen if k not in set(ids)]
    if missing or dupes or extra:
        if missing: say(f'MISSING {len(missing)} rows: {" ".join(missing[:40])}')
        if dupes: say(f'DUPLICATED {len(dupes)}: ' + ' '.join(f'{i}({f})' for i, f in dupes[:40]))
        if extra: say(f'UNKNOWN row ids {len(extra)}: {" ".join(extra[:20])}')
        die('fix the notes_N.tsv files and merge again')
    bad = [(i, seen[i][2]) for i in ids if seen[i][2] and seen[i][2].partition(':')[0].strip().lower() not in FLAGCATS]
    if bad:
        for i, f in bad[:30]: say(f'  row {i}: flag without a known category: "{f}"')
        die(f'{len(bad)} flag(s) lack a category. Use "category: detail" with one of: {", ".join(FLAGCATS)} (data/flag_categories.json)')
    # P12: every row inside an off-by-one chain (lag -> chains.tsv) is flagged for revision. An existing revise flag keeps
    # its category and gets the chain appended; a note-level flag moves behind the chain so the row reaches the revise list.
    chains = read_chains(w); nchain = 0
    for c in chains:
        tag = chain_tag(c)
        for i in map(str, c['rows']):
            if i not in seen or CHAIN_FLAG in seen[i][2]: continue
            f = seen[i][2].strip()
            seen[i][2] = ('item: ' + tag) if not f else (f + '; ' + tag) if parse_flag(f)[1] == 'revise' else ('item: ' + tag + '; ' + f)
            nchain += 1
    with open(w.p('notes.tsv'), 'w') as out:
        for i in ids: out.write('\t'.join(seen[i]) + '\n')
    nrev = sum(1 for i in ids if seen[i][2] and parse_flag(seen[i][2])[1] == 'revise'); nnote = sum(1 for i in ids if seen[i][2]) - nrev
    nfixed = len(read_tsv(w.p('notes_fixed.tsv'))) if os.path.exists(w.p('notes_fixed.tsv')) else 0   # P05
    say(f'notes.tsv: {len(ids)} rows from {len(files)} files ({nfixed} pre-filled in notes_fixed.tsv), {nrev} to revise, {nnote} notes' + (f", {nchain} rows flagged by {len(chains)} off-by-one chain(s)" if chains else ''))

def evidence_dir(w, out):
    """<out>/evidence, kept OUTSIDE iCloud when work/ lives outside the show folder: the real folder is
    <work root>/evidence/<show folder name>/ and <out>/evidence is a symlink to it (D04). Rebuilt from scratch."""
    link = os.path.join(out, 'evidence'); root = os.path.dirname(os.path.realpath(w.dir))
    if os.path.islink(link): real = os.path.realpath(link); os.remove(link)
    elif os.path.isdir(link): shutil.rmtree(link); real = None
    else: real = None
    if os.path.realpath(root) == os.path.realpath(out) or not os.path.isdir(root): real = link      # old layout: work/ inside the show folder
    elif real is None or not real.startswith(os.path.realpath(root)): real = os.path.join(root, 'evidence', os.path.basename(os.path.normpath(out)))
    if os.path.isdir(real): shutil.rmtree(real)
    os.makedirs(real, exist_ok=True)
    if real != link: os.symlink(real, link)
    return link

def evidence_for(w, r, out, lag, review_frames=''):
    """Three native-size frames + the transcript around the sale for one row. Frames: the reviewer's picks
    (tile frame numbers 1-12 in the notes' fourth column, cut now from the segments) first, then the auto
    picks from frames/NNN_*.jpg to make three. Copied to <out>/evidence/<orderNo>_1..3.jpg. Returns the
    item's 'evidence' dict, or None when nothing exists yet."""
    i = int(r[0]); order = r[3]; ev = {}; frames = []
    evdir = os.path.join(out, 'evidence')
    try: meta = json.load(open(w.p('frames', f'{i:03d}.json')))
    except Exception: meta = None
    arg = meta['arg'] if meta else None
    if arg is None:
        try: arg = {int(a_): int(b_) for a_, b_ in read_tsv(w.p('tiles_rows.tsv'))}.get(i)
        except Exception: pass
    picks = []
    for tok in re.split(r'[,\s]+', review_frames.strip()):
        if tok.isdigit() and 1 <= int(tok) <= 12 and arg is not None: picks.append(arg - TILE_SPAN + 3 * (int(tok) - 1))
    for off in picks[:N_FRAMES]:
        dest = os.path.join(evdir, f'{order}_{len(frames) + 1}.jpg')
        if cut_frame(w, i, w.P(r) + off, dest): frames.append({'file': f'evidence/{os.path.basename(dest)}', 'off': off, 'by': 'review'})
    if meta:
        for k, off in enumerate(meta['offsets'], 1):
            if len(frames) >= N_FRAMES: break
            if any(abs(f['off'] - off) < 1 for f in frames): continue
            src = w.p('frames', f'{i:03d}_{k}.jpg')
            if not os.path.exists(src): continue
            dest = os.path.join(evdir, f'{order}_{len(frames) + 1}.jpg')
            try: os.link(src, dest)          # same volume (work root): no copy, no extra blocks, and the frame outlives Step 6's rm of work/
            except OSError: shutil.copy(src, dest)   # other volume or filesystem without hard links
            frames.append({'file': f'evidence/{os.path.basename(dest)}', 'off': off, 'by': 'auto'})
    if frames: ev['frames'] = sorted(frames, key=lambda f: f['off'])
    tj = w.p('wav', f'{i:03d}.wav.json')
    if os.path.exists(tj):
        num = re.search(r'#(\d+)', r[4]); num = num.group(1) if num else ''
        bp = BUYER_PAT[r[2]]; c = lag or 0; lines = []
        for s in json.load(open(tj))['transcription']:
            off = s['offsets']['from'] / 1000 + WAV_T0; txt = s['text'].strip()
            if not txt or not (c - 25 <= off <= c + 25): continue
            hit = bool((num and re.search(r'\b' + num + r'\b', txt)) or re.search(bp, txt.lower()))
            lines.append([round(off, 1), txt, hit])
        if lines: ev['transcript'] = lines
    return ev or None

def item_number(listing):
    """'Beauty pulls #256' -> (256, 'Beauty pulls'): the number the seller stickers on the product. Fixed-title
    listings have no number -> (None, title). Series matter because counters restart per title (Sep 26: 'Beauty
    pulls #1' and 'Beautiful pulls #1'); GIVY #n is its own series."""
    m = re.match(r'^(.*?)\s*#\s*(\d+)\s*$', listing or '')
    if m: return int(m.group(2)), (m.group(1).strip() or listing.strip())
    return None, (listing or '').strip()

def slip_note(a):
    """The slip footer: --slip-note sets it and remembers it in data/slip_note.txt; '' clears it."""
    if a.slip_note is not None:
        open(SLIP_NOTE_F, 'w').write(a.slip_note.strip() + '\n')
    try: return open(SLIP_NOTE_F).read().strip()
    except FileNotFoundError: return SLIP_NOTE_DEFAULT

def write_slips(packages, out, title, date, key, note, order):
    """packing_slips.html (D11): one slip per shipping label, buyer-facing. Every slip is rendered here as
    <article class="slip"> with the sort keys in data attributes; the page's script only reorders them
    (username = Whatnot's label export order, order count, order time) and switches 4x6 / letter paper.
    Rows: item #, product text, order #, a check box. Rows to revise get a dot, never the flag text."""
    from html import escape as E
    multi = len({it['series'] for p in packages for it in p['items'] if it['itemNo'] is not None}) > 1
    def keys(p):
        n = int(p['split'].split(' of ')[0]) if p['split'] else 1
        t0 = min((it['at'] for it in p['items'] if it.get('at') is not None), default=0)
        return n, t0
    SORT = {'username': lambda p: (p['buyer'].lower(), keys(p)[0]), 'count': lambda p: (-len(p['items']), p['buyer'].lower(), keys(p)[0]),
            'time': lambda p: (keys(p)[1], p['buyer'].lower(), keys(p)[0])}
    slips = []
    for p in sorted(packages, key=SORT[order]):   # the file is already in the default order; the script re-sorts on the dropdown
        n, t0 = keys(p)
        rows = []
        for it in sorted(p['items'], key=lambda x: (x['at'] if x.get('at') is not None else 1e12)):
            no = f"#{it['itemNo']}" + (f"<small>{E(it['series'])}</small>" if multi else '') if it['itemNo'] is not None else f'<span class="none">{E(it["listing"])}</span>'
            # giveaway rows hold the reviewer's note to the seller ("the item on camera at the draw is not the prize"): the buyer sees only "Giveaway prize"
            give = it['flagCat'] == 'giveaway'
            dot = '<i class="dot"></i>' if it['flagLevel'] == 'revise' or give else ''
            rows.append(f'<tr><td class="no">{no}</td><td class="prod">{dot}{"Giveaway prize" if give else E(it["product"])}</td><td class="ord">{E(it["order"])}</td><td class="box"><span></span></td></tr>')
        lbl = f' <span class="lbl">Label {E(p["split"])}</span>' if p['split'] else ''
        track = ' · '.join(x for x in (p['courier'], p['tracking'] or 'no label yet') if x)
        slips.append(f'<article class="slip" id="s{p["num"]}" data-buyer="{E(p["buyer"].lower())}" data-n="{n}" data-count="{len(p["items"])}" data-time="{int(t0)}">\n'
                     f'<table><thead><tr><th colspan="4"><div class="head"><div class="show">{E(title)} · {E(date)}</div><div class="buyer">{E(p["buyer"])}{lbl}</div>'
                     f'<div class="track">{E(track)}</div><div class="cols"><span>Item</span><span>Product</span><span>Order</span></div></div></th></tr></thead>\n'
                     f'<tbody>\n' + '\n'.join(rows) + f'\n</tbody></table>\n<footer>{E(note)}</footer>\n</article>')
    tpl = open(os.path.join(HERE, 'slips_template.html')).read()
    page = (tpl.replace('__SLIPS__', '\n'.join(slips)).replace('__TITLE__', f'{date.split(",")[0]} Packing Slips').replace('__SHOW__', E(title)).replace('__DATE__', E(date))
            .replace('__NSLIPS__', str(len(packages))).replace('__KEY__', key).replace('__ORDER__', order))
    path = os.path.join(out, 'packing_slips.html'); open(path, 'w').write(page)
    return path, os.path.getsize(path)

def cmd_build(w, a):
    title, date, key, start = w.meta()
    if a.title: title = a.title
    if a.date: date = a.date
    if a.key: key = a.key
    if a.start: start = datetime.datetime.fromisoformat(a.start.replace('Z', '+00:00'))
    out = os.path.abspath(a.out or w.show_folder())
    notes_f = w.p('notes.tsv')
    if not os.path.exists(notes_f): die('no notes.tsv; run `merge` first')
    rows = w.rows
    notes = {l[0]: (l + ['', '', ''])[:4] for l in read_tsv(notes_f)}
    # R08: the receiver's diff of the last resend (orders added / removed / moved, tracking numbers added).
    # Stays until the next resend replaces it, so a manual rebuild keeps the same "changed since" box.
    diff = None; change = {}
    try: diff = json.load(open(w.p('diff.json')))
    except Exception: pass
    if diff and not (diff.get('added') or diff.get('removed') or diff.get('moved') or diff.get('tracking')): diff = None
    if diff:
        for e in diff['added']: change[e['order']] = ('NEW', e)
        for e in diff['moved']: change[e['order']] = ('MOVED', e)
    ship = {}
    if os.path.exists(w.p('ship.tsv')):
        for f in read_tsv(w.p('ship.tsv')):
            if len(f) >= 3: ship[f[0]] = {'tracking': f[2], 'courier': f[3] if len(f) > 3 else '', 'method': f[4] if len(f) > 4 else ''}
    order = []; groups = {}
    for r in rows:
        sid = r[1]
        if sid not in groups: groups[sid] = []; order.append(sid)
        groups[sid].append(r)
    by_buyer = {}
    for sid in order:
        b = groups[sid][0][2]; by_buyer.setdefault(b, []).append(sid)
    for b, sids in by_buyer.items():
        sids.sort(key=lambda s: min(float(r[6]) for r in groups[s]))
    local = tz(); lag = read_lag(w); per = read_row_lags(w)
    chains = read_chains(w); chain_of = {i: k for k, c in enumerate(chains) for i in c['rows']}   # P12
    chain_ends = {c['first'] for c in chains} | {c['last'] for c in chains}   # only these chain rows get evidence frames: the banner says to check the first and last, and 20 x 3 frames would cost MBs
    os.makedirs(out, exist_ok=True); evidence_dir(w, out)   # derived; rebuilt from scratch every time
    nev = 0
    packages = []
    for k, sid in enumerate(order, 1):
        items = []
        for r in groups[sid]:
            P = int(float(r[6])) + 30
            t = sale_clock(start, P, r[7], local)
            n = notes.get(r[0], [r[0], '(not reviewed)', '', ''])
            pf = parse_flag(n[2])
            it = {'order': r[3], 'listing': r[4], 'price': int(r[5]) / 100, 'time': t, 'product': n[1], 'flag': n[2],
                  'flagCat': pf[0] if pf else '', 'flagLevel': pf[1] if pf else '', 'flagText': (pf[2] or FLAGCATS[pf[0]][1]) if pf else '',
                  'flagLabel': FLAGCATS[pf[0]][1] if pf else ''}
            it['itemNo'], it['series'] = item_number(r[4])
            it['at'] = P   # seconds on the stream timeline = position in the Whatnot replay (D02); the page and hms() show it as h:mm:ss
            if is_giveaway(r[4], r[5]): it['giveaway'] = True   # D06 "Hide giveaways" chip; only set on giveaway rows to keep packages.json small
            if int(r[0]) in chain_of: it['chain'] = chain_of[int(r[0])]   # P12: index into __CHAINS__; the page shows the chain once, not 15 rows
            c = change.get(r[3]); it['change'] = c[0] if c else ''
            if c and c[0] == 'MOVED': it['movedFrom'] = {'package': c[1].get('fromPackage'), 'buyer': c[1].get('fromBuyer', '')}
            co = int(r[0]) in chain_of and int(r[0]) not in chain_ends and (pf[2] or '').startswith(CHAIN_FLAG)   # a middle row of a chain, flagged for nothing else
            if (n[2] and not co) or a.evidence == 'all':
                ev = evidence_for(w, r, out, per.get(int(r[0]), lag) if per else lag, n[3])
                if ev: it['evidence'] = ev; nev += 1
            items.append(it)
        b = groups[sid][0][2]; sids = by_buyer[b]
        split = f"{sids.index(sid) + 1} of {len(sids)}" if len(sids) > 1 else ''
        s = ship.get(sid, {})
        packages.append({'num': k, 'shipment': sid, 'buyer': b, 'tracking': s.get('tracking', ''), 'courier': s.get('courier', ''),
                         'method': s.get('method', '').replace('_', ' '), 'split': split, 'items': items, 'total': sum(x['price'] for x in items)})
    packages.sort(key=lambda p: (min(float(r[6]) for r in groups[p['shipment']]) if not p['split'] else min(float(r[6]) for s in by_buyer[p['buyer']] for r in groups[s]), p['split']))
    for k, p in enumerate(packages, 1): p['num'] = k
    os.makedirs(out, exist_ok=True); json.dump(packages, open(w.p('packages.json'), 'w'), indent=1)
    nit = sum(len(p['items']) for p in packages); nflag = sum(1 for p in packages for it in p['items'] if it['flagLevel'] == 'revise'); total = sum(p['total'] for p in packages)
    nnote = sum(1 for p in packages for it in p['items'] if it['flagLevel'] == 'note')
    splits = [b for b, s in by_buyer.items() if len(s) > 1]
    where = {it['order']: (p, it) for p in packages for it in p['items']}
    # P12: one banner per chain, linking the first and last order so the seller checks two rows instead of fifteen.
    def chain_end(i):
        r = w._byid.get(str(i)); p, it = where.get(r[3], (None, None)) if r else (None, None)
        return {'order': r[3], 'itemNo': it['itemNo'], 'series': it['series'], 'pkg': p['num'], 'buyer': p['buyer'], 'product': it['product']} if it else None
    chains_out = [{'first': chain_end(c['first']), 'last': chain_end(c['last']), 'dir': c['dir'], 'n': len(c['rows']), 'heard': c['heard'], 'series': c['series'],
                   'firstItem': c['first_item'], 'lastItem': c['last_item']} for c in chains]
    chain_only = lambda it: it.get('chain') is not None and it['flagText'].startswith(CHAIN_FLAG)   # the chain is this row's only reason to be revised
    if diff:   # fill in what the page says about each changed order: product text, package number, item number
        for e in diff['added'] + diff['moved']:
            p, it = where.get(e['order'], (None, None))
            if it: e.update({'product': it['product'], 'package': p['num'], 'pkgBuyer': p['buyer'], 'itemNo': it['itemNo'], 'series': it['series']})
        diff['added'] = [e for e in diff['added'] if e.get('package')]; diff['moved'] = [e for e in diff['moved'] if e.get('package')]
        for e in diff['removed']: e['itemNo'], e['series'] = item_number(e['listing'])
        diff['summary'] = ', '.join(s for s, n in [(f"{len(diff['added'])} new", len(diff['added'])), (f"{len(diff['removed'])} removed", len(diff['removed'])), (f"{len(diff['moved'])} moved", len(diff['moved'])),
                                                 (f"{diff['tracking']} tracking number{'s' if diff['tracking'] != 1 else ''} added", diff['tracking'])] if n)
    def item_tag(e): return f"#{e['itemNo']}" if e.get('itemNo') is not None else e['listing']
    parts = replay_parts(w)
    with open(os.path.join(out, 'packing_list.csv'), 'w', newline='') as f:
        wr = csv.writer(f); wr.writerow(['Change', 'Package', 'Buyer', 'Split shipment', 'Tracking #', 'Carrier', 'Item #', 'Listing', 'Order #', 'Sold price', 'Sold at (EDT)', 'Replay position', 'What to pack', 'Needs your check', 'Note', 'Evidence'])
        for p in packages:
            for it in p['items']: wr.writerow([it['change'], p['num'], p['buyer'], ('LABEL ' + p['split']) if p['split'] else '', p['tracking'], p['courier'], it['itemNo'] if it['itemNo'] is not None else '', it['listing'], it['order'], f"${it['price']:.2f}", it['time'], hms(it['at']), it['product'], it['flag'] if it['flagLevel'] == 'revise' else '', (f"moved from package {it['movedFrom']['package'] or '?'} ({it['movedFrom']['buyer']}). " if it['change'] == 'MOVED' else '') + (it['flag'] if it['flagLevel'] == 'note' else ''), ' '.join(f['file'] for f in it.get('evidence', {}).get('frames', []))])
        for e in (diff['removed'] if diff else []):   # gone from Whatnot since the last list; stays here until the next resend
            wr.writerow(['REMOVED', e.get('package') or '', e['buyer'], '', '', '', e['itemNo'] if e['itemNo'] is not None else '', e['listing'], e['order'], '', '', '', e.get('product') or '', '', f"Pull this out of {e['buyer']}'s box if already packed.", ''])
    unreviewed = sum(1 for p in packages for it in p['items'] if it['product'] == '(not reviewed)')
    md = [f'# Whatnot packing list - {title} ({date})\n', f'{len(packages)} packages, {nit} items, ${total:,.2f} in sales. {nflag} orders need a quick check (⚠); {nnote} carry a note (ⓘ).\n']
    if diff:
        md.append(f"\n## Changed since your last list ({diff['summary']})\n")
        for e in diff['added']: md.append(f"- **NEW** Package {e['package']} {e['pkgBuyer']} - {item_tag(e)} (order {e['order']}): {e['product']}")
        for e in diff['removed']: md.append(f"- **REMOVED** {e['buyer']} - {item_tag(e)} (order {e['order']}): {e.get('product') or e['listing']}. Pull this out of {e['buyer']}'s box" + (f" (was Package {e['package']})" if e.get('package') else '') + ' if already packed.')
        for e in diff['moved']: md.append(f"- **MOVED** {item_tag(e)} (order {e['order']}): {e['product']} - was Package {e.get('fromPackage') or '?'} ({e.get('fromBuyer', '')}), now Package {e['package']} ({e['pkgBuyer']})")
        if diff['tracking']: md.append(f"- {diff['tracking']} package{'s' if diff['tracking'] != 1 else ''} got a tracking number.")
    if splits:
        md.append('\n## Split shipments (one buyer, more than one label)\n')
        for b in splits:
            md.append(f'- **{b}** has {len(by_buyer[b])} labels:')
            for p in packages:
                if p['buyer'] == b: md.append(f"  - Label {p['split']}: Package {p['num']}, tracking `{p['tracking']}`, {len(p['items'])} item(s)")
    md.append('\n## How this list was checked\n')
    md.append(f"- {nit} orders in {len(packages)} packages, read from the Whatnot Shipments page.")
    if os.path.exists(w.p('lag_report.txt')):
        rep = open(w.p('lag_report.txt')).read(); m = re.search(r'NB matches (\d+) of (\d+)\s+median ([+-]?\d+\.\d) s', rep)
        if m: md.append(f"- {m.group(1)} of {nit} sales confirmed by the mic (buyer and item number called out); the rest matched by picture.")
        if m: md.append(f"- Receipt offset on this stream: {float(m.group(3)):+.1f} s; every picture is taken at the corrected moment.")
    md.append('- "Replay" is h:mm:ss into the Whatnot replay; drag the scrubber there to see the sale.' + (f' The replay was restarted {parts - 1} time{"s" if parts > 2 else ""} ({parts} parts); positions count from the first start.' if parts > 1 else ''))
    if os.path.exists(w.p('ghosts.tsv')):
        md.append(f"- {len(read_tsv(w.p('ghosts.tsv')))} sale(s) heard on the mic with no order in Whatnot (listed below).")
    if os.path.exists(w.p('chains.tsv')):
        md.append(f"- {len(chains)} stretch(es) of orders whose callouts ran one item number off (a sign that orders shifted against the stream)." if chains else '- No stretch of orders ran one item number off against the callouts.')
    drop, gaps = dropout_rows(w)   # P10
    if os.path.exists(w.p('dropouts.tsv')):
        md.append(f"- The recording has {len(gaps)} gap(s) with no video and no audio; {len(drop)} order(s) fall in them" + (': ' + ', '.join(f'{a:.0f}-{b:.0f} s' for a, b in gaps) + '.' if gaps else '.'))
    md.append(f"- {nflag} orders where the picture or the audio left a doubt are in the double-check list.")
    if os.path.exists(w.p('ghosts.tsv')) and read_tsv(w.p('ghosts.tsv')):
        md.append('\n## Sold on the mic but no order in Whatnot (do not pack)\n')
        for g in read_tsv(w.p('ghosts.tsv')):
            t, user, heard, num, src, text = (g + [''] * 6)[:6]
            md.append(f"- {float(t):.0f} s into the show: {user or ('heard as ' + heard)} #{num} - \"{text}\"")
    md.append('\n## Orders that need your check\n')
    for c in chains_out:
        md.append(f"- **POSSIBLE SHIFT** {c['series']} #{c['firstItem']} to #{c['lastItem']} ({c['n']} orders): the mic called {'one number higher' if c['dir'] > 0 else 'one number lower'} than Whatnot's order for {c['heard']} of them. "
                  + (f"Check the first (Package {c['first']['pkg']} {c['first']['buyer']}, order {c['first']['order']}) and the last (Package {c['last']['pkg']} {c['last']['buyer']}, order {c['last']['order']}); if both are wrong, the whole stretch is." if c['first'] and c['last'] else 'Check the first and last order of the stretch.'))
    for p in packages:
        for it in p['items']:
            if it['flagLevel'] == 'revise' and not chain_only(it):
                ev = it.get('evidence', {}); links = ' '.join(f"[frame {k}]({f['file']})" for k, f in enumerate(ev.get('frames', []), 1))
                tag = f"item #{it['itemNo']}" if it['itemNo'] is not None else it['listing']
                md.append(f"- Package {p['num']} {p['buyer']} - {tag} (order {it['order']}): {it['product']} -> **{it['flag']}**" + (f' ({links})' if links else ''))
    for p in packages:
        sp = f" - SPLIT SHIPMENT, LABEL {p['split']}" if p['split'] else ''
        md.append(f"\n## Package {p['num']} - {p['buyer']}{sp} - {len(p['items'])} items - ${p['total']:.2f}\n")
        md.append(f"Tracking: `{p['tracking']}` ({p['courier']} {p['method']})\n")
        md.append('| Item # | Listing | Order # | Sold at | Replay | Price | What to pack | Check |\n|---|---|---|---|---|---|---|---|')
        for it in p['items']: md.append(f"| {('#' + str(it['itemNo'])) if it['itemNo'] is not None else '—'} | {it['listing']} | {it['order']} | {it['time']} | {hms(it['at'])} | ${it['price']:.2f} | {it['product']} | {('⚠ ' + it['flag']) if it['flagLevel'] == 'revise' else ('ⓘ ' + it['flag']) if it['flag'] else ''} |")
    open(os.path.join(out, 'packing_list.md'), 'w').write('\n'.join(md) + '\n')
    ghosts = []
    if os.path.exists(w.p('ghosts.tsv')):
        evdir = os.path.join(out, 'evidence')
        for k, g in enumerate(read_tsv(w.p('ghosts.tsv'))):
            t, user, heard, num, src, text = (g + [''] * 6)[:6]; t = float(t)
            item = {'t': t, 'user': user, 'heard': heard, 'num': num, 'text': text,
                    'time': (start + datetime.timedelta(seconds=t)).astimezone(local).strftime('%-I:%M:%S %p') if start else f'{hms(t)} into the show'}
            # cut once into work/ghostpics/ (segments may be trimmed later); every build copies from there
            os.makedirs(w.p('ghostpics'), exist_ok=True); cache = w.p('ghostpics', f'g{k + 1}.jpg'); dest = os.path.join(evdir, f'ghost_{k + 1}.jpg')
            if not os.path.exists(cache): cut_frame(w, f'g{k + 1}', t - 6, cache)
            if os.path.exists(cache): shutil.copy(cache, dest); item['pic'] = f'evidence/ghost_{k + 1}.jpg'
            tj = w.p('wav', f'{int(src):03d}.wav.json') if src.isdigit() else ''
            if tj and os.path.exists(tj) and src in w._byid:   # the source row may be gone (cancelled, then resent): keep the picture, skip the transcript
                P0 = w.P(w._byid[src]); ls = []
                for sg in json.load(open(tj))['transcription']:
                    tt = P0 + WAV_T0 + sg['offsets']['from'] / 1000; tx = sg['text'].strip()
                    if tx and abs(tt - t) <= 20: ls.append([round(tt - t, 1), tx, abs(tt - t) < 1])
                if ls: item['transcript'] = ls
            ghosts.append(item)
    # P17: silent.tsv is row<TAB>kind; the seller's count is kind '-' (nothing heard), not N/B/G (number or buyer heard, giveaway). Old one-column files count whole.
    silent = [s for s in read_tsv(w.p('silent.tsv')) if len(s) < 2 or s[1] == '-'] if os.path.exists(w.p('silent.tsv')) else []
    # How this list was checked (D10): numbers the pipeline already has, in plain words for the seller.
    check = {'orders': nit, 'packages': len(packages), 'revise': nflag, 'ghosts': len(ghosts) if os.path.exists(w.p('ghosts.tsv')) else None, 'silent': len(silent) if os.path.exists(w.p('silent.tsv')) else None, 'unreviewed': unreviewed,
             'chains': len(chains) if os.path.exists(w.p('chains.tsv')) else None}
    if parts > 1: check['parts'] = parts   # D02: the lede says replay positions count from the first start
    if os.path.exists(w.p('dropouts.tsv')): check['gaps'] = len(gaps); check['inGaps'] = len(drop)   # P10
    try:
        rep = open(w.p('lag_report.txt')).read()
        m = re.search(r'NB matches (\d+) of (\d+)\s+median ([+-]?\d+\.\d) s', rep)
        if m: check['heard'] = int(m.group(1)); check['lag'] = float(m.group(3))
    except Exception: pass
    try:
        per_ = [float(v) for _, v in read_tsv(w.p('lag_rows.tsv'))]
        if per_: check['lagMin'] = min(per_); check['lagMax'] = max(per_)
    except Exception: pass
    tpl = open(os.path.join(HERE, 'page_template.html')).read()
    page_title = f'{date.split(",")[0]} Packing List'
    from html import escape as E
    J = lambda x, **k: json.dumps(x, **k).replace('</', '<\\/')   # JSON inside <script>: a product text or title holding "</script>" must not end the block
    page = (tpl.replace('__DATA__', J(packages, separators=(',', ':'))).replace('__TITLE__', E(page_title)).replace('__SHOW__', E(title)).replace('__DATE__', E(date))
            .replace('__NPK__', str(len(packages))).replace('__NIT__', str(nit)).replace('__NFL__', str(nflag)).replace('__TOT__', f'{total:,.2f}')
            .replace('__NSPLIT__', str(len(splits))).replace('__KEY__', key).replace('__GHOSTS__', J(ghosts, separators=(',', ':'))).replace('__NSILENT__', str(len(silent))).replace('__CHECK__', J(check))
            .replace('__DIFF__', J(diff, separators=(',', ':'))).replace('__CHAINS__', J(chains_out, separators=(',', ':'))).replace('__META__', J({'show': title, 'date': date, 'key': key})))
    open(os.path.join(out, 'packing_list.html'), 'w').write(page)
    _, slip_bytes = write_slips(packages, out, title, date, key, slip_note(a), a.slip_order)
    say(f'{len(packages)} packages, {nit} items, {nflag} to revise, {nnote} notes ({nev} orders with evidence in evidence/), {len(splits)} split buyers, {len(ghosts)} ghost sales, {len(silent)} silent orders, {len(chains)} off-by-one chains -> {out}' + (f'  WARNING: {unreviewed} rows not reviewed' if unreviewed else '') + (f"  changed since the last list: {diff['summary']}" if diff else ''))
    say(f'packing_slips.html: {len(packages)} slips, {slip_bytes / 1024:.0f} KB, default order {a.slip_order}')
    say(f'title "{title}", date "{date}", key {key}, stream start {start.isoformat() if start else "unknown (times fall back to createdAt)"}')

def cmd_evidence_card(w, a):
    """D12: one PNG per order (or a zip of every flagged order) from the built packing_list.html, drawn by
    scripts/evidence_card.py with the same layout as the page's "Save evidence card" button. Reads the page, not
    work/, so it works after Step 6 deleted work/: run it from the show folder, or pass --list."""
    sys.path.insert(0, HERE); import evidence_card
    argv = list(a.orders) + (['--flagged'] if a.flagged else []) + (['--list', a.list] if a.list else []) + (['--out', a.out] if a.out else []) + (['--check-layout'] if a.check_layout else [])
    sys.exit(evidence_card.main(argv, w.show_folder() if w.rows else None))

def cmd_normalize(w, a):
    """Rewrite notes.tsv product texts to catalog names (keeping the count), report what did not match."""
    cat = load_catalog(); notes_f = w.p('notes.tsv')
    if not os.path.exists(notes_f): die('no notes.tsv; run `merge` first')
    rows = [(l + ['', ''])[:3] for l in read_tsv(notes_f)]
    key = a.show or w.meta()[2]; out = []; unmatched = {}; changed = 0; bundles = 0; touched = set()
    for i, text, flag in rows:
        rule, qty = catalog_match(cat, text)
        if not rule:
            unmatched[text] = unmatched.get(text, 0) + 1; out.append((i, text, flag)); continue
        touched.add(id(rule))
        if len(rule['yields']) > 1: bundles += 1; out.append((i, text, flag)); continue
        pid, units = rule['yields'][0]; name = cat['products'][pid]['name']
        n = units if units > 1 else (qty or 1)
        new = (f'{n}x ' if n > 1 else '') + name
        if new != text: changed += 1
        out.append((i, new, flag))
    say(f'{len(rows)} rows: {len(rows) - sum(unmatched.values())} matched ({bundles} bundles kept verbatim), {changed} rewritten, {sum(unmatched.values())} unmatched')
    for text, n in sorted(unmatched.items(), key=lambda x: -x[1]): say(f'  {n:3d}x  {text[:110]}')
    if a.dry_run: return
    if a.add_unmatched and unmatched:
        for text in unmatched:
            m = QTY_RX.match(text); body = text[m.end():].strip() if m else text.strip()
            pid = re.sub(r'[^a-z0-9]+', '-', body.lower()).strip('-')[:60].rstrip('-')
            if pid not in cat['products']: cat['products'][pid] = {'name': body, 'section': a.section}
            # an existing product with this id (same words, other punctuation) just gains an exact-text rule
            cat['rules'].append({'match': '^' + re.escape(body) + '$', 'yields': [[pid, 1]], 'seen': [key]})
        if a.section not in cat['sections']: cat['sections'].append(a.section)
        say(f'{len(unmatched)} unmatched texts added to the catalog as their own products (section "{a.section}"; fix sections/wording in data/catalog.json)')
    if not os.path.exists(w.p('notes_raw.tsv')): shutil.copy(notes_f, w.p('notes_raw.tsv'))
    open(notes_f, 'w').write(''.join('\t'.join(r) + '\n' for r in out))
    for rule in cat['rules']:
        if id(rule) in touched and key not in rule.setdefault('seen', []): rule['seen'].append(key)
    save_catalog(cat); say(f'notes.tsv rewritten (original kept as notes_raw.tsv); catalog rules marked seen on {key}')
    if unmatched: say('add the unmatched ones:  python3 run.py catalog add --match "^Regex" --name "Canonical name" --section Lip [--units N]')

def cmd_catalog(w, a):
    cat = load_catalog()
    if a.action == 'list':
        for pid, p in cat['products'].items(): say(f'{p["section"]:18s} {pid:50s} {p["name"]}')
        say(f'{len(cat["products"])} products, {len(cat["rules"])} rules'); return
    if a.action == 'add':
        if not (a.match and a.name and a.section): die('catalog add needs --match, --name, --section')
        pid = a.id or re.sub(r'[^a-z0-9]+', '-', a.name.lower()).strip('-')[:60].rstrip('-')
        cat['products'].setdefault(pid, {'name': a.name, 'section': a.section})
        if a.section not in cat['sections']: cat['sections'].append(a.section)
        rule = {'match': a.match, 'yields': [[pid, a.units]], 'seen': [a.show or w.meta()[2]]}
        cat['rules'].insert(0, rule) if a.first else cat['rules'].append(rule)
        save_catalog(cat); say(f'added {pid} ({a.section}) matching /{a.match}/ x{a.units}'); return
    if a.action == 'test':
        for text in a.texts:
            rule, qty = catalog_match(cat, text)
            say(f'{text[:70]!r} -> ' + (', '.join(f"{cat['products'][pid]['name']} x{u}" for pid, u in rule['yields']) if rule else 'NO MATCH'))

STOP = set('at thank you thanks guys one more last seconds second deal bundle amazing here steal doing another purchase first go okay love the and for your we are is it that this get got oh yes no number numbers five four three two zero let us lets come on going bundles of a an in on to so ok alright right now just also with out up down again wow very nice good great cool'.split())
LEARN_WIN = 8   # words kept before a heard number: about 3 s of speech (whisper gives no word times)
NUMWORDS = set(NUM2WORD.values())
def num_in(txt, n): return re.search(r'(?<![\d.$])' + n + r'(?![\d%])', txt) or (n in NUM2WORD and re.search(r'number ' + NUM2WORD[n] + r'\b', txt))
def clean_words(s): return re.sub(r"[^a-z0-9' ]+", ' ', s).split()
def name_tokens(words, from_end=True):
    """The name next to the number: the last (or first) word with 2+ letters that is not STOP, plus the two-word phrase it ends
    when the word before it is not STOP either or is a number word (4_roses -> 'four roses')."""
    ok = lambda x: not x.isdigit() and len(re.sub(r'[^a-z]', '', x)) >= 2 and x not in STOP
    ks = [k for k, x in enumerate(words) if ok(x)]
    if not ks: return set()
    k = ks[-1] if from_end else ks[0]; out = {words[k]}
    if k and (ok(words[k - 1]) or words[k - 1] in NUMWORDS): out.add(f'{words[k - 1]} {words[k]}')
    return out
def spelling_rx(c):
    """buyers.json alternative for a heard spelling: short tokens ("cw", "jc") need anchors; a space stays a space."""
    rx = re.escape(c).replace('\\ ', ' '); return (r'\b' + rx + r'\b') if len(re.sub(r'[^a-z]', '', c)) < 4 else rx
def cmd_learn_buyers(w, a):
    """Propose whisper spellings for buyers with 3+ orders. Every clip that caught a callout spells the name its own way, so
    every transcript line holding one of the buyer's listing numbers near the lag counts: the name is the word (or two-word
    phrase) right before the number, within ~3 s and after the previous number, else the first word after it ("number 118,
    thank you, guzk"). Proposed: a spelling heard on 2+ of the buyer's orders and mostly around this buyer, or a one-off that
    looks like the username. Prints the match rate per buyer before and after (P15, Oct 8 2026). --buyers reads and writes
    another buyers.json (tests)."""
    import difflib
    global BUYERS, BUYER_PAT
    bf = a.buyers or BUYERS_F
    if a.buyers: BUYERS = json.load(open(bf)); BUYER_PAT = buyer_pat(BUYERS)
    segs = transcript_segments(w)
    if not segs: die('no transcripts')
    res = match_rows(w, segs); lag = read_lag(w) or 0; times = [s[0] for s in segs]
    last = {}; prev = []   # prev[j]: index of the line before segs[j] in the same clip
    for j, (t, txt, src) in enumerate(segs): prev.append(last.get(src)); last[src] = j
    matched = lambda rs: {b: sum(1 for i, bb, l, best in rs if bb == b and best and 'B' in best[2]) for b in {r[2] for r in w.rows}}
    before = matched(res); orders = {b: sum(1 for r in w.rows if r[2] == b) for b in before}
    cnt = {}; tot = {}
    for r in w.rows:
        b = r[2]; n = listing_num(r[4]); P = w.P(r); heard = set()
        if not n or is_giveaway(r[4], r[5]) or orders[b] < 3: continue
        for j in range(bisect.bisect_left(times, P + lag - 12), bisect.bisect_right(times, P + lag + 12)):
            t, txt, src = segs[j]; m = num_in(txt, n)
            if not m: continue
            ws = clean_words(txt[:m.start()]); p = prev[j]
            if len(ws) < LEARN_WIN and p is not None and t - segs[p][0] <= 10: ws = clean_words(segs[p][1]) + ws   # the number opened the line: the name is at the end of the previous one
            ws = ws[max([k + 1 for k, x in enumerate(ws) if x.isdigit()], default=0):][-LEARN_WIN:]   # stop at the previous number (the earlier callout)
            heard |= name_tokens(ws) or name_tokens(clean_words(txt[m.end():])[:LEARN_WIN], from_end=False)
        for c in heard: cnt.setdefault(b, {}).setdefault(c, 0); cnt[b][c] += 1; tot[c] = tot.get(c, 0) + 1   # once per order, however many clips caught it
    props = {}
    for b, cs in cnt.items():
        known = BUYER_PAT.get(b); ualpha = re.sub(r'[^a-z]', '', b.lower())
        for c, k in cs.items():
            if k * 2 < tot[c] or (known and re.search(known, c)): continue   # heard around other buyers too, or already covered
            if k >= 2 or (' ' not in c and difflib.SequenceMatcher(None, re.sub(r'[^a-z]', '', c), ualpha).ratio() >= 0.6): props.setdefault(b, {})[c] = k   # a one-off is kept only when the word looks like the username
    if not props: say('nothing to propose: every heard number already had its buyer matched'); return
    entry = lambda b: BUYERS.get(b) or [re.escape(b[:4])]   # what matched this buyer until now (the 4-letter fallback of match_rows when there is no entry)
    pat = buyer_pat(dict(BUYERS, **{b: entry(b) + [spelling_rx(c) for c in cs] for b, cs in props.items()}))   # every buyer keeps buyer_rx; unknown ones the prefix fallback
    BUYER_PAT, old = pat, BUYER_PAT; after = matched(match_rows(w, segs)); BUYER_PAT = old   # match rate with the proposals in
    for b, cs in sorted(props.items()):
        say(f'{b:24s} {before[b]:3d}/{orders[b]:<3d} -> {after[b]:3d}/{orders[b]:<3d} <- ' + ', '.join(f'"{c}" x{n}' for c, n in sorted(cs.items(), key=lambda x: (-x[1], x[0]))))
    say(f'buyer matched {sum(before.values())}/{len(res)} -> {sum(after.values())}/{len(res)} with these spellings')
    if a.write:
        for b, cs in props.items():
            lst = BUYERS.setdefault(b, entry(b))
            for c in cs:
                if spelling_rx(c) not in lst: lst.append(spelling_rx(c))
        json.dump(BUYERS, open(bf, 'w'), indent=1, ensure_ascii=False); say(f'{os.path.basename(bf)} updated ({len(props)} buyers); rerun `lag`')
    else: say('add them with --write, or edit data/buyers.json by hand (regex alternatives per username)')

def cmd_fetch(w, a):
    """Download missing segments for stream-time spans ('a-b,c-d' seconds) from seg_urls.txt (the signed link
    from the last extension send; expires in hours). Used for ghost pictures and late-order recuts."""
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor
    urls = [l.strip() for l in open(w.p('seg_urls.txt')) if l.strip()]
    def refresh():
        """Segment signatures expire before the playlist link does: re-read the playlist (show.json showUrl) for fresh ones."""
        try: link = json.load(open(w.p('show.json'))).get('showUrl')
        except Exception: link = None
        if not link: return False
        try: pl = urllib.request.urlopen(urllib.request.Request(link, headers={'User-Agent': 'Mozilla/5.0'}), timeout=60).read().decode()
        except Exception as e: say(f'  playlist refresh failed: {e}'); return False
        fresh = [l.strip() for l in pl.split('\n') if l and not l.startswith('#')]
        if len(fresh) != len(urls): say(f'  playlist changed ({len(fresh)} vs {len(urls)} segments); not refreshed'); return False
        urls[:] = fresh; open(w.p('seg_urls.txt'), 'w').write('\n'.join(urls) + '\n'); say('  segment links refreshed from the playlist'); return True
    need = set()
    for span in a.spans.split(','):
        a_, _, b_ = span.partition('-'); a_, b_ = float(a_), float(b_ or a_)
        for n, s_, d in w.segs:
            if s_ + d >= a_ and s_ <= b_ and not os.path.exists(w.p('seg', f'{n}.ts')): need.add(n)
    os.makedirs(w.p('seg'), exist_ok=True)
    def get(n):
        try:
            data = urllib.request.urlopen(urllib.request.Request(urls[n], headers={'User-Agent': 'Mozilla/5.0'}), timeout=120).read()
            if data: open(w.p('seg', f'{n}.ts'), 'wb').write(data); return 1
        except Exception as e:
            if '403' in str(e): return -1
            say(f'  segment {n}: {e}')
        return 0
    need = sorted(need)
    if need and get(need[0]) == -1 and refresh(): pass
    with ThreadPoolExecutor(16) as ex: got = sum(1 for r in ex.map(get, need) if r == 1)
    if got < len(need): say(f'  {len(need) - got} segment(s) refused (403): the show link has expired; click Send in the extension for a fresh one')
    say(f'fetched {got}/{len(need)} segments')

def cmd_categorize(w, a):
    """Rewrite free-text flags in notes.tsv as 'category: detail' using CATEGORIZE_RULES (for notes written before P08)."""
    rows = read_tsv(w.p('notes.tsv')); changed = 0; counts = {}
    out = []
    for l in rows:
        l = (l + ['', '', ''])[:4]; f = l[2].strip()
        if f and f.partition(':')[0].strip().lower() not in FLAGCATS:
            cat = next(c for c, pat in CATEGORIZE_RULES if re.search(pat, f, re.I))
            new = cat if f.lower() == cat else f'{cat}: {f}'
            if a.verbose or not a.write: say(f'  {l[0]:>4}  {new}')
            l[2] = new; changed += 1
        if f: counts[parse_flag(l[2])[0]] = counts.get(parse_flag(l[2])[0], 0) + 1
        out.append(l)
    say(f'{changed} flag(s) categorised; by category: ' + ', '.join(f'{c} {n}' for c, n in sorted(counts.items(), key=lambda x: -x[1])))
    nrev = sum(n for c, n in counts.items() if FLAGCATS[c][0] == 'revise'); say(f'{nrev} to revise, {sum(counts.values()) - nrev} notes')
    if a.write: open(w.p('notes.tsv'), 'w').write(''.join('\t'.join(l) + '\n' for l in out)); say('notes.tsv rewritten')
    else: say('dry run; add --write to rewrite notes.tsv')

# ---------- cli ----------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--work', default='.', help='the show work/ folder (default: current directory)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('status')
    p = sub.add_parser('wav'); p.add_argument('--rows', help='e.g. 0-19,40')
    p = sub.add_parser('transcribe'); p.add_argument('--model', default=MODEL); p.add_argument('--threads', type=int, default=8); p.add_argument('--verbose', action='store_true'); p.add_argument('--keep-wav', action='store_true')
    p = sub.add_parser('lag'); p.add_argument('--all', action='store_true', help='print every row, not only CHECK ones')
    p = sub.add_parser('tiles'); p.add_argument('--lag', type=int, help='tile offset in seconds; default from lag.txt by the rule in --help'); p.add_argument('--rows'); p.add_argument('--force', action='store_true')
    p = sub.add_parser('prep'); p.add_argument('--rows'); p.add_argument('--model', default=MODEL); p.add_argument('--threads', type=int, default=8); p.add_argument('--verbose', action='store_true'); p.add_argument('--all', action='store_true'); p.add_argument('--lag', type=int); p.add_argument('--force', action='store_true'); p.add_argument('--keep-wav', action='store_true')
    p = sub.add_parser('show'); p.add_argument('ids', nargs='+')
    p = sub.add_parser('recut'); p.add_argument('--rows', help='only these rows of recut.tsv, e.g. 12,40'); p.add_argument('--budget', type=int, help=f'seconds of prep allowed (default {RECUT_BUDGET}; 0 = no cap)'); p.add_argument('--force', action='store_true', help='re-cut clips that exist'); p.add_argument('--model', default=MODEL); p.add_argument('--verbose', action='store_true'); p.add_argument('--keep-wav', action='store_true'); p.add_argument('--all', action='store_true', help='lag: print every row')
    p = sub.add_parser('watch'); p.add_argument('--every', type=int, default=10, help='seconds between checks'); p.add_argument('--stall', type=int, default=300, help='seconds without a new row before STALLED'); p.add_argument('--timeout', type=int, default=2400, help='seconds before TIMEOUT (exit 2)'); p.add_argument('--once', action='store_true', help='print the progress once and exit')
    sub.add_parser('missing')
    p = sub.add_parser('chunks'); p.add_argument('--n', type=int, help='number of chunks (default: enough for --size rows each)'); p.add_argument('--size', type=int, default=20, help='rows per chunk (default 20, P17)'); p.add_argument('--template', help='review instructions template (default scripts/review_instructions.md)'); p.add_argument('--spellings', help='file with buyer spellings for this show'); p.add_argument('--notes', help='file with product notes for this show')
    sub.add_parser('merge')
    p = sub.add_parser('categorize'); p.add_argument('--write', action='store_true'); p.add_argument('--verbose', action='store_true')
    p = sub.add_parser('fetch'); p.add_argument('spans', help='stream seconds, e.g. 120-200,3400-3460')
    p = sub.add_parser('normalize'); p.add_argument('--dry-run', action='store_true'); p.add_argument('--show', help='show key to mark rules seen on (default mmdd of this show)'); p.add_argument('--add-unmatched', action='store_true', help='add every unmatched text to the catalog as its own product'); p.add_argument('--section', default='Other')
    p = sub.add_parser('catalog'); p.add_argument('action', choices=['list', 'add', 'test']); p.add_argument('texts', nargs='*'); p.add_argument('--match'); p.add_argument('--name'); p.add_argument('--section'); p.add_argument('--units', type=int, default=1); p.add_argument('--id'); p.add_argument('--first', action='store_true', help='put the rule before all others'); p.add_argument('--show')
    p = sub.add_parser('learn-buyers'); p.add_argument('--write', action='store_true'); p.add_argument('--buyers', help='another buyers.json to read and (with --write) update, instead of data/buyers.json')
    p = sub.add_parser('build'); p.add_argument('--evidence', choices=['flagged', 'all'], default='all', help='frames + transcript panel for every order (default, Juan asked Oct 6, 2026) or flagged rows only'); p.add_argument('--title'); p.add_argument('--date', help='"Sep 25, 2026"'); p.add_argument('--key', help='localStorage key, default mmdd'); p.add_argument('--start', help='stream start ISO (UTC)'); p.add_argument('--out', help='output folder, default the parent of work/')
    p.add_argument('--slip-note', help='thank-you line at the foot of every packing slip; remembered in data/slip_note.txt ("" clears it)'); p.add_argument('--slip-order', choices=['username', 'count', 'time'], default='username', help='default order of the slips (username = Whatnot label export order)')
    p = sub.add_parser('evidence-card', help='PNG evidence card per order for a Whatnot claim (D12); --flagged zips every revise row'); p.add_argument('orders', nargs='*', help='Whatnot order numbers'); p.add_argument('--flagged', action='store_true'); p.add_argument('--list', help='packing_list.html (default ./packing_list.html, else the show folder)'); p.add_argument('--out', help='folder for the files (default next to the list)'); p.add_argument('--check-layout', action='store_true', help='diff the layout with the page JavaScript (node) for one order')
    a = ap.parse_args()
    w = Work(a.work, need_rows=a.cmd != 'evidence-card')
    {'status': cmd_status, 'wav': cmd_wav, 'transcribe': cmd_transcribe, 'lag': cmd_lag, 'tiles': cmd_tiles, 'prep': cmd_prep, 'recut': cmd_recut,
     'show': cmd_show, 'chunks': cmd_chunks, 'watch': cmd_watch, 'missing': cmd_missing, 'merge': cmd_merge, 'build': cmd_build, 'normalize': cmd_normalize, 'categorize': cmd_categorize, 'fetch': cmd_fetch,
     'catalog': cmd_catalog, 'learn-buyers': cmd_learn_buyers, 'evidence-card': cmd_evidence_card}[a.cmd](w, a)

if __name__ == '__main__':
    main()
