---
name: whatnot-video-receipts
description: "Build a packing list from a Whatnot live show: for every unfulfilled order, identify exactly what was sold (brand, product, shade, bundle size) by pulling the video receipt timestamps, grabbing frames from the show recording at the moment of sale, and transcribing the seller's audio. Use when Juan says he did a Whatnot live and needs to know who bought what, wants video receipts reviewed, or asks for a packing/shipping list for a show."
---

<!-- argument-hint: [which show, e.g. "the Sep 25 show" — or leave blank to ask] -->

# Whatnot video receipts → packing list

Goal: one document (web page + CSV + Markdown) listing every package, every order number, and what to
put in the box, with a short list of orders that need Juan's own check. Zero guessing: every product is
identified from the frames at the sale moment AND from what the seller said on the mic.

Proven on ten shows, Sep 11 - Sep 25, 2026 (128 to 372 items each). Per-show notes, lags, product rules
and buyer spellings are in `HISTORY.md`; read the most recent section before a new show.
Everything runs through `scripts/run.py`, one subcommand per stage, from the show's `work/` folder.

## The one insight that makes this reliable

Whatnot video receipts are not separate clips. They are the full show recording (HLS) plus a start
offset. `countdown.vtt` for an order starts 30 s before the purchase, so **purchase time P = vttStart + 30**
on the stream timeline. Segment URLs are signed with a wildcard policy, so one `index.m3u8` lets the
receiver download the whole show. That turns "watch 300 receipts in real time" into "cut 300 clips
locally with ffmpeg".

Second insight: right after each sale the seller says the buyer's name and the listing number
("Maria 171", "Sam thank you 63"). Those callouts, transcribed with whisper, are the strongest
cross-check of which item went to which order. Listing numbers cycle, so match on buyer + number + time.

Third: **the recording lags real time by a different amount every stream** (-10 s to +13 s so far).
`run.py lag` measures it from the callouts before any tile is cut. Never skip it.

## Layout

    ~/Desktop/Whatnot Packing Lists/<Mon D>/          one folder per show; a second show that day gets the first two
                                                       title words ("Sep 24 Makeup Madness"); a resend finds its folder by show id
        packing_list.html / .csv / .md                 the deliverable
        packing_slips.html                             one printable slip per shipping label, buyer-facing (D11)
        evidence -> ~/whatnot-work/evidence/<Mon D>/   three frames per flagged order, <orderNo>_1..3.jpg, native size,
                                                       kept outside iCloud (symlink); the page links them
        work -> ~/whatnot-work/<Mon D>/                everything else, OUTSIDE iCloud (the Desktop syncs; iCloud evicts
                                                       big files when the disk is full and blocks reads). Deleted at the end.
            show.json livestream.json stream_start.txt  from the receiver
            rows.tsv    i shipmentId buyer orderNo listing cents vttStart createdAt
            ship.tsv    shipmentId buyer tracking courier method qty isFirst
            segtimes.tsv seg_urls.txt seg/N.ts          the recording (~2 s segments, cumulative times)
            wav/ lag.txt tiles/ frames/ prep.log PREP_DONE  run.py prep (started by the receiver); .wav files are
                                                        deleted once their .json transcript exists
            notes_fixed.tsv chunkN.txt REVIEW.md notes_N.tsv notes.tsv  review (notes_fixed.tsv: rows that need none, P05)
            packages.json                               run.py build
            rows.prev.tsv diff.json                     a resend: the previous order list and what changed (R08)

Client shows (access codes other than the master) land in `<Mon D> - <Client name>/`.

## Steps

**Resuming after a dead session (Oct 9, 2026: the session died mid-publish, the next one started with no context).**
Before anything: `python3 run.py status` in the newest `work/` (it says what is cut, reviewed, built), `Artifact list`
(is the list already published?), and the last `HISTORY.md` section. Continue from the first thing not done. Never
redo a stage whose outputs exist; `notes.tsv` from the previous session is the deliverable, do not re-merge over it
unless `merge` is needed for new rows.

