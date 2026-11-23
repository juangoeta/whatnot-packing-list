"""pipeline/scripts/run.py build on a work/ folder written by the receiver from a fixture bundle plus a small
review (notes.tsv). No ffmpeg, no whisper: evidence frames simply do not exist for these rows."""
import collections, csv, json, os, re, sys
import pytest
from conftest import work_of

import run

CSV_HEADER = ['Change', 'Package', 'Buyer', 'Split shipment', 'Tracking #', 'Carrier', 'Item #', 'Listing', 'Order #', 'Sold price',
              'Sold at (EDT)', 'Replay position', 'What to pack', 'Needs your check', 'Note', 'Evidence']

def build(rx, b, folder, notes, out, monkeypatch, *extra):
    """process() the bundle, write notes.tsv ({row id: (product, flag)}), run `run.py build --out out`."""
    rx.process(b, lambda m: None)
    work = work_of(rx, folder)
    with open(os.path.join(work, 'notes.tsv'), 'w') as f:
        for r in [l.split('\t') for l in open(os.path.join(work, 'rows.tsv')) if l.strip()]:
            product, flag = notes.get(r[0], (f'Product for {r[4]}', ''))
            f.write(f'{r[0]}\t{product}\t{flag}\t\n')
    monkeypatch.setattr(sys, 'argv', ['run.py', '--work', work, 'build', '--out', out, *extra])
    run.main()
    return work

@pytest.fixture
def sep25_list(rx, sep25, tmp_path, monkeypatch, capsys):
    notes = {'0': ('Lip gloss, pink', 'shade: pink or coral, not readable'), '7': ('Mascara', 'qty: 1 or 2 in the bag'),
             '20': ('Blush palette', 'item: camera shows a brush'), '30': ('Perfume mini', 'giveaway'), '31': ('Face mask 2-pack', 'expiry: Dec 2026')}
    out = str(tmp_path / 'list'); work = build(rx, sep25, 'Sep 25', notes, out, monkeypatch)
    return work, out, capsys.readouterr().out

def test_build_packages_and_split_labels(sep25_list, sep25):
    work, out, printed = sep25_list
    pk = json.load(open(os.path.join(work, 'packages.json')))
    assert len(pk) == 41 and sum(len(p['items']) for p in pk) == 176
    assert [p['num'] for p in pk] == list(range(1, 42))
    # the buyer with five shipping labels gets LABEL 1 of 5 .. 5 of 5, in order of first sale; the two-label buyer 1 of 2, 2 of 2
    per = collections.Counter(s['buyer'] for s in sep25['shipments'])
    five = next(b for b, n in per.items() if n == 5); two = next(b for b, n in per.items() if n == 2)
    assert [p['split'] for p in pk if p['buyer'] == five] == ['1 of 5', '2 of 5', '3 of 5', '4 of 5', '5 of 5']
    assert [p['split'] for p in pk if p['buyer'] == two] == ['1 of 2', '2 of 2']
    assert all(p['split'] == '' for p in pk if p['buyer'] not in (five, two))
    firsts = [min(it['at'] for it in p['items']) for p in pk if p['buyer'] == five]
    assert firsts == sorted(firsts)
    # every package carries its tracking number and every order its item number
    assert all(p['tracking'].startswith('9400') and p['courier'] == 'USPS' for p in pk)
    assert all(it['itemNo'] is not None for p in pk for it in p['items'])
    assert collections.Counter(it['series'] for p in pk for it in p['items']) == {'Beauty pulls': 166, 'GIVY': 10}
    assert sum(1 for p in pk for it in p['items'] if it.get('giveaway')) == 10   # GIVY rows are marked for the "Hide giveaways" filter
    assert '41 packages, 176 items, 3 to revise, 2 notes' in printed and '2 split buyers' in printed

def test_build_csv_header_and_rows(sep25_list):
    work, out, _ = sep25_list
    rows = list(csv.reader(open(os.path.join(out, 'packing_list.csv'))))
    assert rows[0] == CSV_HEADER and len(rows) == 177
    by_order = {r[8]: r for r in rows[1:]}
    r = by_order['1000001']
    assert (r[6], r[7], r[9], r[12], r[13]) == ('162', 'Beauty pulls #162', '$3.00', 'Lip gloss, pink', 'shade: pink or coral, not readable')
    assert re.fullmatch(r'\d+:\d\d:\d\d', r[11]), 'replay position is h:mm:ss into the recording (D02)'
    assert re.fullmatch(r'\d{1,2}:\d\d:\d\d [AP]M', r[10]), 'sold-at time comes from the stream start'
    assert sum(1 for r in rows[1:] if r[13]) == 3                 # revise flags in "Needs your check"
    assert sum(1 for r in rows[1:] if r[14]) == 2                 # notes in "Note"
    assert sum(1 for r in rows[1:] if r[3].startswith('LABEL ')) == sum(1 for r in rows[1:] if r[2] in {r_[2] for r_ in rows[1:] if r_[3]})
    labels = {r[3] for r in rows[1:] if r[3]}
    assert labels == {f'LABEL {k} of 5' for k in range(1, 6)} | {'LABEL 1 of 2', 'LABEL 2 of 2'}

