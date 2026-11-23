"""Shared fixtures. receive.py and run.py are scripts, not packages: put their folders on sys.path and
point every path the receiver writes to (Desktop folder, work root, inbox, ledger) at a temp dir."""
import json, os, sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, 'tests', 'fixtures')
for d in ('receiver', os.path.join('pipeline', 'scripts')):
    p = os.path.join(ROOT, d)
    if p not in sys.path: sys.path.insert(0, p)

import receive  # noqa: E402

PLAYLIST = 'https://example.invalid/index.m3u8'
M3U8 = ('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:3\n'
        '#EXT-X-PROGRAM-DATE-TIME:2026-09-25T22:08:21.000Z\n'
        '#EXTINF:2.000,\nhttps://example.invalid/seg/0.ts\n'
        '#EXTINF:2.500,\nhttps://example.invalid/seg/1.ts\n'
        '#EXTINF:3.000,\nhttps://example.invalid/seg/2.ts\n'
        '#EXT-X-ENDLIST\n')

def load_bundle(name):
    b = json.load(open(os.path.join(FIX, name)))
    b['showUrl'] = PLAYLIST   # the fixture has no signed link; the receiver insists on one
    return b

@pytest.fixture
def sep25(): return load_bundle('bundle_sep25.json')

@pytest.fixture
def snacks(): return load_bundle('bundle_snacks.json')

@pytest.fixture
def rx(tmp_path, monkeypatch):
    """receive.py with every output path under tmp_path, prep off, and no network: `get` answers the playlist
    with a 3-segment m3u8 and any segment with a few bytes. Returns the module; rx.calls lists fetched URLs."""
    monkeypatch.setattr(receive.A, 'out', str(tmp_path / 'Desktop'))
    monkeypatch.setattr(receive.A, 'work_root', str(tmp_path / 'work-root'))
    monkeypatch.setattr(receive.A, 'no_prep', True)
    monkeypatch.setattr(receive.A, 'token', 'master-code')
    monkeypatch.setattr(receive, 'HOSTS', ('whatnot.com', 'example.invalid'))   # the fake playlist's host
    monkeypatch.setattr(receive, 'INBOX', str(tmp_path / 'inbox'))
    monkeypatch.setattr(receive, 'LEDGER', str(tmp_path / 'clients.json'))
    calls = []
    def fake_get(url, timeout=60):
        calls.append(url)
        if url.endswith('.m3u8'): return M3U8.encode()
        if url.endswith('.ts'): return b'\x47' * 188
        raise AssertionError(f'unexpected fetch: {url}')
    monkeypatch.setattr(receive, 'get', fake_get)
    monkeypatch.setattr(receive, 'calls', calls, raising=False)
    return receive

def work_of(rx, folder):
    """The real work/ folder of show folder <A.out>/<folder> (work/ is a symlink into the work root)."""
    return os.path.realpath(os.path.join(rx.A.out, folder, 'work'))