### 0. Ask up front (Aloud if it runs, else text; check `pgrep Aloud` first)
- Which show (title + date). Unfulfilled only (default) or everything sold.
- Anything special about the products (a new category, a run of bundles) → `work/show_notes.md`.

### 1. Collect the orders and the recording (extension + receiver)
On the Seller Hub Shipments page, filtered to the show and to Unfulfilled, click the extension card's
**Send show for packing list** (with the computer tool if Juan is not doing it). The receiver
(`~/whatnot-packing-extension/receiver/receive.py`, running as a login service, log in
`~/Library/Logs/whatnot-receiver.log`) writes `work/` and downloads every segment; `work/DOWNLOAD_DONE`
appears when it is finished (3-7 min; its first line says how many segments are missing), then prep
starts by itself (Step 2).
- Two shows the same day need no manual step: the second lands in `<Mon D> <two title words>/`, and a resend
  lands in the folder that already holds that show id, whatever it was renamed to.
- Bundle saved but not processed (receiver was down): `python3 receiver/ingest.py <inbox file> ["<folder>"]`
  (no folder: the receiver's own naming rule above).
  The inbox is `~/Library/Application Support/whatnot-receiver/inbox/`. Show links expire in hours.
- The extension refuses to send until every order is on the page (expected = sum of package item counts).
- **"Not sent: Could not reach the service (Failed to fetch)"** = Tailscale is down on this Mac (`tailscale status`). Fix needs
  sudo (`sudo launchctl enable system/com.tailscale.tailscaled && sudo launchctl bootstrap system
  /Library/LaunchDaemons/com.tailscale.tailscaled.plist`, Juan types it). No-sudo fallback, same speed: collect the bundle in the
  page with `window.__wnCollect.collectShow(...)` and POST it by XMLHttpRequest to `http://127.0.0.1:8787/show` with
  `X-Packing-Token` = the receiver's master token, the first line of `receiver/ACCESS_CODE.txt` (mode 600:
  `head -1 receiver/ACCESS_CODE.txt`; since Oct 29, 2026 it is no longer on the receiver's command line); recipe in
  HISTORY.md, Oct 23, 2026.

### 2. Prep: audio, transcripts, lag, tiles (automatic)
The receiver runs `run.py prep` itself as soon as the download finishes, one show at a time, and posts a
macOS notification when the show is ready for review. Look in `work/`:
- `PREP_DONE` (lines: `lag +6.4`, `tiles 176`, `minutes 11.8`) → go to Step 3. Glance at `dropouts.tsv` next to it (P10, below): empty on a clean show.
  **First show after Oct 1, 2026 (D04 frames): do this comparison yourself, unprompted, and tell Juan the
  findings in the final report.** Compare prep minutes with the last show of similar size (Sep 25: 176 orders,
  11.8 min; Sep 24: 327 orders; Sep 26: 963 orders, whisper 14 min) and `du -sh` of `work/frames/` and the
  evidence folder. Juan will not ask; he expects the numbers and a verdict (within the cap or not). The cap is
  +1 minute on a 300-order show; if prep grew more, cut `FRAME_WIN` in `run.py` from 13 to 7 candidates and say so.
- `PREP_FAILED` (exit code + last log lines) or neither marker after `DOWNLOAD_DONE` + 20 min → read
  `work/prep.log`, fix, and run it by hand:

      cd "~/Desktop/Whatnot Packing Lists/<Mon D>/work"
      python3 ~/whatnot-packing-extension/pipeline/scripts/run.py prep

`prep` = `wav` (P-45..P+35 per row) → `transcribe` (whisper-cli, `~/.cache/whisper/ggml-small.en.bin`,
~14 clips/min) → `lag` (median callout offset → `lag.txt`, per-row report → `lag_report.txt`, `recut.tsv`) →
`recut` (P06: rows where nothing was heard around P, late payments, and rows whose transcript has a mystery-bag phrase
from `pipeline/data/bundle_words.json` get `rc/NNN.wav.txt` + `rc/NNN.jpg` over a 4-5 min window, whisper on 4
threads, then `lag` runs again and rows it now matches are tiled at their callout; capped at 2 min of prep, longest
unmatched stretch first, the rest stay in `recut.tsv` with `-` and `run.py recut --budget 0` cuts them) →
`tiles` (4x3 grid, one frame every 3 s, offset from the lag by the rule in `run.py --help`; from the same
decode, `frames/NNN_1..3.jpg`: the three sharpest native-size frames from the 9 s before the callout to 3 s
after, 2 s apart and visibly different, offsets in `frames/NNN.json`). Each stage skips what already exists.
Check `lag_report.txt`'s first line: "NB matches N of M, median +6.4 s". Its last lines list **ghost sales** (a
buyer's name + a listing number heard on the mic with no matching order within 4 min: failed payment or
cancelled during the show; `ghosts.tsv`; a name the pipeline does not know needs a number that is no order at all) and
**silent orders** (buyer never heard around the sale; `silent.tsv` is `row<TAB>kind`: `-` nothing heard, the only kind
the page counts; `N` number heard, `B` buyer heard, `G` giveaway with no winner read out).
Then **off-by-one chains** (`chains.tsv`): 3+ orders in a row, within one listing series, where the number heard
with the buyer's own name is the order's number +-1. That is the sign of a systematic shift (failed payment,
skipped sticker: "every order was for the previous person"), which per-row review misses because each row
still looks plausible. If the report lists a chain, look at its first and last order before anything else.
`merge` flags every row in a chain (`item: possible off-by-one (items #50-#70 shifted by -1)`, appended to an
existing flag) and `build` shows each chain as one banner in the double-check list linking those two orders.
`build` shows the ghosts in their own box ("do not pack") with a picture from 6 s before the callout and the
transcript; pictures need the segments, so if `seg/` was trimmed run `python3 run.py fetch <t-10>-<t-2>,...`
first (it refreshes expired segment links from the playlist by itself while the show link is fresh). Under ~30% matched means buyer
spellings are missing: `python3 run.py learn-buyers` proposes them from rows where the number was heard
(`--write` adds them to `pipeline/data/buyers.json`, one list of regex alternatives per username), then
rerun `lag` and `tiles --force`. `run.py status` shows what is done; `--rows 0-19` runs a stage on a few rows.
**Stream dropouts** (P10, Sep 23: the stream went black and silent for two minutes and swallowed two sales): a row
whose tile is black in all 12 frames AND whose transcript is empty (or only `[BLANK_AUDIO]`) marks a gap. No new decode:
Pillow over the existing `tiles/NNN.jpg` (about 1 s per 335 tiles) and the `wav/*.json` as they are. `lag` and the end
of `tiles` write `dropouts.tsv` (`i P kind a b`; empty on a clean show, which changes nothing else) and print
"STREAM DROPOUTS: N gap(s), M orders inside" with the stretches (also at the end of `prep.log`; `status` shows
`dropouts 1 gaps, 5 rows inside`). `chunks` pre-fills every row whose sale window (P+lag-10 .. P+lag+5) overlaps a gap
into `notes_fixed.tsv` as the listing title + flag `missing: no video 745-802 s` (revise level) and leaves it out of the
chunks: there is nothing to see or hear. The page's lede says "the recording has N gaps; M orders fall in them". Only
the windows around orders are checked (a gap between sales is invisible and does not matter); the edges are 3 s
coarse (one tile frame). Recovering those sales from the seller's recap stays manual: `run.py show <i>` and the recap.

