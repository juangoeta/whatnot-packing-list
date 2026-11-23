# Review instructions (Whatnot show "{SHOW_TITLE}", {DATE})

Work dir: {WORK_DIR}  (run every command from here)
Your chunk file: chunkN.txt (tab-separated: i shipmentId buyer orderNo listing cents vttStart createdAt).
Purchase time P = vttStart + 30 on the stream timeline. {LAG_TEXT}

{SHOW_NOTES}

HARD RULES (Oct 9, 2026: four agents looped for 43 minutes re-opening the same tiles, 46 times each, and never
reported; a watcher now reads your notes file from disk and stops you if it stops growing):
- Work row by row, in chunk order. Open a tile ONCE. Decide. APPEND THE ROW'S LINE TO notes_N.tsv AT ONCE
  (one `printf '%s\t%s\t%s\t%s\n' ... >> notes_N.tsv` per row, or a heredoc every 2-3 rows), then move to the next row.
- NEVER re-open a tile or frame you already opened, not to "double-check", not at the end. The row is decided
  when its line is written. If you cannot remember a tile, your line for it is already in the file: `grep -P "^i\t" notes_N.tsv`.
- If a Read returns no image, an error, or something you cannot see: do NOT retry it. Run `python3 {RUN} show i`,
  write the row from the transcript with the flag `item: tile did not render` and move on.
- Budget: at most 3 image reads per row (the tile + up to 2 frames). If you reach 2x(rows in your chunk) image
  reads in total, stop opening images: write every remaining row from its transcript with the flag
  `item: not reviewed (read budget)` and finish. Finishing with flags beats not finishing.
- Finish within ~15 minutes. When the file has one line per row, run the wc/grep check in step 6 ONCE and report.

For each row i in your chunk:
1. OPEN THE TILE IMAGE with the Read tool: tiles/NNN.jpg (NNN = i zero-padded to 3). {TILE_TEXT}
   You MUST open every tile, exactly once. Do not skip any row. Do not guess from transcript alone.
2. If a shade/label is small, also open frames/NNN_1.jpg, NNN_2.jpg, NNN_3.jpg: three sharp frames at
   native size from the seconds before the callout (frames/NNN.json has their offsets from P).
3. Read the transcript with:  python3 {RUN} show i j k ...   (several rows per call; run.py is NOT in
   this folder, use that full path). It prints each
   transcript line with its offset from P; ">>" marks lines mentioning the buyer or the listing number.
   Audio covers P-45..P+35.
4. Decide the product: brand, product name, shade/colour/scent (read the package if visible), and QUANTITY.
   Quantity lives in the audio: "bundle of 3", "you're getting two", "two packs", "four of them" -> write it
   into the product text (e.g. "4x Bee Balm Peach lip balm", "2x Beekman 1802 Coconut Cream milk stick").
   Price is a hint: $3-4 items are single pulls; $6-11 items are usually bundles or premium items.
5. Rules:
   - The item on camera just before the callout is the sale unless the callout came earlier than
     usual (then the item shown at the callout time is the sale). If the row's callout offset is far from the measured lag (e.g. the number is said
     30 s early or late), trust the callout time and describe the item on camera then; if that is outside
     the tile, say so in the flag ("callout at P+30, item not in tile").
   - Runs: the seller often sells the same item 5-10 times in a row ("one more, five seconds"). Neighbouring
     rows in time will look identical; that is expected.
   - Buyer's-choice items: the choice is said within ~20 s after the callout.
   - GIVY #n ($0) = giveaway. The item on camera at the draw is NOT the prize. Write what the seller
     says the giveaway prize is if audible, else "Giveaway prize (see show setup)"; flag "giveaway".
   - Items in an opaque bag: record the seller's count ("10 lip glosses") and flag "in bag: N items".
   - Mystery-bag / custom bundles ($8-21, "cannot even close this bag"): the seller lists items for 2-3
     minutes before the sale. Late payments: the callout came minutes before P and the tile shows another item.
     Rows in recut.tsv (i reason from to run clip) already have a longer clip: rc/NNN.wav.txt (its transcript,
     P-200..P+45 for "late", P-300..P+25 for "bundle"; `show i` prints it with offsets) and rc/NNN.jpg (one frame
     every 6 s over that window, 6 per line). List every item from it. When the clip column is "-" (the prep cap
     left it out) or a row you need is not in recut.tsv, do NOT run recut yourself (minutes of whisper that stall
     your chunk): read the tile and the row's own transcript, grep the neighbouring rows' transcripts, write
     what you can, and flag "timing: wider clip needed" so the operator cuts one after the review.
   - If the shade/box is unreadable and not said, flag "shade not stated" rather than guess.
   - Listing numbers cycle/restart; match on buyer + number + time. If the row's number is never
     said in its own audio window, search other rows' transcripts:  grep -il "number N\b" wav/*.txt
     and note it in the flag.
   - The seller says buyer names by voice; whisper misspells them. Spellings seen this show:
     {SPELLINGS}
6. Write results to notes_N.tsv (N = your chunk number), one line per row, tab-separated:
     i<TAB>product text<TAB>flag<TAB>frames
   frames = the tile frame numbers (1-12, comma-separated, up to 3) that show the sold item best, e.g. "5,7,8".
   They become the pictures the seller sees next to the order, so pick frames where the product is held up
   and readable. Leave empty to let the pipeline pick the sharpest frames itself.
   Product text format: "[Nx ]Brand Product, shade/scent, size" - be specific and consistent (read the
   box). Products this seller has sold on recent shows, with the wording to reuse when you see them again:
{CATALOG}
   (more examples: "Maybelline Sky High mascara 800 Blackest Black", "e.l.f. Camo CC Cream Fair 140 W",
   "Beekman 1802 Milk Stick deodorant Glacial Mint & Eucalyptus 2.5 oz").
   flag is empty when you are certain of item, shade and quantity. Otherwise "category: detail", category
   from this list (nothing else is accepted by merge):
     REVISE (the seller must check before sealing):
       shade: ...    shade/scent/variant not readable and not said
       qty: ...      audio and camera disagree on the count ("seller said two, one on camera")
       item: ...     audio and camera disagree on the item, or the pictures do not show it
       cancel: ...   seller said something like "I'll cancel it" / "send me a cancellation"
       missing: ...  no video or audio at the sale (dropout)
     NOTE (shown on the row, not in the revise list):
       giveaway      GIVY rows (the item on camera at the draw is not the prize)
       bag: ...      opaque bag/basket, contents listed from the audio ("bag: 10 lip glosses")
       expiry: ...   a best-by date read off the box
       timing: ...   number not heard, matched by time, item certain
   A count that is already in the product text ("6x ...") is NOT a flag. Do NOT flag rows just because the
   number wasn't said if the item is certain. If NO frame shows the item clearly (opaque bag, item off camera, label unreadable), the row
   MUST carry a flag: the seller revises it. A clear item with clear frames carries no flag.
   Append each row's line right after deciding it (see HARD RULES); never hold rows back to write them together
   at the end. At the end, verify ONCE, without opening any image:
     wc -l notes_N.tsv  == number of rows in chunkN.txt, and every i in chunkN.txt appears exactly once:
     cut -f1 chunkN.txt | sort > /tmp/c$N; cut -f1 notes_N.tsv | sort > /tmp/n$N; diff /tmp/c$N /tmp/n$N
   A missing row: write it from its transcript (`show i`), flagged if you are unsure. A duplicate: delete the
   worse line. Then stop.
7. Final report to me: the count, and a list of any rows you flagged with the reason (one line each).
