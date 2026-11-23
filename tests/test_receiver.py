"""receiver/receive.py: bundle checks, show folder naming, client credits, and process() up to the download."""
import json, os
from conftest import work_of, PLAYLIST

# ---------- completeness_problem ----------

def test_complete_bundle_passes(sep25, snacks):
    import receive
    assert receive.completeness_problem(sep25) is None
    assert receive.completeness_problem(snacks) is None

def test_missing_order_is_counted(sep25):
    import receive
    del sep25['items'][10]
    assert receive.completeness_problem(sep25) == '175 orders received but the packages hold 176'

def test_order_in_wrong_package_names_the_package(sep25):
    import receive
    # same total, but one order sits in another buyer's package: the short package is named
    a, b = sep25['items'][0], next(it for it in sep25['items'] if it['shipmentId'] != sep25['items'][0]['shipmentId'])
    short = a['buyer']; a['shipmentId'] = b['shipmentId']
    msg = receive.completeness_problem(sep25)
    assert msg.startswith(f'package for {short} has ') and msg.endswith(' orders')

def test_blank_receipt_timestamp_is_counted(sep25):
    import receive
    sep25['items'][5]['vttStart'] = None
    assert receive.completeness_problem(sep25) == '1 orders have no receipt timestamp'

def test_no_package_list(sep25):
    import receive
    sep25['shipments'] = []
    assert receive.completeness_problem(sep25) == 'no package list'

# ---------- validate (shape) ----------

def test_validate_accepts_real_bundles(sep25, snacks):
    import receive
    assert receive.validate(sep25) is None and receive.validate(snacks) is None

def test_validate_names_the_first_bad_field(sep25):
    import receive, copy
    def bad(f):
        b = copy.deepcopy(sep25); f(b); return receive.validate(b)
    assert receive.validate([]) == 'not an object'
    assert bad(lambda b: b.pop('showUrl')) == 'no showUrl'
    assert bad(lambda b: b.update(showUrl=5)) == 'no showUrl'
    assert bad(lambda b: b.update(showTitle=['x'])) == 'showTitle is not text'
    assert bad(lambda b: b.update(livestream='live')) == 'livestream is not an object'
    assert bad(lambda b: b['livestream'].update(id=[1])) == 'livestream id or startTime has the wrong type'
    assert bad(lambda b: b.update(items=[])) == 'no items'
    assert bad(lambda b: b.update(items={'a': 1})) == 'no items'
    assert bad(lambda b: b.update(shipments='abc')) == 'shipments is not a list'
    assert bad(lambda b: b['items'].__setitem__(2, 'x')) == 'item 2 is not an object'
    assert bad(lambda b: b['items'][0].pop('buyer')) == 'item 0 has no buyer'
    assert bad(lambda b: b['items'][0].update(buyer={'username': 'x'})) == 'item 0: buyer is dict'
    assert bad(lambda b: b['items'][1].pop('listing')) == 'item 1 has no listing'
    assert bad(lambda b: b['items'][1].update(listing=None)) is None        # a null listing is written as ''
    assert bad(lambda b: b['items'][1].pop('vttStart')) is None            # optional: completeness_problem reports it
    assert bad(lambda b: b['items'][1].update(vttStart='12:00')) == 'item 1: vttStart is str'
    assert bad(lambda b: b['items'][1].update(vttStart=True)) == 'item 1: vttStart is bool'
    assert bad(lambda b: b['items'][1].update(cents=None)) is None
    assert bad(lambda b: b['shipments'][0].pop('qty')) == 'package 0 has no qty'
    assert bad(lambda b: b['shipments'][0].update(qty='abc')) == 'package 0: qty is str'
    assert bad(lambda b: b['shipments'][0].update(buyer=None)) is None
    assert bad(lambda b: b['shipments'][3].pop('isFirst')) == 'package 3 has no isFirst'
    assert bad(lambda b: b['shipments'][3].update(isFirst=1)) == 'package 3: isFirst is int'

def test_post_refuses_a_bad_bundle_before_charging(rx, sep25):
    """A malformed bundle gets a 400 with the field name; no credit is charged, nothing is fetched, nothing lands in the inbox."""
    import threading, urllib.request, urllib.error, copy
    from http.server import ThreadingHTTPServer
    ledger(rx, 2)
    srv = ThreadingHTTPServer(('127.0.0.1', 0), rx.H); threading.Thread(target=srv.serve_forever, daemon=True).start()
    def post(b, code='abc'):
        req = urllib.request.Request(f'http://127.0.0.1:{srv.server_port}/show', json.dumps(b).encode(), {'Content-Type': 'application/json', 'X-Packing-Token': code})
        try: r = urllib.request.urlopen(req, timeout=5); return r.status, json.load(r)
        except urllib.error.HTTPError as e: return e.code, json.load(e)
    try:
        b = copy.deepcopy(sep25); del b['shipments'][0]['qty']
        assert post(b) == (400, {'ok': False, 'message': 'bad bundle: package 0 has no qty'})
        b = copy.deepcopy(sep25); b['items'][0]['buyer'] = None
        assert post(b) == (400, {'ok': False, 'message': 'bad bundle: item 0: buyer is NoneType'})
        assert post({'showUrl': PLAYLIST, 'items': ['x']}) == (400, {'ok': False, 'message': 'bad bundle: item 0 is not an object'})
        b = copy.deepcopy(sep25); b['showUrl'] = 'http://127.0.0.1:8789/admin/api/state'
        assert post(b)[0] == 400
        assert post(sep25, 'nope')[0] == 403
        assert rx.load_ledger()['clients']['abc']['credits'] == 2 and rx.calls == [] and not os.path.exists(rx.INBOX)
    finally: srv.shutdown(); srv.server_close()

