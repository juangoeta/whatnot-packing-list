#!/usr/bin/env python3
"""Dispute evidence card (D12): one PNG per order with the show, buyer, order and item numbers, sale time,
the three frames with their offsets, the transcript around the sale (callout highlighted), the identified
product and a footer. The page (`page_template.html`, block CARD-BEGIN..CARD-END) draws the same card on a
canvas; this module draws it with Pillow for the Desktop copy. The LAYOUT IS SHARED: `card_layout` here and
`cardLayout` there must produce the same list of drawing ops for the same order. `--check-layout` runs the
page's JavaScript with node and diffs the two.

    python3 run.py evidence-card <order> [<order> ...]   # evidence-<order>.png next to packing_list.html
    python3 run.py evidence-card --flagged                 # every revise-flag row -> evidence-cards.zip
    python3 evidence_card.py --list <packing_list.html> --out <dir> <order>...

Reads the orders from packing_list.html (works after work/ is deleted); frames come from evidence/ next to
it, or from the sheets of a published copy (pack_evidence.py). On demand only: nothing runs at build time."""
import io, json, math, os, re, sys, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- layout constants; the JS CARD object in page_template.html is a copy of this ----
W, PAD, GAP, FW, FH = 960, 40, 50, 260, 462          # card width, margin, gap, frame box (9:16)
FONT = {'h1': ('bold', 40, 'sans'), 'h2': ('', 24, 'sans'), 'lab': ('bold', 18, 'sans'), 'val': ('', 30, 'sans'),
        'prod': ('bold', 30, 'sans'), 'tx': ('', 24, 'sans'), 'txb': ('bold', 24, 'sans'), 'tt': ('', 22, 'mono'),
        'chip': ('bold', 24, 'mono'), 'foot': ('', 20, 'sans')}
COL = {'bg': '#FAF7F2', 'ink': '#221D25', 'muted': '#6E6674', 'rule': '#E4DCD4', 'hit': '#FFF3CF', 'hitInk': '#7A5200',
       'black': '#000000', 'white': '#FFFFFF', 'chip': '#000000'}