def test_build_html_counts(sep25_list):
    work, out, _ = sep25_list
    html = open(os.path.join(out, 'packing_list.html')).read()
    assert re.findall(r'__[A-Z]+__', html) == [], 'unfilled placeholder'
    assert '<b>3</b><span>need your check</span>' in html
    assert '<summary>3 orders to double-check before sealing</summary>' in html
    assert '<title>Sep 25 Packing List</title>' in html
    line = next(l for l in html.splitlines() if l.startswith('const DATA='))
    data = json.loads(line[len('const DATA='):].rstrip(';'))
    assert len(data) == 41 and sum(1 for p in data for it in p['items'] if it['flagLevel'] == 'revise') == 3
    assert os.path.exists(os.path.join(out, 'packing_list.md')) and os.path.exists(os.path.join(out, 'packing_slips.html'))
    slips = open(os.path.join(out, 'packing_slips.html')).read()
    assert slips.count('<article class="slip"') == 41 and 'Giveaway prize' in slips
    md = open(os.path.join(out, 'packing_list.md')).read()
    assert '41 packages, 176 items' in md and '3 orders need a quick check' in md and 'has 5 labels' in md

def test_build_snacks_fixed_title_listings(rx, snacks, tmp_path, monkeypatch, capsys):
    out = str(tmp_path / 'list')
    work = build(rx, snacks, 'Sep 24', {'0': ('Coconut snacks 12 pack', 'qty: 6 or 12 pack')}, out, monkeypatch)
    pk = json.load(open(os.path.join(work, 'packages.json')))
    assert len(pk) == 23 and sum(len(p['items']) for p in pk) == 128 and all(p['split'] == '' for p in pk)
    series = {it['series'] for p in pk for it in p['items']}
    assert len(series) > 1 and all(it['itemNo'] is not None for p in pk for it in p['items'])
    html = open(os.path.join(out, 'packing_list.html')).read()
    assert '<b>1</b><span>need your check</span>' in html and '<title>Sep 24 Packing List</title>' in html

def test_build_key_differs_for_two_shows_on_one_day(rx, snacks, tmp_path, monkeypatch):
    """The packed-checkbox localStorage key is mmdd for the day's first list and mmdd-<folder words> for a second one."""
    from test_receiver import second_show
    out1, out2 = str(tmp_path / 'l1'), str(tmp_path / 'l2')
    build(rx, snacks, 'Sep 24', {}, out1, monkeypatch)
    build(rx, second_show(snacks), 'Sep 24 Makeup Madness', {}, out2, monkeypatch)
    keys = [re.search(r"wn-packed-([\w-]+)", open(os.path.join(o, 'packing_list.html')).read()).group(1) for o in (out1, out2)]
    assert keys == ['0924', '0924-makeupmadness']
    assert run.Work(work_of(rx, 'Sep 24')).meta()[2] == '0924'
    os.rename(os.path.join(rx.A.out, 'Sep 24'), os.path.join(rx.A.out, 'Sep 24 - Maria'))
    open(os.path.join(work_of(rx, 'Sep 24 - Maria'), 'show_folder.txt'), 'w').write(os.path.join(rx.A.out, 'Sep 24 - Maria') + '\n')
    assert run.Work(work_of(rx, 'Sep 24 - Maria')).meta()[2] == '0924-maria'

def test_item_number():
    assert run.item_number('Beauty pulls #162') == (162, 'Beauty pulls')
    assert run.item_number('COCONUT SNACKS BEST BY SEP2026- 12 PACK #10') == (10, 'COCONUT SNACKS BEST BY SEP2026- 12 PACK')
    assert run.item_number('GIVY #3') == (3, 'GIVY')
    assert run.item_number('Mystery bag') == (None, 'Mystery bag')
    assert run.item_number('') == (None, '')

def test_parse_flag():
    assert run.parse_flag('shade: not readable') == ('shade', 'revise', 'not readable')
    assert run.parse_flag('giveaway') == ('giveaway', 'note', '')
    assert run.parse_flag('something odd') == ('item', 'revise', 'something odd')
    assert run.parse_flag('') is None

