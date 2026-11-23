#!/usr/bin/env python3
"""Sales recap numbers for one show, from its work/ folder.
  python3 recap.py [--work DIR] [--product SUBSTR] [--bundle-name SUBSTR=NAME] [--run-gap SEC]
Reads rows.tsv, notes.tsv, ship.tsv, segtimes.tsv (show length = last segment start + duration) and the shared
catalog (via picklist.canon). Prints the numbers and writes recap.json (prod, pace, buyers, sec, runs) for
recap_template.html. The "by time" run analysis uses the highest-revenue product unless --product names another
(case-insensitive substring of the catalog name). A product text that the catalog expands to several products is
named "A + B"; --bundle-name renames such a line when its joined name contains SUBSTR
(e.g. --bundle-name "hand cream 2 oz=Hand cream BUNDLE (Pure + Pistachio)")."""
import json, collections, statistics, sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from picklist import load_catalog, canon as _canon

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('--work', default='.', help='show work/ folder (default: current directory)')
ap.add_argument('--product', help='product for the by-time run analysis (substring; default: top revenue)')
ap.add_argument('--bundle-name', action='append', default=[], metavar='SUBSTR=NAME', help='rename a multi-product line whose name contains SUBSTR')
ap.add_argument('--run-gap', type=float, default=300, help='seconds between sales that separate two runs (default 300)')
a = ap.parse_args()
os.chdir(a.work)
renames = [tuple(s.split('=', 1)) for s in a.bundle_name if '=' in s]

def read_tsv(f): return [l.rstrip('\n').split('\t') for l in open(f) if l.strip()]
CAT = load_catalog()
rows = read_tsv('rows.tsv')
notes = {l[0]: l for l in read_tsv('notes.tsv')}
ship = {l[0]: l for l in read_tsv('ship.tsv')}
def canon(t): return _canon(CAT, t)

S = []; unmatched = []
for r in rows:
    outs = canon(notes[r[0]][1])
    if not outs: unmatched.append(notes[r[0]][1]); continue
    units = sum(o[2] for o in outs)
    name = outs[0][1] if len(outs) == 1 else ' + '.join(o[1] for o in outs)
    if len(outs) > 1:
        for sub, new in renames:
            if sub in name: name = new; break
    S.append(dict(i=int(r[0]), buyer=r[2], price=int(r[5]) / 100, P=float(r[6]) + 30, sec=outs[0][0], name=name, units=units, first=ship[r[1]][6] == 'true'))
if unmatched:
    print(f'{len(unmatched)} rows not in the catalog (skipped; add them with run.py catalog add):', file=sys.stderr)
    for u in unmatched: print('  ' + u[:120], file=sys.stderr)

# show length: end of the last downloaded segment; fall back to the last sale when there is no recording
try:
    _, st, du = read_tsv('segtimes.tsv')[-1][:3]; L = float(st) + float(du)
except Exception:
    L = max(s['P'] for s in S); print('no segtimes.tsv; show length = last sale', file=sys.stderr)

