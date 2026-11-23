"""run.py tiles on a subset of rows: tiles_rows.tsv and tiles_arg.txt keep the offsets the other rows were cut with.
ffmpeg and the segment cut are stubbed; the tiles themselves come from test_dropouts.fake_prep."""
import os, sys
from conftest import work_of
from test_dropouts import fake_prep, rows_of

import run

def stub_ffmpeg(monkeypatch):
    def fake_cut(w, i, a, b, kind): p = w.p(f'{kind}_{i}.cat'); open(p, 'w').close(); return p, 0.0
    monkeypatch.setattr(run, 'cut', fake_cut); monkeypatch.setattr(run, 'ffmpeg', lambda args: None)

def test_tiles_rows_keeps_the_other_rows_offsets(rx, sep25, monkeypatch, capsys):
    rx.process(sep25, lambda m: None); work = work_of(rx, 'Sep 25'); w = run.Work(work)
    fake_prep(w, lag=4.0); stub_ffmpeg(monkeypatch)   # every row tiled at offset 3
    assert set(v for _, v in rows_of(w.p('tiles_rows.tsv'))) == {'3'}
    monkeypatch.setattr(sys, 'argv', ['run.py', '--work', work, 'tiles', '--rows', '1', '--lag', '9', '--force']); run.main()
    targs = dict(rows_of(w.p('tiles_rows.tsv')))
    assert targs['1'] == '9' and set(targs[i] for i in targs if i != '1') == {'3'} and len(targs) == len(w.rows)
    assert open(w.p('tiles_arg.txt')).read().strip() == '3', 'the show-wide fallback offset is not the --lag of a --rows run'
    # per-row offsets from a drifting lag_rows.tsv: only the re-cut row takes its own offset
    open(w.p('lag_rows.tsv'), 'w').write(''.join(f'{int(r[0])}\t{0.0 if k % 2 else 15.0:.1f}\n' for k, r in enumerate(w.rows)))
    monkeypatch.setattr(sys, 'argv', ['run.py', '--work', work, 'tiles', '--rows', '2', '--force']); run.main()
    targs = dict(rows_of(w.p('tiles_rows.tsv')))
    assert targs['2'] == str(run.tile_arg_from_lag(15.0)) and targs['1'] == '9' and targs['0'] == '3'