### 3. Review — this is the real work, budget for it
    python3 run.py chunks               # notes_fixed.tsv (no review needed) + chunk1..N.txt of ~20 rows each, sorted by sale time + REVIEW.md
                                        # (--size 20 is the default; --n overrides. 43-row chunks looped on Oct 9, never go back)
`chunks` first sorts the rows by listing title with `data/listing_patterns.json` and prints
"176 rows: 112 to review, 50 fixed, 14 giveaways" (`status` prints the same line). Fixed-title listings
(the title is the product, e.g. "... 12 PACK #7") and giveaways (`GIVY #n` or $0) go straight into
`notes_fixed.tsv` (`i product flag frames`: the title without its item number, no flag; "Giveaway prize
(see show setup)", flag `giveaway`) and are left out of the chunks. Random pulls (`Beauty pulls #N`,
`Snack #N`, `... SINGLES #N`) are reviewed. A title matching no pattern is reviewed and named by `chunks`:
if it is the product, add one regex to the `fixed` list in the json. An existing `notes_fixed.tsv` is
kept (hand edits), delete it to regenerate.
`REVIEW.md` carries the lag, the tile frame positions, the known spellings of this show's buyers (from
`data/buyers.json`) and the canonical names of products sold on the last three shows (from
`data/catalog.json`), so agents reuse the same wording. Product notes for this show go in
`work/show_notes.md` before `chunks` (eyewear shows too: brand on the temple arm, optical vs sunglasses, count of
frames; there is no separate template). REVIEW.md gives `run.py` by its absolute path, since `run.py` is not in
`work/`; agents run `python3 .../pipeline/scripts/run.py show i j k` from `work/`. Start one background agent per chunk:
"Read `<work>/REVIEW.md`. Your chunk is `chunkN.txt`. Write `notes_N.tsv`, one line per row, appended right after
each row, each tile opened once. Finish in 15 minutes." The agent's reply is NOT the signal that the chunk is done.