# ---------- client IP (rate limit key and log) ----------

def test_x_forwarded_for_is_trusted_only_from_the_local_proxy(rx):
    def ip(peer, xff=None):
        h = rx.H.__new__(rx.H); h.client_address = (peer, 12345); h.headers = {'X-Forwarded-For': xff} if xff else {}
        return h.ip()
    assert ip('127.0.0.1', '100.64.0.9') == '100.64.0.9'                       # Funnel / tailscale serve on this Mac
    assert ip('127.0.0.1', 'made-up, 100.64.0.9') == '100.64.0.9'              # last entry is the proxy's
    assert ip('::1', '100.64.0.9') == '100.64.0.9'
    assert ip('127.0.0.1') == '127.0.0.1'
    assert ip('192.168.1.20', '1.2.3.4') == '192.168.1.20'                      # direct LAN peer: header ignored
    assert ip('100.100.1.2', '1.2.3.4, 5.6.7.8') == '100.100.1.2'               # direct tailnet peer: header ignored

# ---------- fetch allow-list and caps ----------

def test_bad_url_allows_only_https_on_whatnot_hosts(rx):
    import io
    assert rx.bad_url('https://api.whatnot.com/api/v2/video/presigned/index.m3u8?x=1') is None
    assert rx.bad_url('https://s3ntry.whatnot.com/whatnot-livestream-videos-prod/a/0.ts') is None
    assert rx.bad_url('https://whatnot.com/x') is None
    for u in ('http://api.whatnot.com/x', 'file:///etc/hosts', 'ftp://api.whatnot.com/x', 'https://127.0.0.1:8789/admin',
              'https://evil.com/whatnot.com/x', 'https://whatnot.com.evil.com/x', 'https://api.whatnot.com@evil.com/x', '', None):
        assert rx.bad_url(u), u

def test_get_refuses_bad_url_before_opening_and_caps_bytes(monkeypatch):
    import receive, pytest
    opened, size = [], [0]
    class R:
        def read(self, n=-1): return b'x' * min(n, size[0])     # a body of size[0] bytes, read in one call
    monkeypatch.setattr(receive.urllib.request, 'urlopen', lambda req, timeout=0: opened.append(req.full_url) or R())
    with pytest.raises(ValueError, match='not https'): receive.get('file:///etc/hosts')
    with pytest.raises(ValueError, match='not https'): receive.get('http://api.whatnot.com/x')
    with pytest.raises(ValueError, match='host not allowed'): receive.get('https://10.0.0.5/x')
    assert opened == []
    size[0] = 10; assert receive.get('https://api.whatnot.com/small.ts', limit=10) == b'x' * 10
    size[0] = 11
    with pytest.raises(ValueError, match='over 0 MB'): receive.get('https://api.whatnot.com/big.ts', limit=10)
    size[0] = (16 << 20) + 1
    with pytest.raises(ValueError, match='over 16 MB'): receive.get('https://api.whatnot.com/big.ts')
    assert opened == ['https://api.whatnot.com/small.ts', 'https://api.whatnot.com/big.ts', 'https://api.whatnot.com/big.ts']

def test_playlist_with_a_foreign_or_local_line_is_refused(rx, sep25, monkeypatch):
    import pytest
    from conftest import M3U8
    bad = M3U8.replace('https://example.invalid/seg/1.ts', 'file:///home/user/.ssh/id_rsa')
    monkeypatch.setattr(rx, 'get', lambda url, timeout=60: bad.encode() if url.endswith('.m3u8') else b'\x47' * 188)
    with pytest.raises(ValueError, match='not https'): rx.process(sep25, lambda m: None)
    work = work_of(rx, 'Sep 25')
    assert not os.path.exists(os.path.join(work, 'seg_urls.txt')) and not os.listdir(os.path.join(work, 'seg'))
    bad = M3U8.replace('https://example.invalid/seg/1.ts', 'https://127.0.0.1:8789/admin/api/state')
    monkeypatch.setattr(rx, 'get', lambda url, timeout=60: bad.encode() if url.endswith('.m3u8') else b'\x47' * 188)
    with pytest.raises(ValueError, match='host not allowed: 127.0.0.1'): rx.process(sep25, lambda m: None)

