"""run.py chunks / merge / status on a work/ folder the receiver wrote from a fixture bundle (P05): fixed-title rows
and giveaways are pre-filled into notes_fixed.tsv and left out of the chunks; merge puts every row back."""
import glob, os, sys
from conftest import work_of

import run

def run_py(work, monkeypatch, *args):
    monkeypatch.setattr(sys, 'argv', ['run.py', '--work', work, *args]); run.main()

def rows_of(path): return [l.rstrip('\n').split('\t') for l in open(path) if l.strip()]

def test_chunks_prefill_fixed_and_giveaways_on_snacks(rx, snacks, monkeypatch, capsys):
    rx.process(snacks, lambda m: None); work = work_of(rx, 'Sep 24')
    rows = {r[0]: r for r in rows_of(os.path.join(work, 'rows.tsv'))}
    run_py(work, monkeypatch, 'chunks', '--n', '2')
    printed = capsys.readouterr().out
    assert '128 rows: 59 to review, 64 fixed, 5 giveaways' in printed and 'matching no pattern' not in printed
    fixed = rows_of(os.path.join(work, 'notes_fixed.tsv'))
    assert len(fixed) == 69 and all(len(f) == 4 and f[3] == '' for f in fixed)
    # the 12-PACK / 6 PACK rows carry their title (without the trailing item number) and no flag
    packs = [f for f in fixed if f[2] == '']
    assert len(packs) == 64 and all('PACK' in f[1] and '#' not in f[1] and rows[f[0]][4].startswith(f[1]) for f in packs)
    gives = [f for f in fixed if f[2] == 'giveaway']
    assert len(gives) == 5 and all(f[1] == run.GIVEAWAY_TEXT and rows[f[0]][4].startswith('GIVY') for f in gives)
    # exactly the "Snack #N" rows are left for review, each once
    chunked = [r for f in sorted(glob.glob(os.path.join(work, 'chunk[0-9]*.txt'))) for r in rows_of(f)]
    assert sorted(r[0] for r in chunked) == sorted(i for i, r in rows.items() if r[4].startswith('Snack #'))
    assert len(chunked) == 59 and len(set(r[0] for r in chunked)) == 59
    # merge: the agents' notes + notes_fixed.tsv give back every row of rows.tsv
    for k, f in enumerate(sorted(glob.glob(os.path.join(work, 'chunk[0-9]*.txt'))), 1):
        with open(os.path.join(work, f'notes_{k}.tsv'), 'w') as out:
            for r in rows_of(f): out.write(f'{r[0]}\tSnack bar\t\t\n')
    run_py(work, monkeypatch, 'merge')
    merged = rows_of(os.path.join(work, 'notes.tsv'))
    assert len(merged) == len(rows) == 128 and [m[0] for m in merged] == [r[0] for r in rows_of(os.path.join(work, 'rows.tsv'))]
    assert sum(1 for m in merged if m[2] == 'giveaway') == 5 and sum(1 for m in merged if 'PACK' in m[1]) == 64
    assert '128 rows from 3 files (69 pre-filled in notes_fixed.tsv)' in capsys.readouterr().out
    # a second chunks keeps the existing notes_fixed.tsv; status shows the split
    run_py(work, monkeypatch, 'chunks', '--n', '2'); run_py(work, monkeypatch, 'status')
    printed = capsys.readouterr().out
    assert 'notes_fixed.tsv exists, kept as is' in printed and 'review        128 rows: 59 to review, 64 fixed, 5 giveaways   notes_fixed.tsv 69 rows' in printed

def test_chunks_random_pull_show_prefills_only_giveaways(rx, sep25, monkeypatch, capsys):
    rx.process(sep25, lambda m: None); work = work_of(rx, 'Sep 25')
    run_py(work, monkeypatch, 'chunks', '--n', '8')
    assert '176 rows: 166 to review, 0 fixed, 10 giveaways' in capsys.readouterr().out
    fixed = rows_of(os.path.join(work, 'notes_fixed.tsv'))
    assert len(fixed) == 10 and all(f[2] == 'giveaway' for f in fixed)
    chunked = [r for f in glob.glob(os.path.join(work, 'chunk[0-9]*.txt')) for r in rows_of(f)]
    assert len(chunked) == 166 and all(r[4].startswith('Beauty pulls #') for r in chunked)