**The watcher is the signal (P17, Oct 9, 2026).** Right after launching the agents, start
`python3 .../run.py watch` with the Monitor tool (or `run_in_background` Bash and read its output file). Every line
is an event; act on them and on nothing else:
- `DONE chunk N (m rows)`: the file on disk is complete. If that agent is still running, stop it (TaskStop); do not
  wait for its report.
- `STALLED chunk N k/m ... missing: ids`: no new row for 5 min. Stop that agent. `python3 run.py missing` writes
  the rows to `missing.txt`; review them yourself (`show i` + the tile, once each) and append them to `notes_N.tsv`,
  or hand `missing.txt` to ONE fresh agent with the same brief. Do not restart the stuck agent.
- `ALL DONE`: stop every agent still running, go to merge. `TIMEOUT` (40 min): same, then `missing`.
- Never sit waiting on agent notifications: if nothing has arrived in 10 min, run `run.py watch --once` or
  `run.py status` (it prints `review done 1:20/20 2:17/20 ...`) and act on the numbers.
Why: on Oct 9, 2026 four of ten agents (43 rows each) looped for 43 min re-reading the same tiles 46 times each
($378 of a $385 session) while their notes files were already complete; the main session idled 20 min waiting for
reports that never came. The same loop risk exists for any agent that reads many images: a chunk is 20 rows now,
the brief forbids re-opening a tile, and the review completes from disk.

Rules that held on every show (also in REVIEW.md):
- The item on camera just before the callout is the sale. If a row's callout is far from the measured
  lag (late payment: createdAt 30-180 s late), trust the callout time; search other rows' transcripts
  for `number N` (`grep -il "number N\b" wav/*.txt`).
- "Bundles of N", "two packs", "you're getting three", "buyer's choice" live in the audio; write the qty
  into the product text. Buyer's-choice items resolve within ~20 s after the callout.
- Giveaways (`GIVY #n`, $0): the item on camera at the draw is NOT the prize. Flag "giveaway".
- Opaque bags / mystery bundles: the seller lists the contents for 2-5 minutes before the sale; the row is
  in `recut.tsv` with its long clip in `rc/` (`show i` prints it; `run.py recut --rows i --budget 0` makes a
  missing one; `--rows` re-matches only those rows, their `lag_rows.tsv` entry and tile, and no longer reruns
  `lag`, so a hand-filtered `ghosts.tsv` is safe; a plain `recut` or `lag` refreshes ghosts, silent and chains).
  Write the whole list. Flag "in bag: N items".