FONT_FILES = {'sans': ['/System/Library/Fonts/Helvetica.ttc', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'],
              'mono': ['/System/Library/Fonts/Menlo.ttc', '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf']}


def off_txt(o):
    """'-6 s' / '+3 s'; JS Math.round semantics (half up), not Python's banker's rounding."""
    r = math.floor(o + 0.5)
    return f"{'+' if o >= 0 else ''}{r} s"


def wrap_text(s, mx):
    out, line = [], ''
    for w in str(s).split():
        while len(w) > mx:
            if line: out.append(line); line = ''
            out.append(w[:mx]); w = w[mx:]
        if not line: line = w
        elif len(line) + 1 + len(w) <= mx: line += ' ' + w
        else: out.append(line); line = w
    if line: out.append(line)
    return out or ['']


def clip(s, n):
    s = str(s)
    return s[:n - 1] + '…' if len(s) > n else s


def card_model(p, it, meta, check):
    """What goes on the card for one order; same fields as cardModel in the page."""
    ev = it.get('evidence') or {}
    frames = [{'off': f['off']} for f in ev.get('frames', [])[:3]] if ev.get('frames') else [{'off': o} for o in (ev.get('offs') or [])[:3]]
    item_tag = it['listing'] if it.get('itemNo') is None else f"#{it['itemNo']} · {it['listing']}"
    lvl = it.get('flagLevel') or ''
    flag = ('Marked for a double-check: ' + (it.get('flagText') or it.get('flag') or '')) if lvl == 'revise' else ('Note: ' + (it.get('flag') or '')) if lvl == 'note' else ''
    return {'show': meta.get('show') or '', 'date': meta.get('date') or '', 'buyer': p['buyer'], 'order': str(it['order']), 'itemTag': item_tag,
            'time': f"{it.get('time') or ''} · ${it['price']:.2f}", 'pkg': f"Package {p['num']}" + (f" · label {p['split']}" if p.get('split') else ''),
            'tracking': p.get('tracking') or '', 'product': it.get('product') or '', 'flagText': flag, 'frames': frames,
            'transcript': ev.get('transcript') or [], 'lag': check.get('lag') if check else None}


def card_layout(m):
    """-> (ops, height). ops: ['rect', x, y, w, h, color] | ['text', x, baseline, str, font, color] | ['img', slot, x, y, w, h]."""
    ops = []; X = PAD; IW = W - 2 * PAD; y = PAD
    def text(s, fk, color, x, yy): ops.append(['text', x, yy, s, fk, color])
    def base(yy, size): return yy + round(size * 0.78)
    for l in wrap_text(m['show'], 40): text(l, 'h1', COL['ink'], X, base(y, 40)); y += 48
    y += 4; text(f"Whatnot show · {m['date']} · evidence for order {m['order']}", 'h2', COL['muted'], X, base(y, 24)); y += 32
    y += 18; ops.append(['rect', X, y, IW, 2, COL['rule']]); y += 22
    CW = (IW - GAP) // 2
    facts = [['Buyer', m['buyer']], ['Order #', m['order']], ['Item', m['itemTag']], ['Sold at', m['time']], ['Package', m['pkg']], ['Tracking', m['tracking'] or '(no label yet)']]
    for i, (k, v) in enumerate(facts):
        cx = X + (i % 2) * (CW + GAP); cy = y + (i // 2) * 92
        text(k.upper(), 'lab', COL['muted'], cx, base(cy, 18)); text(clip(v, 26), 'val', COL['ink'], cx, base(cy + 26, 30))
    y += 3 * 92
    text('WHAT WAS SOLD', 'lab', COL['muted'], X, base(y, 18)); y += 26
    for l in wrap_text(m['product'], 53): text(l, 'prod', COL['ink'], X, base(y, 30)); y += 38
    if m['flagText']: y += 6; text(clip(m['flagText'], 80), 'tx', COL['hitInk'], X, base(y, 24)); y += 32
    y += 22
    text('PICTURES FROM THE RECORDING AT THE SALE (seconds from the countdown)', 'lab', COL['muted'], X, base(y, 18)); y += 30
    for k in range(3):
        fx = X + k * (FW + GAP); ops.append(['rect', fx, y, FW, FH, COL['black']])
        f = m['frames'][k] if k < len(m['frames']) else None
        if f:
            ops.append(['img', k, fx, y, FW, FH]); lbl = off_txt(f['off']); cw = len(lbl) * 15 + 24
            ops.append(['rect', fx + 10, y + FH - 10 - 36, cw, 36, COL['chip']]); text(lbl, 'chip', COL['white'], fx + 22, base(y + FH - 10 - 36 + 6, 24))
        else: text('no picture', 'tx', COL['muted'], fx + 90, base(y + FH // 2 - 12, 24))
    y += FH + 30
    text('WHAT WAS SAID (seconds from the countdown; the callout is highlighted)', 'lab', COL['muted'], X, base(y, 18)); y += 30
    if m['transcript']:
        for o, txt, hit in m['transcript']:
            lines = wrap_text(txt, 67); h = len(lines) * 32 + 8
            if hit: ops.append(['rect', X - 8, y, IW + 16, h, COL['hit']])
            text(off_txt(o), 'tt', COL['hitInk'] if hit else COL['muted'], X, base(y + 4, 22))
            for i, l in enumerate(lines): text(l, 'txb' if hit else 'tx', COL['hitInk'] if hit else COL['ink'], X + 100, base(y + 4 + i * 32, 24))
            y += h
    else: text('No transcript saved for this order.', 'tx', COL['muted'], X, base(y, 24)); y += 32
    y += 22; ops.append(['rect', X, y, IW, 2, COL['rule']]); y += 18
    foot = 'Frames from the show recording at the moment of sale, corrected for the receipt offset' + (f" ({off_txt(m['lag'])} on this stream)" if m['lag'] is not None else '') + ". Transcript from the seller's mic. Order data from the Whatnot Shipments page."
    for l in wrap_text(foot, 91): text(l, 'foot', COL['muted'], X, base(y, 20)); y += 26
    y += PAD - 8
    return ops, y


# ---- Pillow painter ----
_fonts = {}
def font(fk):
    from PIL import ImageFont
    if fk in _fonts: return _fonts[fk]
    weight, size, fam = FONT[fk]; f = None
    for path in FONT_FILES[fam]:
        if not os.path.exists(path): continue
        try:
            f = ImageFont.truetype(path, size, index=1 if (weight == 'bold' and path.endswith('.ttc')) else 0)
            if weight == 'bold' and not path.endswith('.ttc'):
                b = path.replace('.ttf', '-Bold.ttf'); f = ImageFont.truetype(b, size) if os.path.exists(b) else f
            break
        except Exception: f = None
    if f is None: f = ImageFont.load_default(size)
    _fonts[fk] = f; return f


def card_images(ev, base):
    """[(PIL image, (sx, sy, sw, sh)) | None] per frame slot, from evidence/ files or a published sheet."""
    from PIL import Image
    out = []
    if ev.get('frames'):
        for f in ev['frames'][:3]:
            try: im = Image.open(os.path.join(base, f['file'])).convert('RGB'); out.append((im, (0, 0, im.width, im.height)))
            except Exception: out.append(None)
    elif ev.get('sheet'):
        try:
            im = Image.open(os.path.join(base, ev['sheet'])).convert('RGB'); fw = ev['w'] // 3
            for c in range(ev.get('k') or 3): out.append((im, (c * fw, ev['idx'] * ev['h'], fw, ev['h'])))
        except Exception: pass
    return out


def paint(ops, H, imgs):
    from PIL import Image, ImageDraw
    im = Image.new('RGB', (W, H), COL['bg']); d = ImageDraw.Draw(im)
    for op in ops:
        if op[0] == 'rect': d.rectangle([op[1], op[2], op[1] + op[3] - 1, op[2] + op[4] - 1], fill=op[5])
        elif op[0] == 'text': d.text((op[1], op[2]), op[3], font=font(op[4]), fill=op[5], anchor='ls')
        elif op[0] == 'img':
            src = imgs[op[1]] if op[1] < len(imgs) else None
            if not src: continue
            pic, (sx, sy, sw, sh) = src; s = min(op[4] / sw, op[5] / sh); dw, dh = round(sw * s), round(sh * s)
            crop = pic.crop((sx, sy, sx + sw, sy + sh)).resize((dw, dh), Image.LANCZOS)
            im.paste(crop, (op[2] + (op[4] - dw) // 2, op[3] + (op[5] - dh) // 2))
    return im


def png_bytes(im):
    b = io.BytesIO(); im.save(b, 'PNG', optimize=True); return b.getvalue()


# ---- page reading ----
def load_page(path):
    """DATA, META, CHECK from a built packing_list.html (META falls back to the header text on older pages)."""
    html = open(path).read()
    m = re.search(r'const DATA=(\[.*?\]);\n', html, re.S)
    if not m: raise SystemExit(f'{path}: no DATA block (not a packing_list.html?)')
    data = json.loads(m.group(1))
    mm = re.search(r'const META=(\{.*?\});\n', html); meta = json.loads(mm.group(1)) if mm else {}
    if not meta:
        s = re.search(r'<small>Whatnot show "(.*?)" · (.*?) · ', html)
        if s: meta = {'show': s.group(1), 'date': s.group(2)}
    mc = re.search(r'const CHECK=(\{.*?\});\n', html); check = json.loads(mc.group(1)) if mc else {}
    return data, meta, check


def render(p, it, meta, check, base):
    m = card_model(p, it, meta, check); ops, H = card_layout(m)
    return png_bytes(paint(ops, H, card_images(it.get('evidence') or {}, base)))


def find_list(given, work_show_folder=None):
    for c in [given, 'packing_list.html', work_show_folder and os.path.join(work_show_folder, 'packing_list.html')]:
        if c and os.path.exists(c): return os.path.abspath(c)
    raise SystemExit('no packing_list.html here; pass --list <path to packing_list.html>')


def check_layout(list_path, order):
    """Run the page's cardLayout with node on the same order and compare the op lists with ours."""
    import subprocess
    data, meta, check = load_page(list_path)
    where = {str(it['order']): (p, it) for p in data for it in p['items']}
    if order not in where: raise SystemExit(f'order {order} not in {list_path}')
    p, it = where[order]; m = card_model(p, it, meta, check); ops, H = card_layout(m)
    tpl = open(os.path.join(HERE, 'page_template.html')).read()
    js = re.search(r'/\*CARD-BEGIN\*/(.*?)/\*CARD-END\*/', tpl, re.S).group(1)
    prog = js + f"\nconst L=cardLayout({json.dumps(m)});process.stdout.write(JSON.stringify([L.ops,L.H]));"
    r = subprocess.run(['node', '-e', prog], capture_output=True, text=True)
    if r.returncode: raise SystemExit('node failed: ' + r.stderr)
    j_ops, j_h = json.loads(r.stdout)
    diffs = [(a, b) for a, b in zip(ops, j_ops) if a != b]
    if H == j_h and len(ops) == len(j_ops) and not diffs: print(f'layout identical: {len(ops)} ops, {W}x{H}'); return 0
    print(f'LAYOUT DIFFERS: py {len(ops)} ops h={H}, js {len(j_ops)} ops h={j_h}')
    for a, b in diffs[:10]: print(' py', a); print(' js', b)
    return 1


def main(argv=None, work_show_folder=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('orders', nargs='*', help='Whatnot order numbers')
    ap.add_argument('--flagged', action='store_true', help='every revise-flag row, zipped as evidence-cards.zip')
    ap.add_argument('--list', help='packing_list.html (default: ./packing_list.html, else the show folder of --work)')
    ap.add_argument('--out', help='folder for the PNG/zip (default: next to the list)')
    ap.add_argument('--check-layout', action='store_true', help='compare the layout with the page JavaScript (needs node) for one order')
    a = ap.parse_args(argv)
    lst = find_list(a.list, work_show_folder); base = os.path.dirname(lst); out = a.out or base
    if a.check_layout:
        if len(a.orders) != 1: raise SystemExit('--check-layout takes one order')
        return check_layout(lst, a.orders[0])
    data, meta, check = load_page(lst)
    where = {str(it['order']): (p, it) for p in data for it in p['items']}
    if a.flagged: todo = [(p, it) for p in data for it in p['items'] if it.get('flagLevel') == 'revise' and it.get('evidence')]
    else:
        missing = [o for o in a.orders if o not in where]
        if missing: raise SystemExit(f'not in {lst}: {", ".join(missing)}')
        todo = [where[o] for o in a.orders]
    if not todo: raise SystemExit('nothing to do: pass order numbers or --flagged (rows with evidence only)')
    os.makedirs(out, exist_ok=True)
    if a.flagged:
        dest = os.path.join(out, 'evidence-cards.zip')
        with zipfile.ZipFile(dest, 'w', zipfile.ZIP_STORED) as z:
            for p, it in todo: z.writestr(f"evidence-{it['order']}.png", render(p, it, meta, check, base))
        print(f'{len(todo)} cards, {os.path.getsize(dest) / 1e6:.1f} MB -> {dest}')
    else:
        for p, it in todo:
            dest = os.path.join(out, f"evidence-{it['order']}.png"); png = render(p, it, meta, check, base)
            open(dest, 'wb').write(png); print(f'{len(png) / 1e3:.0f} KB -> {dest}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
