#!/usr/bin/env python3
"""Manage packing-list clients (access codes + show credits). The receiver reads this file live.

  python3 clients.py add "Maria's Beauty" --credits 4      # prints the new access code
  python3 clients.py add "Maria's Beauty" --credits 4 --email maria@example.com   # kept in the ledger, so the code can be mailed
  python3 clients.py list
  python3 clients.py credit "Maria's Beauty" 2              # add 2 shows (negative to remove)
  python3 clients.py disable "Maria's Beauty"  /  enable "Maria's Beauty"
  python3 clients.py shows "Maria's Beauty"                 # what they've sent
Names or codes both work as the selector.
"""
import argparse, json, os, secrets, datetime
LEDGER = os.path.expanduser('~/Library/Application Support/whatnot-receiver/clients.json')

def load():
    try: return json.load(open(LEDGER))
    except Exception: return {'clients': {}}
def save(d):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    tmp = LEDGER + '.tmp'; json.dump(d, open(tmp, 'w'), indent=1); os.replace(tmp, LEDGER)
def find(d, sel):
    if sel in d['clients']: return sel
    hits = [k for k, c in d['clients'].items() if c['name'].lower() == sel.lower()]
    if len(hits) != 1: raise SystemExit(f'no unique client named {sel!r}')
    return hits[0]

ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest='cmd', required=True)
a = sub.add_parser('add'); a.add_argument('name'); a.add_argument('--credits', type=int, default=1)
a.add_argument('--email', default='', help='where the access code goes (the email from a paid order on the landing page); kept in the ledger')
sub.add_parser('list')
c = sub.add_parser('credit'); c.add_argument('sel'); c.add_argument('n', type=int)
for n in ('disable', 'enable', 'shows'): sub.add_parser(n).add_argument('sel')
A = ap.parse_args(); d = load()

if A.cmd == 'add':
    code = secrets.token_hex(8)
    d['clients'][code] = {'name': A.name, 'credits': A.credits, 'used': 0, 'active': True, 'created': datetime.date.today().isoformat(), 'shows': {}}
    email = A.email.strip()
    if email: d['clients'][code]['email'] = email
    save(d); print(f'{A.name}: access code {code}  ({A.credits} show credits)' + (f'  -> mail it to {email}' if email else ''))
elif A.cmd == 'list':
    print(f"{'name':24} {'code':18} {'left':>4} {'used':>4}  {'status':8}  email")
    for k, cl in sorted(d['clients'].items(), key=lambda kv: kv[1]['name'].lower()):
        print(f"{cl['name'][:24]:24} {k:18} {cl.get('credits',0):>4} {cl.get('used',0):>4}  {'active' if cl.get('active', True) else 'DISABLED':8}  {cl.get('email', '')}")
elif A.cmd == 'credit':
    k = find(d, A.sel); d['clients'][k]['credits'] = max(0, d['clients'][k].get('credits', 0) + A.n); save(d)
    print(f"{d['clients'][k]['name']}: {d['clients'][k]['credits']} shows left")
elif A.cmd in ('disable', 'enable'):
    k = find(d, A.sel); d['clients'][k]['active'] = (A.cmd == 'enable'); save(d); print(f"{d['clients'][k]['name']}: {A.cmd}d")
elif A.cmd == 'shows':
    k = find(d, A.sel)
    for live, sh in d['clients'][k].get('shows', {}).items():
        print(f"{sh.get('first','')[:16]}  {sh.get('title','')[:40]:40} {sh.get('items',0):>4} orders  sends={sh.get('sends',1)}  {sh.get('folder','')}")