def test_row_kind_unknown_title_goes_to_review():
    assert run.row_kind('GIVY #3', 0) == 'giveaway' and run.row_kind('Lip gloss #4', 0) == 'giveaway'
    assert run.row_kind('Beauty pulls #12', 300) == 'review' and run.row_kind('SUNGLASSES- PRESCRIPTION SINGLES #168', 1600) == 'review'
    assert run.row_kind('COCONUT SNACKS BEST BY SEP2026- 12 PACK #7', 1200) == 'fixed' and run.listing_title('A 12-PACK #7') == 'A 12-PACK'
    assert run.row_kind('Mystery 12-PACK pulls #2', 500) == 'review', 'a review pattern wins over a fixed one'
    assert run.row_kind('Some brand new listing name #9', 500) is None and run.split_rows([['0', '', '', '', 'Some brand new listing name #9', '500']])['unknown'] == ['Some brand new listing name']

def test_watch_declares_chunks_done_from_disk_and_names_the_missing(rx, snacks, monkeypatch, capsys):
    """P17 (Oct 9, 2026): the review is complete when notes_N.tsv on disk holds every chunk row, whatever the agents say.
    Default chunks are ~20 rows; `watch --once` prints progress / DONE / ALL DONE; `missing` writes the rows still to do."""
    rx.process(snacks, lambda m: None); work = work_of(rx, 'Sep 24')
    run_py(work, monkeypatch, 'chunks')   # 59 rows to review -> 3 chunks of up to 20
    chunks = sorted(glob.glob(os.path.join(work, 'chunk[0-9]*.txt')))
    assert len(chunks) == 3 and max(len(rows_of(f)) for f in chunks) <= 20
    capsys.readouterr()
    run_py(work, monkeypatch, 'watch', '--once')
    assert capsys.readouterr().out.splitlines() == ['chunk 1 0/20', 'chunk 2 0/20', 'chunk 3 0/19']
    c1, c2 = rows_of(chunks[0]), rows_of(chunks[1])
    with open(os.path.join(work, 'notes_1.tsv'), 'w') as out:
        for r in c1: out.write(f'{r[0]}\tSnack bar\t\t\n')
    with open(os.path.join(work, 'notes_2.tsv'), 'w') as out:
        for r in c2[:5]: out.write(f'{r[0]}\tSnack bar\t\t\n')
        out.write(f'{c2[0][0]}\tSnack bar again\t\t\n')   # a duplicate line counts once and is warned about
    run_py(work, monkeypatch, 'watch', '--once')
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == 'DONE chunk 1 (20 rows)' and 'chunk 2 5/20' in lines and any(l.startswith('WARN chunk 2: 1 duplicate') for l in lines)
    run_py(work, monkeypatch, 'missing')
    assert '15 missing -> notes_2.tsv' in capsys.readouterr().out
    missing = rows_of(os.path.join(work, 'missing.txt'))
    assert [m[0] for m in missing] == [r[0] for r in c2[5:]] + [r[0] for r in rows_of(chunks[2])]
    for k, f in enumerate(chunks, 1):
        with open(os.path.join(work, f'notes_{k}.tsv'), 'w') as out:
            for r in rows_of(f): out.write(f'{r[0]}\tSnack bar\t\t\n')
    run_py(work, monkeypatch, 'watch', '--once')
    assert capsys.readouterr().out.splitlines()[-1] == 'ALL DONE'
    run_py(work, monkeypatch, 'status')
    assert 'review done   1:20/20 2:20/20 3:19/19' in capsys.readouterr().out
