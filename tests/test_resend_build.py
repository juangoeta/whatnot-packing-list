"""A resend after prep: a row the `lag` run never saw (an order added by the resend) or a row it saw that is gone
(a cancelled order) must not crash status, show, chunks or build. No ffmpeg, no whisper: lag_rows.tsv, dropouts.tsv
and ghosts.tsv are written by hand as `lag` would leave them."""
import copy, json, os, sys
from conftest import work_of

import run

def run_py(work, monkeypatch, *args):
    monkeypatch.setattr(sys, 'argv', ['run.py', '--work', work, *args]); run.main()

def rows_of(path): return [l.rstrip('\n').split('\t') for l in open(path) if l.strip()]

def fake_lag(w, drift=12.0):
    """lag.txt, lag_rows.tsv (first half of the rows at 0 s, second half at `drift`: per-row offsets are in force when
    the drift exceeds 6 s) and an empty dropouts.tsv, as `lag` writes on a clean show."""
    open(w.p('lag.txt'), 'w').write('5.0\n'); n = len(w.rows)
    open(w.p('lag_rows.tsv'), 'w').write(''.join(f'{int(r[0])}\t{0.0 if k < n // 2 else drift:.1f}\n' for k, r in enumerate(w.rows)))
    open(w.p('dropouts.tsv'), 'w').write('')

def write_notes(w):
    open(w.p('notes.tsv'), 'w').write(''.join(f'{r[0]}\tProduct for {r[4]}\t\t\n' for r in w.rows))

def test_resend_with_a_new_order_on_a_drifting_show_does_not_crash(rx, sep25, tmp_path, monkeypatch, capsys):
    rx.process(sep25, lambda m: None); work = work_of(rx, 'Sep 25'); w = run.Work(work)
    fake_lag(w); write_notes(w)
    assert run.read_row_lags(w) is not None, 'the drift puts per-row offsets in force'
    again = copy.deepcopy(sep25); again['items'].append(dict(again['items'][-1], orderNo='1999999'))
    rx.process(again, lambda m: None, reuse=os.path.join(rx.A.out, 'Sep 25'))
    new = next(r[0] for r in rows_of(os.path.join(work, 'rows.tsv')) if r[3] == '1999999')
    assert new == '176' and new not in {i for i, _ in rows_of(os.path.join(work, 'lag_rows.tsv'))}
    run_py(work, monkeypatch, 'status'); assert 'rows          177' in capsys.readouterr().out
    run_py(work, monkeypatch, 'show', new); assert f'=== row {new} |' in capsys.readouterr().out
    run_py(work, monkeypatch, 'chunks'); capsys.readouterr()
    out = str(tmp_path / 'list'); run_py(work, monkeypatch, 'build', '--out', out)
    pk = json.load(open(os.path.join(work, 'packages.json')))
    assert sum(len(p['items']) for p in pk) == 177 and any(it['order'] == '1999999' and it['change'] == 'NEW' for p in pk for it in p['items'])

def test_build_survives_a_ghost_whose_source_row_was_cancelled(rx, sep25, tmp_path, monkeypatch):
    rx.process(sep25, lambda m: None); work = work_of(rx, 'Sep 25'); w = run.Work(work)
    fake_lag(w); write_notes(w); os.makedirs(w.p('wav'), exist_ok=True)
    src = w.rows[3]; t = w.P(src) + 4   # a ghost callout heard in row 3's clip
    json.dump({'transcription': [{'offsets': {'from': 49000, 'to': 51000}, 'text': ' number 999 for ghostbuyer'}]}, open(w.p('wav', f'{int(src[0]):03d}.wav.json'), 'w'))
    open(w.p('ghosts.tsv'), 'w').write(f'{t:.1f}\tghostbuyer\tghostbuyer\t999\t{src[0]}\tnumber 999 for ghostbuyer\n')
    again = copy.deepcopy(sep25); again['items'] = [it for it in again['items'] if it['orderNo'] != src[3]]   # that order cancelled
    rx.process(again, lambda m: None, reuse=os.path.join(rx.A.out, 'Sep 25'))
    assert src[0] not in {r[0] for r in rows_of(os.path.join(work, 'rows.tsv'))}
    out = str(tmp_path / 'list'); run_py(work, monkeypatch, 'build', '--out', out)
    html = open(os.path.join(out, 'packing_list.html')).read()
    assert '"user":"ghostbuyer"' in html and sum(len(p['items']) for p in json.load(open(os.path.join(work, 'packages.json')))) == 175
