# ADR-0205: An Eightfold career site whose backing ATS Board already serves it is an alias

**Status:** accepted · **Date:** 2026-09-24 · **Relates to:** [ADR-0017](0017-tech-role-filter.md) (the tech gate decides what is served), [ADR-0111](0111-duplicate-boards-resolve-the-board-surface.md) (the alias ledger, and the `_EIGHTFOLD_ALIAS_LOSERS` it kept), [ADR-0182](0182-a-clearcompany-board-is-its-hrm-direct-feed.md) and [ADR-0186](0186-a-taleo-section-another-section-already-lists-is-an-alias.md) (the two writers this one follows), [ADR-0187](0187-a-workday-requisition-is-served-once-per-tenant.md) (the Workday requisition id), [ADR-0188](0188-a-dedup-rule-change-is-a-trends-epoch.md) (the Trends epoch)

## Context

An Eightfold career site is often a front over the company's real ATS. `jobs.nvidia.com` lists
the requisitions of NVIDIA's Workday site; `arcadis.eightfold.ai` lists Arcadis's Oracle ones.
Both Boards are scraped, so the posting is served twice under two ATS labels. Their native ids
never overlap and `index_plan.evict_duplicate` groups within one Board, so nothing sees the pair.
Served index v654 (2026-09-23) holds 11,914 rows on 35 Eightfold Boards paired this way with a
Workday, SuccessFactors, Oracle, Taleo Enterprise or Greenhouse Board (found by exact shared
descriptions; the pair list is the candidate table below).

Measured live on 2026-09-24 before deciding:

- **Every Eightfold posting states the backing ATS's own requisition id.** The PCSX search carries
  `atsJobId`/`displayJobId`, SmartApply `ats_job_id`/`display_job_id`. They match the Workday
  requisition id (the native id, ADR-0187), Oracle's requisition `Id` (`displayJobId`),
  Greenhouse's `internal_job_id`, Taleo Enterprise's `contestNo` (not its `jobId`), and the
  `"internalId":"49983-en_GB"` every SuccessFactors RMK job page states.
- **The id match is strong.** Recall on postings known to be held (Eightfold rows on v654 whose
  exact description sits on a backing row, still listed): **9,690 of 9,694 (99.96%)**. The
  critique's earlier title search found 88% on Workday and 67% on SuccessFactors.
- **An every-posting bar flaps.** The two listings are read minutes apart and postings propagate
  between them: micron's unmatched count read 2, 20 and 0 on three reads that day. Strict
  containment passed 18 of 35 Boards; 14 more were short by 1 to 32 postings each, at most 1%.
- **Listed is not served.** 1,061 v654 rows sit on an Eightfold Board whose backing Board lists
  the requisition but serves no row for it. The backing copy carries its own department, and
  `tech_filter` reads it: Eightfold's department marks the posting tech, the backing Board's does
  not, so only the Eightfold copy is in search.

## Decision

**An Eightfold Board is buried onto its backing Board(s) in the ADR-0111 alias ledger,
`data/validate/aliases/eightfold.csv`, signal `backing-reqs`, when all of these hold** (the
user's decisions of 2026-09-24):

1. **Every tech posting has a backing copy the tech gate keeps.** Zero tolerance. Tech is
   `tech_filter.is_tech` on each Board's own listing fields: Eightfold's title and department,
   Workday's title and `jobFamilyGroup`, Greenhouse's title and department, Taleo's title and
   listing department, Oracle's and SuccessFactors' title (their listings carry no department).
   The served index is not read.
2. **At most 1% of its postings have no backing copy at all**, none of them tech (rule 1). The
   margin is what stops the flapping: micron's 2 → 20 → 0 is under 1% of 2,979 every time.
3. **"No backing copy" is judged against the backing Board's own listing walk**, the walk the
   pipeline scrapes, never a targeted search. Citi's 27 postings that a Workday search by id finds
   and the walk does not return are not served, so they count as missing.
4. **Every backing Board is Scrapable and was read; the Eightfold listing was read whole.** A
   truncated or failed read earns no verdict, so the Board is not buried that run.
5. **A multi-site company is buried onto every site that holds its postings** — hp onto two
   Workday sites, costar onto three — one ledger row per backing Board. Rule 4 applies to each.

**Cross-ATS and multi-canonical rows need no format change.** `canonical` holds the backing
Board's lowercased `board_key`, `ats` prefix included, and a Board with several backing Boards has
one row per backing Board. `board_aliases.load` keeps one canonical per duplicate, and every
consumer only tests membership.

**The six `_EIGHTFOLD_ALIAS_LOSERS` join the ledger** as candidates backed by their winner
(`nvidia.eightfold.ai` → `jobs.nvidia.com`, and so on). They are decided after it and follow it:
onto the winner's backing Board when the winner is itself buried, else onto the winner. Their
liveness rows are `dead` and stay so, because `check_liveness` skips a buried Board unprobed
(ADR-0111). **The hand list stays**, because the ledger does not fully replace it: its verdict needs
a whole read of the pair and the list's does not. Both runs of 2026-09-24 missed qualcomm (a short
sweep of `careers.qualcomm.com`, then a failed connection), and a loser out of the ledger would be
re-probed after `liveness.DEAD_TTL_DAYS`, found live, and scraped as a duplicate until the next
clean run. Retiring the list needs the writer to keep a loser's burial through an unread run, a
rule this ADR does not make.

