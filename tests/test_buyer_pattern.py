"""run.py lag on a tiny work/ folder (P16): a buyer whose buyers.json entry holds only mishearings still matches when whisper
spells the username; the 4-letter prefix and the spoken form ("four roses") match too; a short name keeps its word boundary;
an unknown buyer keeps the raw prefix fallback."""
import json, os, sys

import run

ROWS = [   # buyer, listing number, transcript lines (ms from the clip start; the clip starts at P-45, so 55000 is P+10)
    ('tavkl', 140, [(55000, 'tavkl 140')]),                                         # spelled exactly: not in the entry
    ('melissakz2048', 141, [(55000, 'meli 141')]),                                  # 4-letter prefix
    ('4_roses', 142, [(55000, 'four roses 142')]),                               # spoken form of the username
    ('bkt7204', 143, [(50000, 'sbkt 143 no'), (57000, 'bkt 143')]),               # short name: \bbkt\b skips "sbkt" at +5, takes +12
    ('nobody99', 144, [(55000, 'nobo 144')]),                                       # no entry: the raw prefix, as before
    ('dgreen2961', 145, [(55000, 'the green 145')]),                                # not covered: number heard without the buyer
]
ENTRIES = {'tavkl': ['guzk', 'goose'], 'melissakz2048': ['venes'], '4_roses': ['rosez'], 'bkt7204': ['see w'], 'dgreen2961': ['duh green']}

def test_lag_matches_username_prefix_and_spoken_form(tmp_path, monkeypatch, capsys):
    os.mkdir(tmp_path / 'wav')
    with open(tmp_path / 'rows.tsv', 'w') as f:
        for i, (buyer, n, lines) in enumerate(ROWS):
            f.write(f'{i}\t{900000 + i}\t{buyer}\t{1000000 + i}\tSUNGLASSES #{n}\t900\t{1000 + 100 * i}\tTue, 06 Oct 2026 01:00:00 GMT\n')
            json.dump({'transcription': [{'offsets': {'from': ms, 'to': ms + 2000}, 'text': txt} for ms, txt in lines]}, open(tmp_path / 'wav' / f'{i:03d}.wav.json', 'w'))
    monkeypatch.setattr(run, 'BUYER_PAT', run.buyer_pat(ENTRIES))
    assert run.BUYER_PAT['tavkl'] == 'guzk|goose|tavk' and run.BUYER_PAT['4_roses'] == 'rosez|four roses' and run.BUYER_PAT['bkt7204'] == r'see w|\bbkt\b'
    assert run.BUYER_PAT['nobody99'] == 'nobo' and run.BUYER_PAT.get('nobody99') is None, 'an unknown buyer: the prefix on lookup, no entry for learn-buyers'
    monkeypatch.setattr(sys, 'argv', ['run.py', '--work', str(tmp_path), 'lag', '--all']); run.main()
    report = open(tmp_path / 'lag_report.txt').read().split('\n')
    assert report[0].startswith('NB matches 5 of 6') and 'flagged 1' in report
    best = {l.split()[1]: l.split('|')[2].split()[0] for l in report if l.startswith('  ')}
    assert best == {'tavkl': '+10.0s', 'melissakz2048': '+10.0s', '4_roses': '+10.0s', 'bkt7204': '+12.0s', 'nobody99': '+10.0s', 'dgreen2961': '+10.0s'}
    assert [l for l in report if l.startswith('  5 dgreen2961')][0].endswith('N  the green 145 CHECK')

def test_transcripts_and_status_count_four_digit_row_ids(tmp_path, monkeypatch, capsys):
    """Row ids run past 999 on big shows and after resends; wav/1000.wav.json must be read like wav/999.wav.json."""
    os.mkdir(tmp_path / 'wav')
    with open(tmp_path / 'rows.tsv', 'w') as f:
        for i in (999, 1000, 1001):
            f.write(f'{i}\t900000\ttavkl\t{1000000 + i}\tSUNGLASSES #{i - 900}\t900\t{1000 + i}\tTue, 06 Oct 2026 01:00:00 GMT\n')
            json.dump({'transcription': [{'offsets': {'from': 55000, 'to': 57000}, 'text': f'tavkl {i - 900}'}]}, open(tmp_path / 'wav' / f'{i:03d}.wav.json', 'w'))
    open(tmp_path / 'wav' / '1001.wav', 'w').close()
    w = run.Work(str(tmp_path))
    assert [i for _, _, i in run.transcript_segments(w)] == [999, 1000, 1001]
    monkeypatch.setattr(sys, 'argv', ['run.py', '--work', str(tmp_path), 'status']); run.main()
    out = capsys.readouterr().out
    assert 'audio         3 / 3 cut' in out and 'transcripts   3 / 3' in out
