#!/usr/bin/env python3
"""Ingest a saved bundle (from the inbox or a Downloads file) into a show folder without the server.
  python3 ingest.py <bundle.json> ["<folder path>"] [--no-prep] [--work-root DIR]     e.g. "~/Desktop/Whatnot Packing Lists/Sep 24 snacks"
Writes <folder>/work/{show.json,rows.tsv,ship.tsv,segtimes.tsv,seg_urls.txt}, downloads seg/, then runs
run.py prep in that folder (unless --no-prep) and waits for it. Without a folder the receiver's own rule names it
(<Mon D>, <Mon D> <title words> for a second show that day, or the folder that already holds this show id).
--work-root DIR puts the scratch work/ folder under DIR (default ~/whatnot-work, like the receiver); a second
ingest of the same show is a resend.
"""
import sys, os, json, receive
argv = sys.argv[1:]
if '--work-root' in argv:
    k = argv.index('--work-root'); receive.A.work_root = os.path.expanduser(argv[k + 1]); del argv[k:k + 2]
args = [a for a in argv if not a.startswith('--')]
receive.A.no_prep = '--no-prep' in argv
b = json.load(open(args[0])); folder = os.path.expanduser(args[1]) if len(args) > 1 else None
if folder: os.makedirs(folder, exist_ok=True)
receive.process(b, lambda m: print(m, flush=True), 'master', None, reuse=folder)
receive.PREP_Q.join()