def test_playlist_over_the_segment_cap_is_refused(rx, sep25, monkeypatch):
    import pytest
    monkeypatch.setattr(rx, 'MAX_SEGS', 2)
    with pytest.raises(ValueError, match='3 segments'): rx.process(sep25, lambda m: None)
    assert not os.listdir(os.path.join(work_of(rx, 'Sep 25'), 'seg'))

def test_download_stops_at_the_byte_cap(rx, sep25, monkeypatch):
    import pytest
    monkeypatch.setattr(rx, 'MAX_BYTES', 200)       # three 188-byte segments (16 threads, so all may land): the cap trips
    with pytest.raises(ValueError, match='download stopped'): rx.process(sep25, lambda m: None)
    assert not os.path.exists(os.path.join(work_of(rx, 'Sep 25'), 'DOWNLOAD_DONE'))   # no prep on a capped show

# ---------- show_dir ----------

def test_show_dir_first_show(rx, sep25):
    assert rx.show_dir(sep25) == os.path.join(rx.A.out, 'Sep 25')

def test_show_dir_is_named_from_the_stream_start_not_the_send(rx, snacks):
    # the snacks bundle was sent on Sep 25; the show ran on Sep 24 (New York time)
    assert rx.show_dir(snacks) == os.path.join(rx.A.out, 'Sep 24')

def second_show(b, title='MAKEUP MADNESS: liquidate everything!!', id='11111111-2222-3333-4444-555555555555'):
    """Another show on the same day: a copy of the bundle with its own show id and title, same startTime."""
    import copy
    s = copy.deepcopy(b); s['livestream'] = dict(s['livestream'], id=id, title=title); s['showTitle'] = title
    return s

def test_show_dir_second_show_same_day_is_named_from_its_title(rx, snacks):
    rx.process(snacks, lambda m: None)                         # first show of the day: Sep 24/
    assert rx.show_dir(snacks) == os.path.join(rx.A.out, 'Sep 24')
    other = second_show(snacks)
    assert rx.show_dir(other) == os.path.join(rx.A.out, 'Sep 24 Makeup Madness')
    rx.process(other, lambda m: None)
    assert sorted(os.listdir(rx.A.out)) == ['Sep 24', 'Sep 24 Makeup Madness']
    assert json.load(open(os.path.join(rx.A.out, 'Sep 24', 'work', 'livestream.json')))['id'] == snacks['livestream']['id']
    assert json.load(open(os.path.join(rx.A.out, 'Sep 24 Makeup Madness', 'work', 'livestream.json')))['id'] == other['livestream']['id']
    # re-ingesting the second bundle updates its folder rather than creating a third
    rx.process(other, lambda m: None)
    assert sorted(os.listdir(rx.A.out)) == ['Sep 24', 'Sep 24 Makeup Madness']
    # the old '{}' placeholder trick, or a title with no words, still gives a folder of its own
    assert rx.show_dir(second_show(snacks, '!!!', id='third-show')) == os.path.join(rx.A.out, 'Sep 24 show2')
    # a third show with the same two title words gets its own folder too, never the second show's
    third = second_show(snacks, 'MAKEUP MADNESS part two', id='33333333-2222-3333-4444-555555555555')
    assert rx.show_dir(third) == os.path.join(rx.A.out, 'Sep 24 Makeup Madness 3')
    rx.process(third, lambda m: None)
    assert sorted(os.listdir(rx.A.out)) == ['Sep 24', 'Sep 24 Makeup Madness', 'Sep 24 Makeup Madness 3']
    assert json.load(open(os.path.join(rx.A.out, 'Sep 24 Makeup Madness', 'work', 'livestream.json')))['id'] == other['livestream']['id']
    assert rx.show_dir(third) == os.path.join(rx.A.out, 'Sep 24 Makeup Madness 3')   # and a resend finds it by id
    assert rx.show_dir(second_show(snacks, 'MAKEUP MADNESS part three', id='fourth')) == os.path.join(rx.A.out, 'Sep 24 Makeup Madness 4')

def test_show_dir_resend_finds_the_folder_by_show_id_after_a_rename(rx, snacks):
    rx.process(snacks, lambda m: None)
    os.rename(os.path.join(rx.A.out, 'Sep 24'), os.path.join(rx.A.out, 'Sep 24 snacks'))     # renamed by hand
    assert rx.show_dir(snacks) == os.path.join(rx.A.out, 'Sep 24 snacks')
    rx.process(snacks, lambda m: None)
    assert sorted(os.listdir(rx.A.out)) == ['Sep 24 snacks'] and os.path.exists(os.path.join(rx.A.out, 'Sep 24 snacks', 'work', 'rows.prev.tsv'))
    assert rx.show_dir(second_show(snacks)) == os.path.join(rx.A.out, 'Sep 24')   # the day's plain folder is free again

