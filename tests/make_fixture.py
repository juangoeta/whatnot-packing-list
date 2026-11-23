#!/usr/bin/env python3
"""Turn a saved inbox bundle into a test fixture that holds no secrets and no real buyer.

  python3 tests/make_fixture.py "~/Library/Application Support/whatnot-receiver/inbox/<file>.json" tests/fixtures/bundle_x.json

Dropped: showUrl (signed, expires) and pageUrl. Replaced, same value -> same stand-in everywhere in the bundle:
buyer usernames (buyer01, buyer02, ... in order of first appearance in the package list), shipment ids,
order numbers, tracking numbers and the livestream id. Everything else (listings, prices, receipt timestamps,
package sizes, carrier, show title) is kept, so the fixture exercises the same code paths as the real show.
The inbox file is only read.
"""
import hashlib, json, os, sys, uuid

def anonymize(b):
    b = json.loads(json.dumps(b))   # never touch the caller's object
    b.pop('showUrl', None); b.pop('pageUrl', None)
    ships = b.get('shipments') or []
    buyers, sids, orders, tracks = {}, {}, {}, {}
    def sub(table, key, fmt):
        if key in (None, ''): return key
        if key not in table: table[key] = fmt(len(table) + 1)
        return table[key]
    for s in ships:   # packages first so buyer numbers follow the package list
        s['buyer'] = sub(buyers, s['buyer'], lambda n: f'buyer{n:02d}')
        s['shipmentId'] = sub(sids, str(s['shipmentId']), lambda n: str(900000 + n))
        s['tracking'] = sub(tracks, s.get('tracking'), lambda n: f'9400{n:018d}')
    for it in b['items']:
        it['buyer'] = sub(buyers, it['buyer'], lambda n: f'buyer{n:02d}')
        it['shipmentId'] = sub(sids, str(it['shipmentId']), lambda n: str(900000 + n))
        it['orderNo'] = sub(orders, str(it['orderNo']), lambda n: str(1000000 + n))
    ls = b.get('livestream') or {}
    if ls.get('id'): ls['id'] = str(uuid.UUID(hashlib.sha1(ls['id'].encode()).hexdigest()[:32]))
    return b

if __name__ == '__main__':
    if len(sys.argv) != 3: sys.exit(__doc__)
    src = json.load(open(os.path.expanduser(sys.argv[1])))
    out = anonymize(src)
    json.dump(out, open(sys.argv[2], 'w'), separators=(',', ':'))
    print(f"{sys.argv[2]}: {len(out['items'])} items, {len(out.get('shipments') or [])} packages, {os.path.getsize(sys.argv[2]) // 1024} KB")