**The writer is `scripts/validate/eightfold_backing_boards.py`, run by hand** after every refresh
of the eightfold ledger or of a ledger its candidates read, the footing of ADR-0182 and ADR-0186.
Its election, `aliases`, is pure; its readers are injected. It reads each Board once, 16 at a time,
every read sequential within its Board, so at most 16 requests are in flight. It replaces the file
each run and re-reads the Boards the last run buried, so a Board whose backing Board drops out, or
that starts posting on its own, comes back on the next run. `dedupe_boards.py --apply` refuses
eightfold, and now also refuses any ATS whose ledger holds a row whose signal is not `redirect`,
which closes Jibe's hand-written row.

**The candidates are a table in the script (`BACKING`)**, the 35 content-matched pairs less two:
Lumen, whose backing site is an internal careers site (the user's decision), and International
SOS, which lists postings of its own. A new front enters by adding it there. Two scraper changes
carry the ids through: SmartApply's shape keeps `ats_job_id`/`display_job_id` under the PCSX names,
and the Taleo Enterprise listing row keeps `contestNo`. Neither is read by `parse`.

## Evidence

Run on 2026-09-24 against the committed ledger (80 Boards read):

| | Boards | v654 rows removed |
| --- | ---: | ---: |
| buried, cross-ATS | 3 (curriculumassociates, twilio, vizientinc) | 113 |
| buried, second Eightfold site | 5 of 6 (qualcomm's winner read short) | 0 (their rows are dead) |
| kept by rule 1 | 30 | |
| kept for an unread Board | 1 (qualcomm) | |

A second run the same day, stopped before its last Board (sephora) after Eightfold walled the
machine and four reads failed, elects the same 8 from the 79 Boards it read.

**Rule 1 decides almost everything.** Tech postings with no backing copy the tech gate keeps, per
Board, from the same run: nvidia 19 of 2,061 tech, micron 29 of 1,959, amat 108 of 994, caci 129 of
985, citi 154 of 1,087, ngc 64 of 1,813, hp 85 of 306, vodafone 96 of 438, nab 62 of 181 — no
Board but the three above reads 0. Aliasing those Boards would take that many tech postings out of
search. On v654 the three buried Boards leave **0** tech rows without a served backing copy.

**The tech check agrees with v654** on 33,254 of 34,591 backing postings (96.1%). By listing verdict
× has a v654 row: tech and served 11,301, non-tech and not served 21,953, non-tech but served 408
(211 of them on Oracle's `ebcs`, whose department is detail-only, so the check reads title alone),
tech but not served 929. The 408 err toward keeping an Eightfold Board. The 929 err toward burying
one, and are an upper bound on disagreement: v654 is a day older than the listing, and a Board not
in its run's slice has not had today's postings added.

## Alternatives considered

- **Strict containment, as first specified.** 18 Boards, 7,063 v654 rows, and it flaps: the same
  Boards read 0 and 20 missing postings hours apart.
- **Listed-only containment (rules 2–4 without rule 1).** Buries 22 Boards and 4,745 v654 rows on
  the same run, and takes tech postings out of search: across all 35 pairs, 1,061 v654 rows have
  no served backing copy. The user required zero tech loss.
- **A tech tolerance.** Rejected by the user: any tech posting lost is a posting nobody can find.
- **Carry Eightfold's department to the backing Board's tech verdict**, so its copy would be
  served. That is a change to what is served, not a dedup rule, and it would need a cross-ATS join
  at scrape time; out of scope.
- **Mark losers `dead`**, as `dedupe_eightfold_aliases.py` did. ADR-0111 already rejected it:
  they are not dead.
- **Run the writer in the pipeline.** A pipeline change; the manual footing matches the other
  two writers.

## Consequences

- **The effect is small until rule 1 is revisited.** 3 cross-ATS Boards, 113 v654 rows, of the
  11,914 v654 rows on the 35 candidates. The table above says which Boards a department-aware
  tech verdict would release.
- **Scrapable Board** and **Hiring Board** fall by 3 each (the losers' rows are already dead).
- **The Trends chart marks the tick:** `backing-reqs` is a new alias signal, so this change bumps
  `index_plan.DEDUP_VERSION` from 3 to 4 (ADR-0188).
- **Oracle and SuccessFactors backing copies are judged on title alone**, because their listings
  carry no department while the served row gets one from the detail page or the feed. None of the
  three buried Boards has such a backing Board; a future one would lean on the 929-posting upper
  bound above.
- **The same gap as ADR-0186:** a burial is bounded by how often the writer runs, not by time.
- **A run takes about 90 minutes**, most of it reading SuccessFactors job pages one at a time
  (sephora's 1,900) — the only way to read their requisition ids.