def test_show_dir_client_second_show_same_day(rx, snacks):
    rx.process(snacks, lambda m: None, 'Maria')
    assert rx.show_dir(snacks, 'Maria') == os.path.join(rx.A.out, 'Sep 24 - Maria')
    assert rx.show_dir(second_show(snacks), 'Maria') == os.path.join(rx.A.out, 'Sep 24 - Maria Makeup Madness')
    assert rx.show_dir(second_show(snacks)) == os.path.join(rx.A.out, 'Sep 24')   # the master's day folder is separate

def test_short_title(rx, snacks, sep25):
    assert rx.short_title(snacks) == 'Snacks Deals' and rx.short_title(sep25) == 'Beauty Pulls'
    assert rx.short_title({'showTitle': ' sun-glasses  2 for $10 '}) == 'Sun Glasses' and rx.short_title({'showTitle': ''}) == ''

def test_show_dir_client_and_reuse(rx, sep25, tmp_path):
    assert rx.show_dir(sep25, 'Maria') == os.path.join(rx.A.out, 'Sep 25 - Maria')
    reuse = tmp_path / 'somewhere'; reuse.mkdir()
    assert rx.show_dir(sep25, 'Maria', str(reuse)) == str(reuse)
    assert rx.show_dir(sep25, 'Maria', str(tmp_path / 'missing')) == os.path.join(rx.A.out, 'Sep 25 - Maria')
    assert rx.show_dir(dict(sep25, statusFilter='all')) == os.path.join(rx.A.out, 'Sep 25 (all orders)')

# ---------- master code ----------

def test_load_token_reads_the_first_line_and_warns_on_loose_permissions(rx, tmp_path):
    f = tmp_path / 'ACCESS_CODE.txt'; warned = []
    assert rx.load_token(str(f)) == ''                                   # missing file: no master code, no crash
    f.write_text('  abc123  \nsecond line ignored\n'); f.chmod(0o600)
    assert rx.load_token(str(f), warned.append) == 'abc123' and warned == []
    f.chmod(0o644)
    assert rx.load_token(str(f), warned.append) == 'abc123' and len(warned) == 1 and 'chmod 600' in warned[0] and 'abc123' not in warned[0]
    f.write_text('\n'); f.chmod(0o600)
    assert rx.load_token(str(f)) == ''
    assert rx.A.token_file.endswith(os.path.join('receiver', 'ACCESS_CODE.txt'))

# ---------- charge ----------

def ledger(rx, credits):
    rx.save_ledger({'clients': {'abc': {'name': 'Maria', 'credits': credits, 'used': 0, 'active': True, 'shows': {}}}})

def test_charge_new_show_costs_one_credit_and_resend_is_free(rx, sep25):
    ledger(rx, 2)
    ok, msg, folder = rx.charge('abc', 'Maria', sep25)
    assert (ok, folder) == (True, None) and msg == '1 show left on your plan.'
    c = rx.load_ledger()['clients']['abc']
    assert (c['credits'], c['used']) == (1, 1) and list(c['shows']) == [sep25['livestream']['id']]
    rx.remember_folder('abc', sep25, '/shows/Sep 25 - Maria')
    ok, msg, folder = rx.charge('abc', 'Maria', sep25)
    assert ok and msg.startswith('This show was already received; updated with 176 orders.') and folder == '/shows/Sep 25 - Maria'
    c = rx.load_ledger()['clients']['abc']
    assert (c['credits'], c['used'], c['shows'][sep25['livestream']['id']]['sends']) == (1, 1, 2)

def test_charge_refuses_without_credits(rx, sep25):
    ledger(rx, 0)
    ok, msg, folder = rx.charge('abc', 'Maria', sep25)
    assert (ok, folder) == (False, None) and 'no shows left' in msg
    assert rx.load_ledger()['clients']['abc']['used'] == 0

def test_master_is_unlimited(rx, sep25):
    assert rx.charge('master-code', 'master', sep25) == (True, '', None)
    assert rx.client_for('master-code') == ('master', None)

def test_client_for(rx):
    ledger(rx, 1)
    assert rx.client_for('abc')[0] == 'Maria'
    assert rx.client_for('nope') == (None, None)
    d = rx.load_ledger(); d['clients']['abc']['active'] = False; rx.save_ledger(d)
    assert rx.client_for('abc') == (None, None)

# ---------- process ----------

def read_tsv(p): return [l.rstrip('\n').split('\t') for l in open(p) if l.strip()]