paid = [s for s in S if s['sec'] != 'Giveaways']
tot = sum(s['price'] for s in paid)
print('sales', tot, 'paid orders', len(paid), 'avg', round(tot / len(paid), 2), 'median', statistics.median(s['price'] for s in paid), 'show length min', round(L / 60), 'first sale min', round(min(s['P'] for s in S) / 60, 1))
print('per hour $', round(tot / (L / 3600)), 'orders/hr', round(len(paid) / (L / 3600)))
# price distribution
c = collections.Counter(int(s['price']) for s in paid); print('price dist', sorted(c.items()))
# pace per 15 min
b = collections.defaultdict(lambda: [0, 0])
for s in paid: k = int(s['P'] // 900); b[k][0] += s['price']; b[k][1] += 1
pace = [(k * 15, round(v[0]), v[1]) for k, v in sorted(b.items())]
print('pace', pace)
# products
g = collections.defaultdict(list)
for s in paid: g[s['name']].append(s)
prod = []
for n, l in g.items():
    rev = sum(x['price'] for x in l); u = sum(x['units'] for x in l)
    l2 = sorted(l, key=lambda x: x['P']); gaps = [b_['P'] - a_['P'] for a_, b_ in zip(l2, l2[1:]) if b_['P'] - a_['P'] < 120]
    prod.append(dict(name=n, sec=l[0]['sec'], orders=len(l), units=u, rev=rev, avg=rev / len(l), per_unit=rev / u, first=l2[0]['price'], last=l2[-1]['price'], lo=min(x['price'] for x in l), hi=max(x['price'] for x in l), gap=statistics.median(gaps) if gaps else None))
prod.sort(key=lambda p: -p['rev'])
print('\nTOP REV'); [print(f"{p['rev']:6.0f} {p['orders']:3d}o {p['units']:3d}u avg{p['avg']:5.2f} /unit{p['per_unit']:5.2f} {p['first']:.0f}->{p['last']:.0f} lo{p['lo']:.0f} hi{p['hi']:.0f} gap{p['gap']} | {p['name'][:70]}") for p in prod[:25]]
print('\nLOWEST per-order (>=3 orders)'); [print(f"{p['avg']:5.2f} {p['orders']}o /unit{p['per_unit']:.2f} | {p['name'][:70]}") for p in sorted([p for p in prod if p['orders'] >= 3], key=lambda p: p['avg'])[:10]]
print('\nHIGHEST per-order (>=2)'); [print(f"{p['avg']:5.2f} {p['orders']}o | {p['name'][:70]}") for p in sorted([p for p in prod if p['orders'] >= 2], key=lambda p: -p['avg'])[:10]]
print('\n$1 sales:', [(s['name'][:40]) for s in paid if s['price'] <= 1])
sec = collections.defaultdict(lambda: [0, 0])
for s in paid: sec[s['sec']][0] += s['price']; sec[s['sec']][1] += 1
secs = sorted([(k, round(v[0]), v[1]) for k, v in sec.items()], key=lambda x: -x[1])
print('\nSECTIONS', secs)
by = collections.defaultdict(lambda: [0, 0, False])
for s in S:
    by[s['buyer']][0] += s['price']; by[s['buyer']][1] += 1; by[s['buyer']][2] = by[s['buyer']][2] or s['first']
bl = sorted(by.items(), key=lambda x: -x[1][0])
print('\nBUYERS', len(bl), 'first-time', sum(1 for b_ in bl if b_[1][2]), 'first-time $', sum(b_[1][0] for b_ in bl if b_[1][2]))
[print(f"{v[0]:6.0f} {v[1]:3d} {'NEW' if v[2] else ''} {k}") for k, v in bl[:12]]
top5 = sum(v[0] for k, v in bl[:5]); top10 = sum(v[0] for k, v in bl[:10]); print('top5 share', round(top5 / tot * 100), 'top10', round(top10 / tot * 100), 'one-item buyers', sum(1 for k, v in bl if v[1] == 1))

# one product by time: its sales grouped into runs (a gap of --run-gap seconds or more starts a new run)
if a.product:
    hits = [p['name'] for p in prod if a.product.lower() in p['name'].lower()]
    if not hits: sys.exit(f'--product {a.product!r} matches no product sold; names are in recap.json prod[].name')
    focus = hits[0]
else: focus = prod[0]['name']
print(f'\n{focus} by time'); l = sorted(g[focus], key=lambda x: x['P'])
runs = []
for x in l:
    if runs and x['P'] - runs[-1][-1]['P'] < a.run_gap: runs[-1].append(x)
    else: runs.append([x])
for r_ in runs: print(round(r_[0]['P'] / 60), 'min', len(r_), 'sales avg', round(sum(x['price'] for x in r_) / len(r_), 2), [int(x['price']) for x in r_])
d = []
for n, l in g.items():
    l = sorted(l, key=lambda x: x['P'])
    if len(l) >= 5: d.append((sum(x['price'] for x in l[:2]) / 2, sum(x['price'] for x in l[-2:]) / 2, len(l), n[:50]))
print('\nDECAY first2 vs last2'); [print(f"{a_:5.1f} -> {b_:5.1f} ({c_}) {n}") for a_, b_, c_, n in d]
if d: print('avg first2', round(sum(x[0] for x in d) / len(d), 2), 'avg last2', round(sum(x[1] for x in d) / len(d), 2))
json.dump(dict(show_len=round(L), prod=prod, pace=pace, buyers=[(k, v[0], v[1], v[2]) for k, v in bl], sec=secs,
               runs_product=focus, runs=[(round(x['P'] / 60, 1), x['price'], x['buyer']) for x in sorted(g[focus], key=lambda x: x['P'])]),
          open('recap.json', 'w'))
