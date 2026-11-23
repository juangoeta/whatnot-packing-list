#!/usr/bin/env python3
"""usage: python3 picklist_build.py "<title>" "<Mon D, YYYY>" "<out dir>" [key]
Renders picklist.json (from picklist.py) as pick_list.{csv,md,html} in out dir. key = localStorage key for
the "pulled" checkboxes (default pick<mmdd>); two lists the same day need different keys."""
import json, csv, os, sys, datetime
title, date, out = sys.argv[1:4]
key = sys.argv[4] if len(sys.argv) > 4 else 'pick' + datetime.datetime.strptime(date, '%b %d, %Y').strftime('%m%d')
D = json.load(open('picklist.json')); npk = len(json.load(open('packages.json'))) if os.path.exists('packages.json') else 0
tot_u = sum(i['units'] for s in D for i in s['items']); tot_l = sum(len(s['items']) for s in D)
os.makedirs(out, exist_ok=True)
with open(f'{out}/pick_list.csv', 'w', newline='') as f:
    w = csv.writer(f); w.writerow(['Section', 'Product', 'Units to pull', 'Orders', 'Goes into packages (pkg x units)', 'Orders flagged'])
    for s in D:
        for i in s['items']: w.writerow([s['section'], i['name'], i['units'], i['orders'], ', '.join(f'{p} x{u}' for p, u in i['pk']), i['flag'] or ''])
md = [f'# Pick list by product - {title} ({date})\n', f'{tot_l} products, {tot_u} units to pull for {npk} packages. Package numbers match packing_list.\n']
for s in D:
    md.append(f"\n## {s['section']}\n\n| Pull | Product | Orders | Packages (pkg x units) |\n|---:|---|---:|---|")
    for i in s['items']: md.append(f"| **{i['units']}** | {i['name']}{' ⚠' if i['flag'] else ''} | {i['orders']} | {', '.join(f'{p}x{u}' for p, u in i['pk'])} |")
open(f'{out}/pick_list.md', 'w').write('\n'.join(md) + '\n')
tpl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'picklist_template.html')).read()
from html import escape as E
page = (tpl.replace('__DATA__', json.dumps(D, separators=(',', ':')).replace('</', '<\\/')).replace('__NP__', str(tot_l)).replace('__NU__', str(tot_u))
        .replace('__TITLE__', E(f'{date.split(",")[0]} Pick List')).replace('__SHOW__', E(title)).replace('__NPK__', str(npk)).replace('__KEY__', key))
open(f'{out}/pick_list.html', 'w').write(page)
print(f'{tot_l} products, {tot_u} units, {npk} packages -> {out} (key {key})')
