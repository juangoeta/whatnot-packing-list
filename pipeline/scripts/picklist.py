#!/usr/bin/env python3
"""Pick list by product from packages.json, using the shared catalog (pipeline/data/catalog.json).
  python3 picklist.py            (from work/; reads packages.json, writes picklist.json, prints unmatched)
Each product text is matched against the catalog rules (first match wins); a rule yields one or more
products with units (a bundle of two different items yields two lines). Add missing products with
`run.py catalog add`. Then `picklist_build.py` renders pick_list.{csv,md,html}."""
import json, re, collections, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import load_catalog, catalog_match

def canon(cat, text):
    """[(section, name, units)] for a product text, or None."""
    rule, qty = catalog_match(cat, text)
    if not rule: return None
    out = []
    for pid, u in rule['yields']:
        p = cat['products'][pid]; out.append((p['section'], p['name'], u))   # units come from the rule, not the 'Nx' prefix (a rule that enumerates a bundle already counts them)
    return out

def main():
    cat = load_catalog(); pk = json.load(open('packages.json'))
    items = collections.OrderedDict(); un = []
    for p in pk:
        for it in p['items']:
            outs = canon(cat, it['product'])
            if not outs: un.append(it['product']); continue
            for sec, name, u in outs:
                d = items.setdefault((sec, name), {'units': 0, 'orders': 0, 'pk': collections.Counter(), 'flag': 0})
                d['units'] += u; d['orders'] += 1; d['pk'][p['num']] += u; d['flag'] += bool(it['flag'])
    print('unmatched', len(un)); [print(' ', u[:120]) for u in un]
    secs = list(cat['sections']) + sorted({k[0] for k in items} - set(cat['sections']))
    out = []
    for s in secs:
        rows = sorted([(k[1], v) for k, v in items.items() if k[0] == s], key=lambda x: -x[1]['units'])
        if rows: out.append({'section': s, 'items': [{'name': n, 'units': v['units'], 'orders': v['orders'], 'flag': v['flag'], 'pk': sorted(v['pk'].items())} for n, v in rows]})
    json.dump(out, open('picklist.json', 'w'), indent=1)
    print(sum(v['orders'] for v in items.values()), len(items), sum(v['units'] for v in items.values()))

if __name__ == '__main__': main()