def fake_frames(work, row, n=2):
    """What `tiles` leaves for one row: frames/NNN.json (offsets, arg) and frames/NNN_k.jpg (a few bytes stand in for the JPEG)."""
    os.makedirs(os.path.join(work, 'frames'), exist_ok=True)
    for k in range(1, n + 1): open(os.path.join(work, 'frames', f'{row:03d}_{k}.jpg'), 'wb').write(b'\xff\xd8' + bytes([k]) * 64)
    json.dump({'offsets': [-3 + 3 * k for k in range(n)], 'arg': -3}, open(os.path.join(work, 'frames', f'{row:03d}.json'), 'w'))

def build_with_frames(rx, sep25, tmp_path, monkeypatch):
    rx.process(sep25, lambda m: None); work = work_of(rx, 'Sep 25'); fake_frames(work, 0)
    with open(os.path.join(work, 'notes.tsv'), 'w') as f:
        for r in [l.split('\t') for l in open(os.path.join(work, 'rows.tsv')) if l.strip()]: f.write(f'{r[0]}\tProduct for {r[4]}\t\t\n')
    out = str(tmp_path / 'list'); monkeypatch.setattr(sys, 'argv', ['run.py', '--work', work, 'build', '--out', out]); run.main()
    return work, out

def test_build_hard_links_evidence_frames(rx, sep25, tmp_path, monkeypatch):
    work, out = build_with_frames(rx, sep25, tmp_path, monkeypatch)
    src = os.path.join(work, 'frames', '000_1.jpg'); dest = os.path.join(out, 'evidence', '1000001_1.jpg')
    assert os.path.exists(dest) and os.path.samefile(src, dest) and os.stat(src).st_nlink == 2, 'a hard link, not a copy'
    assert os.path.samefile(os.path.join(work, 'frames', '000_2.jpg'), os.path.join(out, 'evidence', '1000001_2.jpg'))
    pk = json.load(open(os.path.join(work, 'packages.json')))
    ev = next(it for p in pk for it in p['items'] if it['order'] == '1000001')['evidence']
    assert [f['file'] for f in ev['frames']] == ['evidence/1000001_1.jpg', 'evidence/1000001_2.jpg']
    # a second build rebuilds the folder and links again; the source keeps a single extra link
    monkeypatch.setattr(sys, 'argv', ['run.py', '--work', work, 'build', '--out', out]); run.main()
    assert os.path.samefile(src, dest) and os.stat(src).st_nlink == 2
    # the frame survives the deletion of work/frames (Step 6 removes work/)
    os.remove(src); assert open(dest, 'rb').read()[:2] == b'\xff\xd8'

def test_build_copies_evidence_frames_when_linking_fails(rx, sep25, tmp_path, monkeypatch):
    def no_link(src, dst, *a, **k): raise OSError(18, 'Invalid cross-device link')
    monkeypatch.setattr(run.os, 'link', no_link)
    work, out = build_with_frames(rx, sep25, tmp_path, monkeypatch)
    src = os.path.join(work, 'frames', '000_1.jpg'); dest = os.path.join(out, 'evidence', '1000001_1.jpg')
    assert os.path.exists(dest) and not os.path.samefile(src, dest) and open(dest, 'rb').read() == open(src, 'rb').read()

def test_build_escapes_title_and_script_json(rx, sep25, tmp_path, monkeypatch):
    import evidence_card
    sep25['showTitle'] = 'Beauty </script><b>pulls</b> & more'
    out = str(tmp_path / 'list')
    work = build(rx, sep25, 'Sep 25', {'0': ('Lip gloss </script><img src=x onerror=alert(1)>', 'shade: </script> pink')}, out, monkeypatch)
    html = open(os.path.join(out, 'packing_list.html')).read()
    tpl = open(os.path.join(os.path.dirname(run.__file__), 'page_template.html')).read()
    assert html.count('</script>') == tpl.count('</script>'), 'data must not close a script block'
    assert 'Whatnot show "Beauty &lt;/script&gt;&lt;b&gt;pulls&lt;/b&gt; &amp; more"' in html
    line = next(l for l in html.splitlines() if l.startswith('const DATA='))
    data = json.loads(line[len('const DATA='):].rstrip(';'))
    it = next(it for p in data for it in p['items'] if it['order'] == '1000001')
    assert it['product'] == 'Lip gloss </script><img src=x onerror=alert(1)>' and it['flag'] == 'shade: </script> pink'
    d, meta, check = evidence_card.load_page(os.path.join(out, 'packing_list.html'))
    assert meta['show'] == 'Beauty </script><b>pulls</b> & more' and len(d) == 41 and check['orders'] == 176
