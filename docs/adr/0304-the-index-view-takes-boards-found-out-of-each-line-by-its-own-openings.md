# ADR-0304: The index view takes Boards found out of each line by its own openings

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0270](0270-the-index-view-takes-a-counting-change-out-too.md) (its decision 7: found Boards
and duplicate removals stay in the index's lines) · **Relates to:**
[ADR-0143](0143-trends-retain-board-deltas-for-arbitrary-comparable-cohorts.md) (the Board-delta
ledger), [ADR-0185](0185-trends-narrow-to-companies-picked-from-a-directory-of-boards.md) (a
pick's found Boards), [ADR-0227](0227-trends-record-each-boards-opened-and-closed-jobs-not-only-its-net.md)
(Recounted), [ADR-0233](0233-trends-serves-reconciled-line-readings-and-the-page-only-formats.md)
(line readings and their checker), [ADR-0272](0272-an-agent-reads-hiring-as-postings-opened-and-closed.md)
(the MCP server's unsized rest) · **Issue:** #857

## Context

ADR-0270 took counting changes out of the index view (no company picked). It left in the Boards
found later, because a pick sizes them per company and the index had no per-category size.
Measured on the HF trends state (newest tick 2026-09-29 04:04), over the 7 days to 04:10Z:

- **9,149 Boards were first counted after the window's first run, with 68,409 tech openings.**
  All tech roles read +41,771 (+12.5%) as hiring. Its postings opened less closed netted −923
  over the 3.4 days turnover covers.
- **Boards are found on nearly every run:** 143 of the week's 170 runs, 118 of them with 5
  openings or more and 73 with 100 or more. The ten largest runs hold 56% of the openings. A
  pick's rule does not carry over. On a category line, a pick takes the whole run of a found
  Board out, since its size is known only in total, and on the index that would take out almost
  every run. A marker and a line break per run would draw a comb across the chart.
- **The Board-delta ledger already holds the size per category.** A Board's first delta is its
  whole stock at once (ADR-0143), by family and level. Since turnover began on 2026-09-25 18:16,
  the index's Recounted in was 32,767 on the runs turnover counts, and the found Boards' first
  deltas were 30,011 of it, 92%.
- **Duplicate removals are mostly netted already.** Of the 24,607 rows the removal ledger holds,
  21,669 land on a counting change's runs, which ADR-0270 takes out. Elsewhere the removing
  Boards moved −2,348 tech openings at their runs. Of that, −2,310 is one flap: Lockheed
  Martin's Eightfold front is re-added one run (+728, booked as opened) and removed the next, four
  times in the week.

## Decision

1. **The index takes each run's Boards found out of every line by the openings they brought it.**
   `TrendHistory` keeps every Board's rows at its first tick (`_found_rows`), except at the
   ledger's own first tick, which is every Board's baseline. For an index question under All
   coverage and Openings, `unnetted_answer` sums them per charted run and per line, as the view
   names its lines: categories, a category's levels, or its watched roles. They arrive in
   `discovered` with `lines`, beside a pick's found Boards. A Board lands on the first charted
   run at or after its first tick, as a pick's does, and an ATS selection narrows the Boards.
2. **Netting lifts each line by its own size.** A note with per-line sizes moves only the lines it
   reached; the first row's size is their sum (`netting.note_size`). The lift keeps the run's
   ordinary hiring in. On a run where a counting change also lands, the found openings are the
   Boards', and the rest of the jump is the change's: the Sep 28 08:26 filter change now reads
   −44,596 on All tech roles, not −41,286, because 3,310 openings found on its settling run were
   in it. A run whose Boards brought under 5 openings in all is left in, as for a pick.
3. **They are one Marked change, drawn nowhere.** "9,149 more job sites found through Sep 29" is
   dated at the first run it takes out and sized on the first row. It stands in every line's "Not
   hiring" and in the Marked changes list. It gets no day marker and breaks no line
   (`MarkedChange.drawn` is false, and `steps_at` skips runs where only found Boards landed). The
   checker, in Python and on the page, names a drawn change by exactly one day marker and one not
   drawn by none. A window holds this change only while it holds every run it lands on.
4. **A cut line's growth is a counting change's, never found Boards'.** A cut is where a step
   takes out more than the history before it held. It is laid on the nearest change at or after
   the cut, and a run where only found Boards landed is skipped, since they add openings.
5. **Duplicate removals stay in the index's lines.** On the index they either land on a counting
   change's runs, already netted, or they are the Lockheed flap. Netting that removal alone would
   turn its re-adds into +2,310 of hiring.
6. **What still is not netted, and why.**
   - *Comparable coverage* holds no Board found later.
   - *New* is unchanged. From 2026-10-02 it is the week's Opened jobs, which never hold a found
     Board's backlog (ADR-0230 decision 5).
   - *Windows before 2026-09-13 12:00* carry unsized found Boards: the archive before the
     Board-delta ledger has no per-Board rows.
   - *Other re-counts*: Boards dropped, Boards read differently, and the removals of decision 5.
     The Sep 22 runs show them: SmartRecruiters Boards added 12,163 openings on one run, and
     Oracle Boards dropped 17,004 two runs later.
7. **The page and MCP say so.** The index's Change caption no longer says that adding companies
   lifts every line. MCP's `read_trends` lists what its unsized rest can hold on the index as
   Boards dropped or read differently and duplicate postings removed. It adds Boards found before
   per-Board counting began only where the window starts before it. The page's `isYoung`, which
   read every index line "too new" in a window under 3 days, now reads no line of the index as
   young.

## Measured

On the HF state above, windows ending 2026-09-29 04:10Z (the Space read the same state):

| Line | Window | Before (ADR-0270) | After |
|---|---|---|---|
| All tech roles | 7 days | +41,771 (+12.5%) | −5,166 (−1.4%); found +68,409 |
| All tech roles | since 2026-09-13 12:00 | +66,977 (+21.6%) | −5,312 (−1.4%) |
| All tech roles | 21 days | +119,862 (+46.6%) | +47,573 (+14.4%): the archive days before 09-13 |
| Software Engineering | 7 days | +3,467 (+5.3%) | −5,639 (−7.5%); found +13,006 |
| Software Engineering | 21 days | +24,395 (+54.1%) | +9,386 (+15.6%) |
| Embedded & Firmware | 7 days | +584, mostly re-counted | −1,390, mostly re-counted |
| All tech roles, comparable | 7 days | −6,751 (−2.1%) | unchanged |

Software Engineering's −5,639 holds −4,071 from the Sep 22 re-reads of decision 6.

Across 160 index views (both Measures, comparable, a Workday-only view, every category's levels,
three role drills, 1 to 21 days and All), no reading fails the checker. Hot's payload, 300 rows,
is identical before and after. The 150 largest companies' readings are identical but for the new
`drawn: true` on each Marked change. Every existing golden reading changed only by that field.

## Alternatives considered

- **One found change per run, drawn as a pick's is.** 118 markers in a week, and a line broken at
  nearly every run.
- **One found change per day.** A marker every day would bury the counting changes the markers
  exist for.
- **Net the index's duplicate removals too.** See decision 5: it adds error.
- **Read hiring as opened less closed (ADR-0272's suggestion for the page).** Turnover covers only
  the runs since 2026-09-25 18:16. It stays the owner's call.

## Consequences

- The index's All-openings lines now read close to its comparable cohort's over the same days.
  Over the week, the first row reads −1.4% against −2.1%.
- `discovered` can hold index entries, with `company` None and `lines`. A Marked change gains
  `drawn`.
- Each index answer sums its window's found rows: about 30,000 rows for a week, read once per
  question. The 160 views took 11 to 15 s, against 10 s before.
- The Lockheed Martin flap inflates the index's opened by about 530 each cycle, which is a data
  fault of its own (#887).