- Shades: read the package number. Unreadable and not said → flag "shade not stated", never guess.
- Runs of the same item repeat for minutes; neighbouring tiles look identical. That is expected.

    python3 run.py merge                # notes_*.tsv + notes_fixed.tsv -> notes.tsv; refuses on missing/duplicate rows
`notes_N.tsv` has a fourth column, `frames`: the tile frame numbers (1-12, up to three) the agent relied on.
`build` cuts those frames for the seller's evidence panel; empty means the pipeline's own sharp picks are used.
A row whose frames do not show the item clearly MUST be flagged (bag, off camera, label unreadable).
**Do NOT run `run.py normalize` on notes.tsv** (Sep 29, 2026: its catalog rules rewrote a Glacial Mint
deodorant as Coconut Cream and an e.l.f. brush as a sunscreen; the rules are too loose). The agents'
wording is the deliverable. `normalize --dry-run` may be used to list wording variants to unify by hand;
the catalog stays for the pick list only, and its rules need anchoring before normalize is trusted again.
Then **clean flags**: drop flags that only restate a certain count ("qty: seller said bundle of 6" when the
text already says 6x); keep real uncertainty. Sep 24: 74 → 49 flags.

### 4. Build the document
    python3 run.py build                # packing_list.{csv,md,html} + packing_slips.html in the show folder; packages.json in work/