def test_process_writes_work_files_and_downloads_segments(rx, sep25):
    log = []
    rx.process(sep25, log.append)
    work = work_of(rx, 'Sep 25')
    assert os.path.isdir(work) and os.path.realpath(work).startswith(os.path.realpath(rx.A.work_root))
    assert open(os.path.join(work, 'show_folder.txt')).read().strip() == os.path.join(rx.A.out, 'Sep 25')
    for f in ('show.json', 'rows.tsv', 'ship.tsv', 'livestream.json', 'segtimes.tsv', 'seg_urls.txt', 'stream_start.txt', 'DOWNLOAD_DONE'):
        assert os.path.exists(os.path.join(work, f)), f
    # one copy of the bundle in the inbox (the temp one, never the real one)
    inbox = os.listdir(rx.INBOX); assert len(inbox) == 1 and inbox[0].endswith('-master.json')
    rows = read_tsv(os.path.join(work, 'rows.tsv'))
    assert len(rows) == 176 and rows[0][:5] == ['0', '900001', 'buyer01', '1000001', 'Beauty pulls #162'] and rows[0][6] == '5967'
    ship = read_tsv(os.path.join(work, 'ship.tsv'))
    assert len(ship) == 41 and ship[0] == ['900001', 'buyer01', '9400000000000000000001', 'USPS', 'usps_ground_advantage', '1', 'false']
    # segtimes: cumulative start times from the playlist's EXTINF durations
    assert read_tsv(os.path.join(work, 'segtimes.tsv')) == [['0', '0.000', '2.0'], ['1', '2.000', '2.5'], ['2', '4.500', '3.0']]
    assert open(os.path.join(work, 'seg_urls.txt')).read().split() == [f'https://example.invalid/seg/{n}.ts' for n in range(3)]
    assert open(os.path.join(work, 'stream_start.txt')).read().strip() == '2026-09-25T22:08:21.000Z'
    assert sorted(os.listdir(os.path.join(work, 'seg'))) == ['0.ts', '1.ts', '2.ts']
    assert rx.calls[0] == PLAYLIST and len(rx.calls) == 4
    assert open(os.path.join(work, 'DOWNLOAD_DONE')).read() == '0 missing\n'
    assert any(m.startswith('playlist: 3 segments') for m in log) and any(m.startswith('download finished: 3/3') for m in log)
    assert json.load(open(os.path.join(work, 'livestream.json')))['id'] == sep25['livestream']['id']

def test_resend_keeps_row_ids_and_writes_the_diff(rx, sep25):
    import copy
    rx.process(sep25, lambda m: None)
    work = work_of(rx, 'Sep 25'); n_calls = len(rx.calls)
    again = copy.deepcopy(sep25)
    removed = again['items'].pop(3)              # one order cancelled
    again['items'][0]['shipmentId'] = again['items'][1]['shipmentId']   # one order moved to another package
    again['items'].append(dict(again['items'][-1], orderNo='1999999'))   # one new order
    log = []
    rx.process(again, log.append, reuse=os.path.join(rx.A.out, 'Sep 25'))
    assert len(rx.calls) == n_calls, 'a resend of a downloaded show must not fetch again'
    rows = read_tsv(os.path.join(work, 'rows.tsv')); prev = read_tsv(os.path.join(work, 'rows.prev.tsv'))
    assert len(prev) == 176 and len(rows) == 176
    ids = {r[3]: r[0] for r in rows}
    assert removed['orderNo'] not in ids and ids['1000005'] == '4' and ids['1999999'] == '176'   # kept their index; the new one got the next free
    d = json.load(open(os.path.join(work, 'diff.json')))
    assert (d['prev'], d['now']) == (176, 176)
    assert [e['order'] for e in d['added']] == ['1999999'] and [e['order'] for e in d['removed']] == [removed['orderNo']]
    assert [e['order'] for e in d['moved']] == ['1000001'] and d['tracking'] == 0
    assert rx.diff_summary(d) == '1 new, 1 removed, 1 moved'
    assert any('1 new, 1 removed, 1 moved' in m for m in log)
    assert not os.path.exists(os.path.join(work, 'notes.tsv'))   # no review yet, so no rebuild was attempted

def test_process_uses_the_playlist_the_handler_fetched(rx, sep25):
    from conftest import M3U8
    log = []
    rx.process(sep25, log.append, playlist=M3U8.encode())
    work = work_of(rx, 'Sep 25')
    assert rx.calls == [f'https://example.invalid/seg/{n}.ts' for n in range(3)], 'the playlist is not fetched a second time'
    assert read_tsv(os.path.join(work, 'segtimes.tsv')) == [['0', '0.000', '2.0'], ['1', '2.000', '2.5'], ['2', '4.500', '3.0']]
    assert any(m.startswith('playlist: 3 segments') for m in log) and open(os.path.join(work, 'DOWNLOAD_DONE')).read() == '0 missing\n'
def test_crash_while_writing_rows_keeps_the_old_rows_tsv(rx, sep25):
    import copy, pytest
    rx.process(sep25, lambda m: None)
    work = work_of(rx, 'Sep 25')
    broken = copy.deepcopy(sep25); del broken['items'][50]['shipmentId']    # a hand-built bundle with a key missing
    with pytest.raises(KeyError): rx.process(broken, lambda m: None)
    assert len(read_tsv(os.path.join(work, 'rows.tsv'))) == 176 and not os.path.exists(os.path.join(work, 'rows.prev.tsv'))
    again = copy.deepcopy(sep25); again['items'].pop(3)
    rx.process(again, lambda m: None)                                       # the next resend still sees the old ids
    assert {r[3]: r[0] for r in read_tsv(os.path.join(work, 'rows.tsv'))}['1000005'] == '4'

