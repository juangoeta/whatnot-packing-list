"""run.py recut --rows: re-cuts and re-matches only those rows. It must not rerun `lag` (Oct 17, 2026: an agent's
`recut --rows 126` rewrote a hand-filtered ghosts.tsv). ffmpeg, the segment cut, whisper and the tile re-cut are stubbed."""
import json, os, sys
from conftest import work_of
from test_dropouts import rows_of

import run

def stub(monkeypatch, w, callout):
    """cut/ffmpeg write empty files; `transcribe` on rc/ writes one line per clip: the row's number and buyer at `callout` s from P."""
    def fake_cut(w_, i, a, b, kind): p = w_.p(f'{kind}_{i}.cat'); open(p, 'w').close(); return p, 0.0
    def fake_ffmpeg(args): open(args[-1], 'w').close()
    def fake_transcribe(rw, a):
        for p in sorted(os.listdir(w.p('rc'))):
            if not p.endswith('.wav'): continue
            r = w.row(int(p[:-4])); why = next(l[1] for l in rows_of(w.p('recut.tsv')) if l[0] == r[0])
            ms = (callout - run.RECUT_WIN[why][0]) * 1000
            json.dump({'transcription': [{'offsets': {'from': ms, 'to': ms + 2000}, 'text': f' number {run.listing_num(r[4])} for {r[2]}'}]}, open(w.p('rc', p + '.json'), 'w'))
    retiled = []
    monkeypatch.setattr(run, 'cut', fake_cut); monkeypatch.setattr(run, 'ffmpeg', fake_ffmpeg)
    monkeypatch.setattr(run, 'cmd_transcribe', fake_transcribe); monkeypatch.setattr(run, 'retile', lambda w_, a, rows: retiled.append(rows))
    return retiled

def test_recut_rows_does_not_rerun_lag(rx, sep25, monkeypatch, capsys):
    rx.process(sep25, lambda m: None); work = work_of(rx, 'Sep 25'); w = run.Work(work)
    open(w.p('lag.txt'), 'w').write('5.0\n'); open(w.p('lag_rows.tsv'), 'w').write(''.join(f'{int(r[0])}\t5.0\n' for r in w.rows))
    ghosts = '100.0\tsomeone\tsomeone\t7\t2\thand-filtered ghost line\n'; open(w.p('ghosts.tsv'), 'w').write(ghosts)
    r3, r5 = w.row(3), w.row(5)
    recut = (f'3\tlate\t{w.P(r3) - 200:.1f}\t{w.P(r3) + 45:.1f}\t1\t-\n' f'5\tbundle\t{w.P(r5) - 300:.1f}\t{w.P(r5) + 25:.1f}\t0\t-\n')
    open(w.p('recut.tsv'), 'w').write(recut)
    retiled = stub(monkeypatch, w, callout=-80)
    monkeypatch.setattr(sys, 'argv', ['run.py', '--work', work, 'recut', '--rows', '3', '--budget', '0']); run.main()
    out = capsys.readouterr().out
    assert 'row 3: callout at P-80 s' in out and 'recut done: 1 clips' in out
    assert open(w.p('ghosts.tsv')).read() == ghosts and open(w.p('lag.txt')).read() == '5.0\n' and not os.path.exists(w.p('silent.tsv'))
    lr = dict(rows_of(w.p('lag_rows.tsv'))); assert lr['3'] == '-80.0' and lr['5'] == '5.0' and len(lr) == len(w.rows)
    assert rows_of(w.p('recut.tsv')) == [l.split('\t') for l in recut.replace('1\t-', '1\trc/003.wav.txt').splitlines()]
    assert retiled == [[3]] and os.path.exists(w.p('rc', '003.wav.json')) and not os.path.exists(w.p('rc', '005.wav'))
