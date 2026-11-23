#!/usr/bin/env python3
"""usage: python3 pack_evidence.py <packing_list.html> <out dir> [--width 480] [--quality 80]
For publishing a list whose orders have frames (D04): packs each package's frames into one JPEG sheet
(evidence/p<package>.jpg: one row per order, up to three frames side by side at the given width each) and
writes <out dir>/packing_list.html whose evidence entries point at the sheets (sheet, idx, n, w, h, k).
Artifacts allow 255 files and 64 MB per version; 963 orders x 3 native frames (~320 MB) do not fit, 142
sheets at 480 px per frame (~45 MB) do. The Desktop copy keeps the native-size frames."""
import json, os, re, sys
from PIL import Image
src, out = sys.argv[1], sys.argv[2]
width = int(sys.argv[sys.argv.index('--width') + 1]) if '--width' in sys.argv else 480
quality = int(sys.argv[sys.argv.index('--quality') + 1]) if '--quality' in sys.argv else 80
html = open(src).read(); base = os.path.dirname(os.path.abspath(src))
m = re.search(r'const DATA=(\[.*?\]);\n', html, re.S); data = json.loads(m.group(1))
os.makedirs(os.path.join(out, 'evidence'), exist_ok=True); total = 0; nsheet = 0
ROW_H = None
for p in data:
    allitems = [it for it in p['items'] if it.get('evidence', {}).get('frames')]
    for part in range(0, len(allitems), 60):          # JPEG height limit is 65500 px: at most 60 rows per sheet
        items = allitems[part:part + 60]; rows = []
        for it in items:
            ims = []
            for f in it['evidence']['frames'][:3]:
                try: im = Image.open(os.path.join(base, f['file'])).convert('RGB')
                except Exception: continue
                ims.append(im.resize((width, round(im.height * width / im.width)), Image.LANCZOS))
            rows.append(ims)
        h = max((im.height for ims in rows for im in ims), default=width)
        sheet = Image.new('RGB', (width * 3, h * len(rows)), (0, 0, 0))
        for r, ims in enumerate(rows):
            for c, im in enumerate(ims): sheet.paste(im, (c * width, r * h))
        name = f"evidence/p{p['num']}{'_' + str(part // 60 + 1) if part else ''}.jpg"
        sheet.save(os.path.join(out, name), 'JPEG', quality=quality, optimize=True)
        total += os.path.getsize(os.path.join(out, name)); nsheet += 1
        for r, it in enumerate(items):
            ev = it['evidence']; offs = [f['off'] for f in ev['frames'][:3]]; ev.pop('frames', None)
            ev.update({'sheet': name, 'idx': r, 'n': len(items), 'w': width * 3, 'h': h, 'k': len(offs), 'offs': offs})
html2 = html[:m.start(1)] + json.dumps(data, separators=(',', ':')).replace('</', '<\\/') + html[m.end(1):]
open(os.path.join(out, 'packing_list.html'), 'w').write(html2)
print(f'{nsheet} sheets, {total / 1e6:.1f} MB, page {len(html2) / 1e6:.1f} MB -> {out}')
# Publish batches (Oct 9, 2026): one 58 MB `files` batch closed the socket; ~30 MB batches worked. files_1.json holds
# packing_slips.html (copied here) + sheets, every batch stays under BATCH_MB; publish them in order to the same URL.
BATCH_MB = 32
slips = os.path.join(base, 'packing_slips.html')
if os.path.exists(slips):
    import shutil; shutil.copy(slips, os.path.join(out, 'packing_slips.html'))
entries = (['packing_slips.html'] if os.path.exists(slips) else []) + sorted(os.listdir(os.path.join(out, 'evidence')))
entries = [e if e == 'packing_slips.html' else 'evidence/' + e for e in entries]
batches, cur, size = [], {}, 0
for e in entries:
    s = os.path.getsize(os.path.join(out, e))
    if cur and size + s > BATCH_MB * 1e6: batches.append(cur); cur, size = {}, 0
    cur[e] = e; size += s
if cur: batches.append(cur)
for k, b in enumerate(batches, 1):
    json.dump(b, open(os.path.join(out, f'files_{k}.json'), 'w'))
    print(f'  files_{k}.json: {len(b)} files, {sum(os.path.getsize(os.path.join(out, e)) for e in b) / 1e6:.1f} MB')
print(f'publish packing_list.html with root={out} and files=files_1.json, then files_2.json.. to the same URL (each under {BATCH_MB} MB)')
