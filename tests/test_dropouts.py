"""P10: stream dropouts from what prep already made. A row whose tile is black in all 12 cells and whose transcript is
empty (or only whisper's [BLANK_AUDIO]) marks a gap; rows whose sale window overlaps it are pre-filled by `chunks` as
`missing: no video A-B s` and left out of the chunks. Small synthetic tiles (160x120 JPEG, 4x3 cells) stand in for the
real 1440x1920 ones; no ffmpeg, no whisper."""
import glob, json, os, sys
from PIL import Image
from conftest import work_of

import run

def run_py(work, monkeypatch, *args):
    monkeypatch.setattr(sys, 'argv', ['run.py', '--work', work, *args]); run.main()

def rows_of(path): return [l.rstrip('\n').split('\t') for l in open(path) if l.strip()]

def write_tile(path, t0, gap):
    """A 4x3 tile whose cell k shows the frame at t0 + 3k + 1.5: black when that instant is inside gap, grey otherwise."""
    im = Image.new('L', (160, 120), 128)
    for k in range(12):
        if gap and gap[0] <= t0 + 3 * k + 1.5 < gap[1]: im.paste(0, (40 * (k % 4), 40 * (k // 4), 40 * (k % 4) + 40, 40 * (k // 4) + 40))
    im.save(path, quality=90)

def fake_prep(w, gap=None, lag=4.0):
    """Tiles and transcripts for every row as prep would leave them (lag.txt, tiles_arg.txt, tiles_rows.tsv included).
    Rows whose tile is fully inside the gap get an empty transcript; one of them gets whisper's [BLANK_AUDIO] marker."""
    arg = run.tile_arg_from_lag(lag); os.makedirs(w.p('tiles'), exist_ok=True); os.makedirs(w.p('wav'), exist_ok=True)
    open(w.p('lag.txt'), 'w').write(f'{lag:.1f}\n'); open(w.p('tiles_arg.txt'), 'w').write(f'{arg}\n')
    open(w.p('tiles_rows.tsv'), 'w').write(''.join(f'{int(r[0])}\t{arg}\n' for r in w.rows))
    inside = []
    for r in w.rows:
        i = int(r[0]); t0 = w.P(r) + arg - run.TILE_SPAN
        write_tile(w.p('tiles', f'{i:03d}.jpg'), t0, gap)
        blank = bool(gap) and gap[0] <= t0 and t0 + 36 <= gap[1]
        if blank: inside.append(i)
        seg = [] if blank and len(inside) != 2 else [{'offsets': {'from': 0, 'to': 5000}, 'text': ' [BLANK_AUDIO]' if blank else f' number {i} for {r[2]}'}]
        json.dump({'transcription': seg}, open(w.p('wav', f'{i:03d}.wav.json'), 'w'))
    return inside

def test_clean_show_has_empty_dropouts_and_nothing_else_changes(rx, sep25, monkeypatch, capsys):
    rx.process(sep25, lambda m: None); work = work_of(rx, 'Sep 25'); w = run.Work(work)
    assert fake_prep(w) == []
    assert run.dropout_report(w) == [] and os.path.getsize(w.p('dropouts.tsv')) == 0
    run_py(work, monkeypatch, 'chunks', '--n', '8'); run_py(work, monkeypatch, 'status')
    printed = capsys.readouterr().out
    assert 'dropout' not in printed.split('dropouts      ')[0] and 'dropouts      0 gaps, 0 rows inside -> dropouts.tsv' in printed
    fixed = rows_of(w.p('notes_fixed.tsv'))
    assert len(fixed) == 10 and all(f[2] == 'giveaway' for f in fixed), 'the P05 pre-fill is unchanged on a clean show'

def test_black_tiles_with_empty_transcripts_mark_a_gap_and_prefill_missing(rx, sep25, monkeypatch, capsys):
    rx.process(sep25, lambda m: None); work = work_of(rx, 'Sep 25'); w = run.Work(work)
    lag = 4.0; arg = run.tile_arg_from_lag(lag); order = sorted(w.rows, key=w.P)
    # a 60 s black + silent stretch over the tightest run of three sales, starting 2 s before the first tile window
    k = min(range(len(order) - 2), key=lambda k: w.P(order[k + 2]) - w.P(order[k]))
    a = w.P(order[k]) + arg - run.TILE_SPAN - 2; gap = (a, a + 60)
    inside = fake_prep(w, gap, lag)
    assert len(inside) >= 3 and all(int(order[k + j][0]) in inside for j in range(3))
    # `lag` cannot run without real transcripts; its hook is dropout_report, which tiles also calls at its end
    lines = run.dropout_report(w)
    drops = rows_of(w.p('dropouts.tsv'))
    assert [int(d[0]) for d in drops] == inside and all(d[2] == 'black+silent' for d in drops)
    (ga, gb), = {(float(d[3]), float(d[4])) for d in drops}
    # the edges come from black cells (3 s each) of any tile that overlaps the gap: never wider than the truth, and within
    # 3 s of it as far as a tile covers it (only the windows around sales are looked at)
    t0s = [w.P(r) + arg - run.TILE_SPAN for r in w.rows]; cover = max(t + 36 for t in t0s if gap[0] <= t and t + 36 <= gap[1])
    while any(t <= cover < t + 36 for t in t0s): cover = max(t + 36 for t in t0s if t <= cover < t + 36)   # contiguous tile coverage past the black rows
    assert gap[0] <= ga <= gap[0] + 3 and min(gap[1], cover) - 3 <= gb <= gap[1], (ga, gb, gap, cover)
    assert lines[0].startswith(f'STREAM DROPOUTS (black tile, empty transcript): 1 gap(s), ') and f'{ga:.0f}-{gb:.0f} s' in lines[1]
    # every row whose sale window (P+lag-10 .. P+lag+5) overlaps the gap is flagged, the dropout rows among them
    flagged, gaps = run.dropout_rows(w)
    want = {r[0] for r in w.rows if w.P(r) + lag - 10 < gb and ga < w.P(r) + lag + 5}
    assert gaps == [(ga, gb)] and set(flagged) == want and {str(i) for i in inside} <= want
    assert all(f == f'missing: no video {ga:.0f}-{gb:.0f} s' and run.parse_flag(f)[:2] == ('missing', 'revise') for f in flagged.values())
    # chunks: the flagged rows go to notes_fixed.tsv with the listing title and the flag, and out of the chunks
    run_py(work, monkeypatch, 'chunks', '--n', '8')
    printed = capsys.readouterr().out
    assert f'{len(want)} rows fall in 1 stream dropout(s) (dropouts.tsv): pre-filled as missing' in printed
    fixed = {f[0]: f for f in rows_of(w.p('notes_fixed.tsv'))}
    miss = {i: f for i, f in fixed.items() if f[2].startswith('missing')}
    assert set(miss) == want - {i for i, f in fixed.items() if f[2] == 'giveaway'}
    assert all(f[1] == run.listing_title(w.row(i)[4]) and f[2] == flagged[i] for i, f in miss.items())
    chunked = {r[0] for f in glob.glob(w.p('chunk[0-9]*.txt')) for r in rows_of(f)}
    assert not (chunked & want) and len(chunked) + len(fixed) == len(w.rows)
    run_py(work, monkeypatch, 'status')
    assert f'dropouts      1 gaps, {len(want)} rows inside -> dropouts.tsv' in capsys.readouterr().out

def test_a_black_tile_with_speech_or_a_missing_tile_is_not_a_dropout(tmp_path):
    t = str(tmp_path / 'black.jpg'); write_tile(t, 0, (0, 36)); g = str(tmp_path / 'grey.jpg'); write_tile(g, 0, None)
    assert all(run.tile_black_cells(t)) and not any(run.tile_black_cells(g))
    j = str(tmp_path / 'x.json')
    json.dump({'transcription': []}, open(j, 'w')); assert run.transcript_blank(j)
    json.dump({'transcription': [{'text': ' [BLANK_AUDIO]'}, {'text': '(silence)'}]}, open(j, 'w')); assert run.transcript_blank(j)
    json.dump({'transcription': [{'text': ' [BLANK_AUDIO]'}, {'text': ' number 12 for buyer01'}]}, open(j, 'w')); assert not run.transcript_blank(j)
    # a work folder with one row: black tile but speech -> no dropout; no tile at all -> no dropout
    d = tmp_path / 'work'; d.mkdir(); (d / 'tiles').mkdir(); (d / 'wav').mkdir()
    (d / 'rows.tsv').write_text('0\t900001\tbuyer01\t1000001\tLip gloss #1\t500\t100.0\t2026-09-25T22:10:00Z\n')
    w = run.Work(str(d)); write_tile(w.p('tiles', '000.jpg'), 0, (0, 36))
    json.dump({'transcription': [{'text': ' number one for buyer01'}]}, open(w.p('wav', '000.wav.json'), 'w'))
    assert run.find_dropouts(w) == []
    os.remove(w.p('tiles', '000.jpg')); json.dump({'transcription': []}, open(w.p('wav', '000.wav.json'), 'w'))
    assert run.find_dropouts(w) == [] and run.dropout_rows(w) == ({}, [])