Title, date, stream start and the page's localStorage key come from `show.json`, `livestream.json`,
`stream_start.txt` and the folder name; override with `--title/--date/--start/--key/--out`.
The page (`scripts/page_template.html`): **Item #** (the `#N` from the listing title, the seller's sticker
number) is the big number on every row, with the listing series under it when a show has more than one
numbering series (Sep 26: "Beauty pulls" and "Beautiful pulls"); the Whatnot order number is hidden until
the Order column is unfolded from its header (never remembered, so every open starts collapsed). Search `#14` matches item
14 only. **Sort** (D05): packages by sale time / buyer A-Z (= Whatnot's label print order) / item count /
most expensive first / needs-check first / no label first, orders inside by sale time / item # / price / flagged
first, each with a direction flip. The choice is remembered per show. (The "one list" and "order numbers" toggles
were removed from the toolbar on Oct 1, 2026; the Order column unfolds from its header arrows.) **Filters** (D06, trial set): Buyer type-ahead, Flag reason chips
(one per P08 category present in the show, two chips = either) and Hide giveaways
(GIVY or $0 rows); they combine with search, sort, "Hide packed" and "Needs check only", show as removable chips in a
"Showing" row with Clear all, and the stats row adds "N of M items showing" while anything is filtered. Search incl. tracking, per-order "packed" checkbox, "only needs-check" filter, print view, tracking number with Copy per package, "Split shipment · label 1 of 2"
badges when a buyer has 2+ labels, plus a box listing label → package → tracking at the top.
The page opens with a "How this list was checked" box (orders read, callouts matched, recording offset and drift,
ghost sales, double-check count; "not measured" when a stage was skipped) - the seller's proof that the list was
reconciled against Whatnot and corrected for the receipt timing.
**Every order has a "Show evidence" button**: three native-size frames (click to zoom) with their
offset from the countdown, and the transcript around the sale with the callout line highlighted, so Juan
resolves flags without the replay. **Every order gets the panel** (`--evidence all` is the default since Oct 6, 2026: Juan wants 2-3 pictures on every order; `--evidence flagged` only on request
(Sep 26: 963 orders x 3 frames = ~320 MB). `build` writes the frames to `~/whatnot-work/evidence/<Mon D>/`
(outside iCloud; `<show>/evidence` is a symlink; rebuilt from scratch each time; needs `frames/` and
`wav/*.json` still in `work/`, so build BEFORE Step 6). CSV has an `Evidence` column, Markdown links the frames.
**Evidence card for a Whatnot claim** (D12): every evidence panel has "Save evidence card": one PNG (960 px wide,
0.5-0.9 MB) with the show, buyer, order #, item #, sale time, package and tracking, the product text, the three
frames with their offsets, the transcript with the callout highlighted, and a footer naming the source. The
Desktop copy downloads `evidence-<order>.png`; the published artifact shows it in a dialog ("long-press or
right-click to save", artifacts block downloads). "Save all flagged as evidence cards (zip)" above the
double-check list does every revise row at once (Desktop copy only). Chrome will not export a canvas on a page
opened from `file://`, so the Desktop page says to use the CLI instead, which draws the identical layout with Pillow
from `packing_list.html` (no `work/` needed, works after Step 6):

    cd "~/Desktop/Whatnot Packing Lists/<Mon D>"
    python3 ~/whatnot-packing-extension/pipeline/scripts/run.py evidence-card <order> [<order>...]   # evidence-<order>.png here
    python3 ~/whatnot-packing-extension/pipeline/scripts/run.py evidence-card --flagged              # evidence-cards.zip
    python3 ~/whatnot-packing-extension/pipeline/scripts/run.py evidence-card --check-layout <order> # diff vs the page JS (node)

Nothing is rendered at build time; cards cost time and disk only when asked for.
**Packing slips** (D11): `build` also writes `packing_slips.html` (`scripts/slips_template.html`, ~300 KB for 963
orders, no extra time): one slip per shipping label, in Whatnot's label export order (username A-Z, a dropdown
switches to order count or order time; `--slip-order` sets the default), each with buyer, "Label n of N" for a
split buyer, carrier + tracking, show title and date, then item #, OUR product text, order # and a check box per
order. Buyers see it: no flag text and no seller name; rows to double-check and giveaway prizes get a small dot
and the slip says "Giveaway prize" only. Paper dropdown: 4x6 label (one slip per label, a long shipment continues
on the next label with the same header) or letter (two slips per sheet). The footer line is set once with
`build --slip-note "Thanks for shopping with ..."` (kept in `data/slip_note.txt`; default "Thank you for your order!").
The page's "Print slips" button opens it, so publish it next to the list (`files`: `packing_slips.html`). Print
preview was not checked by a browser yet; look once before sending slips to a seller.
Put `<meta charset="utf-8">` first in the local copy. Publish the HTML as an Artifact (load
`artifact-design` first) **with the evidence as supporting files**: always run
`python3 scripts/pack_evidence.py packing_list.html <pub dir>` first (one sheet per package, three frames per
order at 480 px, ~45 MB for 963 orders; `<pub dir>` must be under this session's scratchpad or `work/`, the Artifact
tool refuses other paths) and publish that folder **in the batches it writes**: `files_1.json` (slips + sheets),
`files_2.json`, ... each under 32 MB, first publish without `url`, the rest to the same URL. One 58 MB batch closed
the socket on Oct 9, 2026 ("Socket is closed, slug reserved"); if that happens, publish again to the reserved slug
with a smaller batch. Then `Artifact list --scope files` must show every sheet. Artifacts are private: Juan
must Share from the page before sending the link.

**Resend after the list is built** (labels generated later, a pending payment that cleared, a cancellation):
Juan clicks Send again in the extension; the receiver rewrites `rows.tsv`/`ship.tsv` without re-downloading,
keeps the old list as `rows.prev.tsv`, writes `diff.json` (orders added / removed / moved to another package,
tracking numbers added; order identity = Whatnot order number, package identity = shipment id) and, because
`notes.tsv` exists, rebuilds the three Desktop files itself. Notification: "3 new, 1 removed, 41 tracking
numbers added". The page opens with a **"Changed since your last list"** box: NEW rows green with the product
text, REMOVED rows red with the product text from `notes.tsv` and "pull this out of <buyer>'s box if already
packed", MOVED rows with old and new package; the CSV has a `Change` column and keeps the REMOVED rows at
the end. The diff lasts until the next resend (a third send with no changes clears it). Row ids are stable
across resends: an order keeps its index, so `notes.tsv`, `wav/`, `tiles/`, `frames/` still line up and
`rows.tsv` may have gaps. A Send on a show whose `work/` was deleted (Step 6) only updates the order files;
nothing is re-downloaded. Only the artifact needs a session: republish `packing_list.html` from the same
file path (keeps the URL). **New orders** go in as NEW "(not reviewed)" and the receiver log names their ids:
`run.py wav --rows <ids>`, `transcribe`, `tiles --rows <ids>`, review them (a `chunkN.txt` by hand or
`show <id>`), add them to a `notes_N.tsv`, `merge`, `build`. If `seg/` was already deleted, identify the
item from the Whatnot order page and write the row into `notes_fixed.tsv`.

**Pick list by product — ONLY when Juan asks** (Sep 25, 2026: do not make pick lists unless asked):
`python3 scripts/picklist.py` (reads `packages.json` + the catalog; prints unmatched texts, add them to the
catalog until 0) → `python3 scripts/picklist_build.py "<title>" "<Mon D, YYYY>" "<out dir>" [key]` →
`pick_list.{csv,md,html}`. Units per line come from the catalog rule. Publish as a second artifact.

**Sales recap page — offer it** (Juan asked after Sep 19): `python3 scripts/recap.py` (from `work/`, or
`--work <dir>`; needs `rows.tsv`, `notes.tsv`, `ship.tsv`, `segtimes.tsv` and the catalog) → `recap.json` →
paste into `__DATA__` of `recap_template.html` and rewrite the headline tiles, ledes and "what to change" bullets
from the numbers. Show length comes from `segtimes.tsv`. The "by time" dot plot (`runs` in the JSON) follows the
top-revenue product; `--product <substring>` picks another, `--bundle-name "<substr>=<name>"` renames a
multi-product bundle line, `--run-gap` (s, default 300) splits runs. Rows the catalog cannot match are skipped
with a warning: add them first. Load `dataviz` first; chart colour #B5236B light / #D55181 dark.

### 5. Sanity checks before delivering
- `notes.tsv` row count == `rows.tsv` row count; no duplicate ids (`merge` enforces this).
- Sum of item prices ≈ Shipments page "Sales" minus fulfilled orders.
- Every flagged order appears in the top "needs your check" list; no "(not reviewed)" rows (`build` warns).

### 6. Clean up — delete `work/` (Juan asked for this, Sep 21, 2026)
After the artifact is published and the checks pass: `cd ~ && rm -rf ~/whatnot-work/"<Mon D>" "~/Desktop/Whatnot Packing Lists/<Mon D>/work"`
(the real folder and the link; run it from outside). 3-8 GB of segments and tiles. `evidence/` stays. Before deleting, copy any script
you changed in `work/` back into `scripts/` (`cmp` it). Never delete `~/.cache/whisper/ggml-small.en.bin`.
Segments can be re-downloaded from a fresh extension send if a recheck is needed later.

### 7. Report
BLUF: where the files are, counts, then the order numbers that need his check grouped by reason
(quantity / shade not stated / audio vs package / giveaways). Then add a section to `HISTORY.md`: lag,
timings, new buyer spellings, product rules, anything that broke.

## Things that wasted time (don't repeat)
- Cutting tiles before measuring the lag. Sep 11 was +13 s, Sep 23 PM was -10 s.
- Review agents that skip tiles mid-chunk; check the row count when each reports.
- Flagging every certain bundle "qty: seller said 6"; the count belongs in the product text.
- Two lists on one day sharing a localStorage key (`build` now derives `mmdd-2` from the folder name).
- Aloud installed but Agent Speak off (`"reason":"disabled"`): go with defaults and say so.