def slow_playlist(rx, monkeypatch, delay=0.1):
    import time
    real = rx.get
    def get(url, timeout=60):
        if url.endswith('.m3u8'): time.sleep(delay)
        return real(url, timeout)
    monkeypatch.setattr(rx, 'get', get)

def test_two_sends_of_one_show_at_once_download_once(rx, sep25, monkeypatch):
    import copy, threading
    slow_playlist(rx, monkeypatch)
    ts = [threading.Thread(target=rx.process, args=(copy.deepcopy(sep25), lambda m: None)) for _ in range(2)]
    for t in ts: t.start()
    for t in ts: t.join()
    work = work_of(rx, 'Sep 25')
    assert len(rx.calls) == 4, 'the second send must wait and find the download done'
    assert len(read_tsv(os.path.join(work, 'rows.tsv'))) == 176 and len(read_tsv(os.path.join(work, 'rows.prev.tsv'))) == 176
    assert os.listdir(rx.A.out) == ['Sep 25'] and not os.path.exists(os.path.join(work, 'rows.tsv.tmp'))

def test_two_shows_sent_at_once_get_their_own_folders(rx, snacks, monkeypatch):
    import threading
    slow_playlist(rx, monkeypatch)
    ts = [threading.Thread(target=rx.process, args=(b, lambda m: None)) for b in (snacks, second_show(snacks))]
    for t in ts: t.start()
    for t in ts: t.join()
    names = sorted(os.listdir(rx.A.out))          # whichever show claimed 'Sep 24' first, the other got its titled folder
    assert len(names) == 2 and names[0] == 'Sep 24' and names[1] in ('Sep 24 Makeup Madness', 'Sep 24 Snacks Deals')
    ids = {rx.live_id(json.load(open(os.path.join(rx.A.out, n, 'work', 'show.json')))) for n in names}
    assert ids == {snacks['livestream']['id'], '11111111-2222-3333-4444-555555555555'}

def test_resend_after_cleanup_does_not_download_again(rx, sep25, monkeypatch):
    import copy, shutil
    monkeypatch.setattr(rx, 'notify', lambda *a, **k: None)
    rx.process(sep25, lambda m: None)
    folder = os.path.join(rx.A.out, 'Sep 25'); work = work_of(rx, 'Sep 25'); n_calls = len(rx.calls)
    open(os.path.join(folder, 'packing_list.html'), 'w').write('<html>')
    shutil.rmtree(work); os.remove(os.path.join(folder, 'work'))          # SKILL step 6: work/ and the scratch are gone
    again = copy.deepcopy(sep25); again['shipments'][0]['tracking'] = '9400999999999999999999'
    log = []
    rx.process(again, log.append)
    assert len(rx.calls) == n_calls, 'a delivered show must not be downloaded again'
    work = work_of(rx, 'Sep 25')
    assert read_tsv(os.path.join(work, 'ship.tsv'))[0][2] == '9400999999999999999999' and len(read_tsv(os.path.join(work, 'rows.tsv'))) == 176
    assert not os.path.exists(os.path.join(work, 'DOWNLOAD_DONE')) and os.listdir(os.path.join(work, 'seg')) == []
    assert any('NO re-download' in m for m in log)
    rx.process(again, log.append)                                            # and the next resend lands in the same folder, still no download
    assert len(rx.calls) == n_calls and os.listdir(rx.A.out) == ['Sep 25']

# ---------- credits and the download ----------

def test_credit_refunded_when_nothing_downloads(rx, sep25, monkeypatch):
    monkeypatch.setattr(rx, 'notify', lambda *a, **k: None)
    ledger(rx, 1); real = rx.get
    def forbidden(url, timeout=60):
        if url.endswith('.ts'): raise OSError('HTTP Error 403')   # segment signatures expired before the playlist link
        return real(url, timeout)
    monkeypatch.setattr(rx, 'get', forbidden)
    assert rx.charge('abc', 'Maria', sep25)[0] and rx.load_ledger()['clients']['abc']['credits'] == 0
    log = []
    rx.process(sep25, log.append, 'Maria', 'abc')
    c = rx.load_ledger()['clients']['abc']
    assert (c['credits'], c['used'], c['shows']) == (1, 0, {}) and any('refunded' in m for m in log)
    work = work_of(rx, 'Sep 25 - Maria')
    assert not os.path.exists(os.path.join(work, 'DOWNLOAD_DONE')) and os.path.exists(os.path.join(work, 'rows.tsv'))
    monkeypatch.setattr(rx, 'get', real)                      # the next Send, with a fresh link, is charged once and downloads
    assert rx.charge('abc', 'Maria', sep25) == (True, '0 shows left on your plan.', None)
    rx.process(sep25, log.append, 'Maria', 'abc')
    c = rx.load_ledger()['clients']['abc']
    assert (c['credits'], c['used'], list(c['shows'])) == (0, 1, [sep25['livestream']['id']])
    assert open(os.path.join(work, 'DOWNLOAD_DONE')).read() == '0 missing\n' and os.listdir(rx.A.out) == ['Sep 25 - Maria']

