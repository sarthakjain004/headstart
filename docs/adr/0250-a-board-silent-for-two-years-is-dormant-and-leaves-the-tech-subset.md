# ADR-0250: A Board silent for two years is Dormant, and its Jobs leave the Tech subset

**Status:** accepted · **Date:** 2026-09-28 · **Relates to:**
[ADR-0017](0017-tech-role-filter.md) (the post-hoc tech gate),
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (the Unauthoritative Board),
[ADR-0083](0083-evict-only-on-a-second-consecutive-absence.md) (the Unconfirmed grace period),
[ADR-0161](0161-the-eviction-scope-travels-as-board-keys.md) (`scrape_join` records what only it can see),
[ADR-0229](0229-the-slice-reads-every-tech-yielding-board-and-rotates-the-rest.md) (the head),
[ADR-0243](0243-a-row-the-scrape-saw-is-in-scope-whatever-its-boards-scope.md) (a row the scrape
saw is in the eviction scope) · **Issue:** #570

## Context

An ATS serves a posting until someone closes it, and nobody closes anything on a Board nobody
tends. #570 found SmartRecruiters Boards whose newest posting is from 2017, mostly IT-staffing
agencies that stopped posting within the same few days. SmartRecruiters still serves every one of
their postings as open: detail `active: true`, an `applyUrl`, a posting page that answers 200.

Measured on served table v448 (2026-09-28, 533,799 rows, read straight off HF):

* **55,101 rows (10.3%) sit on 3,896 Boards whose newest posting is more than two years old.**
  SmartRecruiters holds 47,163 of them on 1,701 Boards; its 2017 cluster alone is 285 Boards and
  35,533 rows, and `smartrecruiters:SonsoftInc` carries 6,476. The rest spread over Zoho (3,231),
  ADP (1,064), Freshteam (855), BambooHR (431) and a long tail.
* **They crowd the searches they match.** Search ranks by relevance with no recency weight, and
  the Posted filter defaults to *Any time*. Rows on these Boards are 40.3% of the rows whose title
  says Java, 40.6% for .NET/C#, 37.7% for Oracle and 33.6% for Business Analyst. Modern stacks are
  barely touched (data engineer 2.7%, machine learning 1.3%).
* **They lead the scrape.** Priority is a Board's tech count, so SonsoftInc was #2 of the whole
  priority ledger, behind Amazon, and 15 of the top 100 were such Boards. They took 27% of
  SmartRecruiters' measured Board-time.
* **Nothing removes them.** A random 40 of the small ones, across 13 ATSes, all answered 200 with
  no closed or expired text. They never leave their Board's listing, so `index sync` never sees
  them go.
