"""run.py wav / tiles: a failed ffmpeg run is counted and reported, and a tile with no candidate frames is not marked
done (no frames/NNN.json), so the next `tiles` run retries the row. ffmpeg itself is faked: no decoding, no whisper."""
import os, sys
from PIL import Image
from conftest import work_of

import run

def run_py(work, monkeypatch, *args):
    monkeypatch.setattr(sys, 'argv', ['run.py', '--work', work, *args]); run.main()

def prepped_work(rx, sep25):
    rx.process(sep25, lambda m: None); work = work_of(rx, 'Sep 25')
    open(os.path.join(work, 'lag.txt'), 'w').write('4.0\n')
    return work

def fake_ffmpeg(write_tile, n_cands):
    """Stand-in for run.ffmpeg: returns 1 and writes nothing when write_tile is None; otherwise returns 0 and writes the
    tile (if asked), n_cands candidate frames, and any single frame output (frames/NNN_k.jpg)."""
    def f(args):
        if write_tile is None: return 1
        out = [a for a in args if a.endswith('.jpg')]
        for o in out:
            if '/tiles/' in o and write_tile: Image.new('L', (16, 12), 128).save(o)
            elif '/cand/' in o:
                for k in range(n_cands): Image.new('L', (16, 12), 40 * k).save(o % k)
            elif '/frames/' in o: Image.new('L', (16, 12), 200).save(o)
        return 0
    return f

def test_tiles_failed_cut_is_reported_and_left_undone(rx, sep25, monkeypatch, capsys):
    work = prepped_work(rx, sep25)
    monkeypatch.setattr(run, 'ffmpeg', fake_ffmpeg(None, 0))
    run_py(work, monkeypatch, 'tiles', '--rows', '0')
    out = capsys.readouterr().out
    assert 'tiles done; 1 rows FAILED (rows 0)' in out
    assert not os.path.exists(os.path.join(work, 'tiles', '000.jpg')) and not os.path.exists(os.path.join(work, 'frames', '000.json'))

def test_tile_without_candidate_frames_is_not_marked_done(rx, sep25, monkeypatch, capsys):
    work = prepped_work(rx, sep25)
    monkeypatch.setattr(run, 'ffmpeg', fake_ffmpeg(True, 0))     # ffmpeg exits 0, writes the tile, no candidate frames
    run_py(work, monkeypatch, 'tiles', '--rows', '0')
    assert 'tiles done; 1 rows FAILED (rows 0)' in capsys.readouterr().out
    assert os.path.exists(os.path.join(work, 'tiles', '000.jpg')), 'the tile is kept for the reviewer'
    assert not os.path.exists(os.path.join(work, 'frames', '000.json')), 'no json: the row stays in todo'
    # the next run retries it; with candidates it completes and reports no failure
    monkeypatch.setattr(run, 'ffmpeg', fake_ffmpeg(True, 3))
    run_py(work, monkeypatch, 'tiles', '--rows', '0')
    out = capsys.readouterr().out
    assert '1 to cut, 0 exist' in out and out.rstrip().endswith('tiles done')
    assert os.path.exists(os.path.join(work, 'frames', '000.json'))
    run_py(work, monkeypatch, 'tiles', '--rows', '0')
    assert '0 to cut, 1 exist' in capsys.readouterr().out

def test_wav_failed_cut_is_reported_and_partial_wav_removed(rx, sep25, monkeypatch, capsys):
    work = prepped_work(rx, sep25)
    def failing(args): open(args[-1], 'wb').write(b'RIFF'); return 1    # ffmpeg died after opening the output
    monkeypatch.setattr(run, 'ffmpeg', failing)
    run_py(work, monkeypatch, 'wav', '--rows', '0,1')
    out = capsys.readouterr().out
    assert 'wav done; 2 clips FAILED (rows 0,1)' in out
    assert not os.path.exists(os.path.join(work, 'wav', '000.wav')) and not os.path.exists(os.path.join(work, 'wav', '001.wav'))
    monkeypatch.setattr(run, 'ffmpeg', lambda args: (open(args[-1], 'wb').write(b'RIFF'), 0)[1])
    run_py(work, monkeypatch, 'wav', '--rows', '0,1')
    assert capsys.readouterr().out.rstrip().endswith('wav done')