def test_one_missing_segment_keeps_the_charge(rx, sep25, monkeypatch):
    ledger(rx, 1); real = rx.get
    def flaky(url, timeout=60):
        if url.endswith('/1.ts'): raise OSError('HTTP Error 403')
        return real(url, timeout)
    monkeypatch.setattr(rx, 'get', flaky)
    rx.charge('abc', 'Maria', sep25); rx.process(sep25, lambda m: None, 'Maria', 'abc')
    assert open(os.path.join(work_of(rx, 'Sep 25 - Maria'), 'DOWNLOAD_DONE')).read() == '1 missing\n'
    assert rx.load_ledger()['clients']['abc']['credits'] == 0

# ---------- the HTTP handler ----------

def serve(rx):
    """rx.H on a free localhost port, in a daemon thread. Call .shutdown() when done."""
    from http.server import ThreadingHTTPServer
    import threading
    rx.RATE.clear()
    srv = ThreadingHTTPServer(('127.0.0.1', 0), rx.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

def post(srv, b, code, path='/show'):
    import urllib.request, urllib.error
    req = urllib.request.Request(f'http://127.0.0.1:{srv.server_port}{path}', data=json.dumps(b).encode(), method='POST',
                                 headers={'Content-Type': 'application/json', 'X-Packing-Token': code})
    try:
        with urllib.request.urlopen(req, timeout=5) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

def wait_for(cond, secs=3.0):
    import time
    t0 = time.time()
    while not cond() and time.time() - t0 < secs: time.sleep(0.02)
    return cond()

def test_credit_refunded_when_processing_crashes(rx, sep25, monkeypatch):
    monkeypatch.setattr(rx, 'notify', lambda *a, **k: None)
    ledger(rx, 1); srv = serve(rx)
    real_work_dir = rx.work_dir
    def full_disk(base, log): raise OSError(28, 'No space left on device')   # a valid bundle; process() dies in its thread
    monkeypatch.setattr(rx, 'work_dir', full_disk)
    try:
        status, r = post(srv, sep25, 'abc')
        assert status == 200 and r['message'].endswith('0 shows left on your plan.')
        assert wait_for(lambda: rx.load_ledger()['clients']['abc']['credits'] == 1), 'the credit must come back after the crash'
        c = rx.load_ledger()['clients']['abc']
        assert (c['used'], c['shows']) == (0, {})
        monkeypatch.setattr(rx, 'work_dir', real_work_dir)
        status, r = post(srv, sep25, 'abc')                                  # a good send is then charged normally
        assert status == 200 and wait_for(lambda: os.path.exists(os.path.join(work_of(rx, 'Sep 25 - Maria'), 'DOWNLOAD_DONE')))
        assert rx.load_ledger()['clients']['abc']['credits'] == 0
        assert post(srv, sep25, 'nope')[0] == 403
    finally: srv.shutdown()

# ---------- run_prep ----------

def fake_prep(rx, monkeypatch, resend_rows=()):
    """Stand-in for `run.py prep`: writes a transcript and a tile for every row in rows.tsv as it is when the call starts.
    On the first call it then appends `resend_rows` to rows.tsv, like a resend landing while prep runs."""
    calls = []
    def run(cmd, **kw):
        work = cmd[cmd.index('--work') + 1]; calls.append(cmd[-1])
        rows = read_tsv(os.path.join(work, 'rows.tsv'))
        for d in ('wav', 'tiles'): os.makedirs(os.path.join(work, d), exist_ok=True)
        for r in rows:
            open(os.path.join(work, 'wav', f'{int(r[0]):03d}.wav.json'), 'w').write('{}'); open(os.path.join(work, 'tiles', f'{int(r[0]):03d}.jpg'), 'w').write('x')
        if len(calls) == 1 and resend_rows:
            with open(os.path.join(work, 'rows.tsv'), 'a') as f:
                for r in resend_rows: f.write('\t'.join(r) + '\n')
        class R: returncode = 0
        return R()
    monkeypatch.setattr(rx.subprocess, 'run', run); monkeypatch.setattr(rx, 'notify', lambda *a, **k: None)
    return calls

def test_run_prep_preps_rows_a_resend_added_meanwhile(rx, sep25, monkeypatch):
    rx.process(sep25, lambda m: None); work = work_of(rx, 'Sep 25')
    calls = fake_prep(rx, monkeypatch, [['176', '900001', 'buyer01', '1999999', 'Late order', '500', '6000', '']])
    log = []
    rx.run_prep(work, log.append)
    assert calls == ['prep', 'prep'] and any('arrived in a resend while prep ran; prepping them (rows 176)' in m for m in log)
    assert 'tiles 177' in open(os.path.join(work, 'PREP_DONE')).read()

def test_run_prep_does_not_rerun_for_rows_that_have_no_tile(rx, sep25, monkeypatch):
    rx.process(sep25, lambda m: None); work = work_of(rx, 'Sep 25')
    calls = fake_prep(rx, monkeypatch)
    real = rx.subprocess.run
    def gap(cmd, **kw):   # a row inside a stream dropout: prep leaves it with no transcript and no tile, and that is final
        r = real(cmd, **kw); os.remove(os.path.join(work, 'tiles', '010.jpg')); os.remove(os.path.join(work, 'wav', '010.wav.json')); return r
    monkeypatch.setattr(rx.subprocess, 'run', gap)
    log = []
    rx.run_prep(work, log.append)
    assert calls == ['prep'] and os.path.exists(os.path.join(work, 'PREP_DONE')) and not any('still' in m or 'resend' in m for m in log)

def test_run_prep_runs_once_when_nothing_changed(rx, sep25, monkeypatch):
    rx.process(sep25, lambda m: None); work = work_of(rx, 'Sep 25')
    calls = fake_prep(rx, monkeypatch)
    rx.run_prep(work, lambda m: None)
    assert calls == ['prep'] and os.path.exists(os.path.join(work, 'PREP_DONE'))

def test_resend_after_prep_says_which_rows_have_no_audio(rx, sep25, monkeypatch):
    import copy
    rx.process(sep25, lambda m: None); work = work_of(rx, 'Sep 25')
    fake_prep(rx, monkeypatch); rx.run_prep(work, lambda m: None)
    again = copy.deepcopy(sep25); again['items'].append(dict(again['items'][-1], orderNo='1999999'))
    log = []
    rx.process(again, log.append)
    assert any('1 order(s) in the resend have no transcript or tile' in m and '--rows 176' in m for m in log)

# ---------- show_key ----------

def page_url(live, status='unfulfilled'):
    """A Shipments page address like the one collect.js records: filters = base64 JSON, URL-encoded twice."""
    import base64, urllib.parse
    f = base64.b64encode(json.dumps({'salesChannel': {'type': 'single', 'value': live}, 'status': {'type': 'single', 'value': status}}).encode()).decode()
    return 'https://www.whatnot.com/dashboard/shipments?filters=' + urllib.parse.quote(urllib.parse.quote(f, safe=''), safe='')

def no_live(b, live='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'):
    import copy
    n = copy.deepcopy(b); n['livestream'] = None; n['pageUrl'] = page_url(live)
    return n

def test_show_key_prefers_the_livestream_then_the_page_filter(rx, sep25):
    assert rx.show_key(sep25) == sep25['livestream']['id']
    b = no_live(sep25)
    assert rx.show_key(b) == 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
    b['pageUrl'] = b['pageUrl'].replace('%25', '%')                   # encoded once is fine too
    assert rx.show_key(b) == 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
    assert rx.show_key(dict(b, pageUrl='https://www.whatnot.com/dashboard/shipments')) == ''
    assert rx.show_key(dict(b, pageUrl='https://www.whatnot.com/dashboard/shipments?filters=%%%')) == ''
    assert rx.show_key({'showUrl': 'https://x/y.m3u8'}) == ''

def test_resend_without_livestream_is_free_and_lands_in_the_same_folder(rx, sep25):
    ledger(rx, 2); b = no_live(sep25)
    assert rx.charge('abc', 'Maria', b) == (True, '1 show left on your plan.', None)
    rx.process(b, lambda m: None, 'Maria', 'abc')
    folder = rx.show_dir(b, 'Maria'); work = work_of(rx, os.path.basename(folder))
    again = dict(no_live(sep25), showUrl='https://example.invalid/other-signed-link/index.m3u8')   # a new signed link on every page load
    ok, msg, hint = rx.charge('abc', 'Maria', again)
    assert ok and msg.startswith('This show was already received') and hint == folder
    rx.process(again, lambda m: None, 'Maria', 'abc', hint)
    assert rx.load_ledger()['clients']['abc']['credits'] == 1 and os.listdir(rx.A.out) == [os.path.basename(folder)]
    assert os.path.exists(os.path.join(work, 'rows.prev.tsv')) and rx.show_dir(again, 'Maria') == folder   # found by the page's show id, no hint needed

def test_client_bundle_without_any_show_id_is_refused(rx, sep25):
    ledger(rx, 1); srv = serve(rx)
    try:
        b = dict(no_live(sep25), pageUrl='https://www.whatnot.com/dashboard/shipments')
        status, r = post(srv, b, 'abc')
        assert status == 400 and 'did not say which show' in r['message'] and rx.load_ledger()['clients']['abc']['credits'] == 1
        assert post(srv, b, 'master-code')[0] == 200                   # the master is not charged, so the bundle is taken
        assert post(srv, no_live(sep25), 'abc')[0] == 200
    finally: srv.shutdown()