* **The case #570 asked to protect**, a small company that keeps one opening up for years, is the
  long tail: 1,922 of the 3,896 Boards hold a single served row (1,922 rows, 3.5% of the 55,101),
  and 1,198 more hold two to five (3,434 rows). 77 Boards of over a hundred rows hold 37,566. The
  40 small ones read were roles dated 2018-2024 and a few placeholders ("Test", "Copy of Senior
  Armourer"), all still served as open. The two-year window is for them: a Board that posts once
  a year is never Dormant.

## Decision

1. **A Board is Dormant when its newest posting is more than 730 days old** on the day of the run
   (`board_dormancy.DORMANT_AFTER`; a newest posting exactly 730 days old is not Dormant). It is
   judged only on evidence. A Board whose scrape this run was not authoritative is not judged,
   since a truncated list may have lost the newest page. A shortfall ADR-0121 tolerates (a read
   of at least 99%) still counts as authoritative; if the Job it missed was the only recent one,
   the ADR-0083 grace period holds the rows until the next complete read clears the verdict. A Board with any posting that has no
   usable date is not judged, since an undated posting may be last week's. A usable date is an
   ISO `posted_at` on or after 2000-01-01; Keka's `0001-01-01` and `1900-01-01` placeholders read
   as undated. Two more cases read as undated, because the lines cannot show the whole Board. One
   is an id on no live Board, which resolves through `board_of`'s guess and so can split a
   colon-bearing native id into a phantom Board of one Job. The other is any Jibe Board, whose
   scraper drops each posting an iCIMS Board already serves before it becomes a line (ADR-0240).
2. **`scrape_join` judges every Board**, reading each line's `posted_at` off the parse the union
   already does. It judges from the full scrape, non-tech postings included, because a company
   posting sales jobs this month is hiring, and its older tech opening may be real. The verdict,
   each Dormant Board with its newest posting date, goes to `data/state/dormant_boards.json`. It
   rides the corpus-state artifact to the merge job, and lands on HF as a record of which Boards
   were Dormant (about 200 KB).
3. **`filter_tech` leaves every row on a Dormant Board out of the Tech subset**, before the gate
   judges it. Its report counts these apart from the non-tech rows. With no readable verdict it
   warns and leaves nothing out, which is the Tech subset as it was before this ADR. `index sync`
   reads the same verdict and names a Dormant Board's newly Unconfirmed rows on a line of their
   own, instead of among the Boards that "returned no tech Job this scrape", and names them again
   on the run that evicts them.
4. **Eviction is unchanged.** The Board was scraped, so it is in the eviction scope, and its rows
   are missing from the Tech subset. Their first absence makes them Unconfirmed, and the next
   scrape of the Board evicts them (ADR-0083). The priority ledger's EWMA (0.7 on the latest run)
   leaves the Board scored after one zero-tech run, so it is read again next run and the
   eviction lands one run after the verdict. It stays in the head until its score falls below the
   ledger's 0.05 floor, about ten runs for SonsoftInc's 6,476 and three for a Board of one, and
   then waits in the Tail. It is never parked: it keeps being scraped, so a Board that posts
   again is not Dormant on its next scrape.
5. **`TECH_FILTER_VERSION` goes to 6.** A counting change leaves its run and the next out of
   every Trends line, which covers both scrapes the eviction takes. Without it, Trends would read
   ~55,000 evictions as the IT-staffing market collapsing.
6. **The curated feed (`python -m headstart`) is unchanged.** It keeps no record of which scrapes
   were complete, so it cannot apply the evidence rule, and it passes no Board to leave out.

## Alternatives considered

* **A per-posting age cutoff.** At two years it would also remove 12,687 rows on Boards that
  posted within the last year, and some of those are real, current openings with an old date:
  Databricks' "Senior Software Engineer - Database Engine Internals" (2021), Netlight's "Software
  Engineering Consultant (2026/27 Graduate)" (2021), Point72's rolling ML intern req. Rejected.
* **Mark the Board non-live in the liveness ledger, so `prune` evicts it outright** (#570's
  option B). The liveness probes do not read posting dates, so each of ~39 probes would need
  teaching. A Board off the ledger is never scraped again, so it needs its own path back when it
  revives. `PARKED_BOARDS` is a hand-kept list and cannot hold 3,896 Boards. Rejected.
* **Hide the rows at search time** (a per-row flag, excluded by default). It is fully reversible,
  but the rows keep their storage, the Boards keep leading the scrape, and Trends and the company
  directory keep counting them. Rejected.
* **A new pipeline step after `filter_tech`.** The owner chose to keep the rule inside the
  existing stages. A new step would also have rewritten the Tech subset a second time on the join
  job's critical path.
* **Judge inside `filter_tech` itself, with a second pass over each file.** That honours the same
  choice, but it is a second full parse of ~2.1M rows on the critical path, and `scrape_join`
  already parses every line. `tech_filter.filter_jobs` is also reachable from the curated feed,
  which must not import `ingest`. So `scrape_join` judges, and `filter_jobs` only takes the set of
  Boards to leave out.
* **Another threshold.** One, two and three years remove 60,571, 55,101 and 52,505 served rows.
  The answer barely moves, so the longer window, which protects a company that posts rarely, costs
  little.

## Live verification (2026-09-28)

24 real Boards were scraped live through `scrape_run`'s shard mode, then joined and filtered by
this change's `scrape_join` and `filter_tech` against the committed liveness ledger. There were
18 candidates whose served rows were all over two years old, across 10 ATSes, and six controls
that still post. Those were Bosch and Nagarro on SmartRecruiters, Databricks, Netlight, an active
Zoho Board, and a Keka Board carrying placeholder dates.

* All 24 scrapes were complete (no Unauthoritative Board). **16 Boards were judged Dormant**,
  with 10,656 scraped lines, and `filter_tech` left out exactly those 10,656. SonsoftInc's full
  listing was 6,519 postings, newest 2017-09-14.
* **Two candidates were kept, correctly.** Teamtailor's `quorso` had a posting from 2026-08-07:
  the served table held only its one old tech row, and its full listing shows it hiring. Workday's
  `nuskin/nuskin` had 11 undated postings of 13, so it is not judged. That is the evidence rule
  holding, and it means Workday Boards whose listings carry no dates are left in.
* **No control lost a row.** Each kept exactly the tech rows it keeps without this change.
* Two consecutive `plan_sync` passes over the real served ids for these Boards made all 10,369
  served rows on the Dormant Boards Unconfirmed on the first, then evicted them on the second.
  The only other deletions were 9 Bosch postings missing from the day's full scrape, which are
  ordinary closures.
* The judgement run over all of served v448 finds 3,871 Dormant Boards and 54,661 rows. That is
  440 fewer rows than Context counts: 419 on 22 Jibe Boards, never judged, and 21 on three Boards
  (one SmartRecruiters, two Zoho) that hold an undated row. It sees tech rows only, so it is the
  upper bound.

## Consequences

* The served rows on Dormant Boards leave the index over the two runs after this ships. 54,661 is
  an upper bound from the served table, whose rows are tech only; a Board's non-tech postings and
  the evidence rule keep some Boards in.
* **A revived Board brings its old postings back.** If SonsoftInc posts once, its 2017 postings
  are served again with it. For a Board silent since 2017 that is unlikely enough to accept rather
  than add a second rule.
* **A Dormant Board is still scraped**, in the Tail rotation rather than every run. The
  SmartRecruiters ones cost ~1.4 h of Board-time per full read. ADR-0242's back-off does not
  reach them, because their listings are not empty. Backing them off too is a possible follow-up.
* **A Board that cannot be judged keeps its rows, and may churn.** An Unauthoritative scrape, a
  lost verdict, or one Job whose date depends on a detail fetch that failed (Workday's
  `posted_at` is the detail's `startDate`) leaves the Board's rows in the Tech subset for that run. Rows already evicted are
  then added and embedded again, and evicted two scrapes later. On 2026-09-28, 10 of the Dormant
  Boards (294 served rows) were scope-excluded, almost all on every run (`freshteam:abnhire` 310
  runs in a row). Those are never judged, so they never churn, and their rows stay until they
  scrape clean. Carrying the verdict forward across runs would close the gap, and was not worth
  its state at this size.
* **Trends books a later Dormancy as Closed.** Only the run that brought in the rule and the one
  after it are left out. A Board that crosses the two-year line later has its Jobs booked as
  Closed, a small, steady flow.
* **`fanout_corpus`'s per-ATS kept% steps down once**, mostly on SmartRecruiters, because its
  denominator still counts every scraped row. It is the new baseline, like any `TECH_FILTER_VERSION`
  bump.
* #570's other two findings are not addressed here: the `function.label` fallback's non-software
  "Engineering" rows (option A) and the plural trade titles `_NON_SOFTWARE` misses (option D).
