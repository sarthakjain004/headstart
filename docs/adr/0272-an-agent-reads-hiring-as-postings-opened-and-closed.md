# ADR-0272: An agent reads hiring as postings opened and closed, not the change in openings listed

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0227](0227-trends-record-each-boards-opened-and-closed-jobs-not-only-its-net.md) (turnover:
postings opened, closed and re-counted),
[ADR-0233](0233-trends-serves-reconciled-line-readings-and-the-page-only-formats.md) (the line
reading the tools report), [ADR-0221](0221-a-refit-is-a-step-in-one-trends-history.md) (the index
view is not netted),
[ADR-0238](0238-a-mostly-re-counted-line-gives-no-percentage-and-hot-hides-only-staffing-and-job-boards.md)
(Hot's Operators), [ADR-0250](0250-a-board-silent-for-two-years-is-dormant-and-leaves-the-tech-subset.md)
(Dormant Boards), [ADR-0143](0143-trends-retain-board-deltas-for-arbitrary-comparable-cohorts.md)
(comparable coverage), [ADR-0267](0267-the-space-hosts-the-mcp-server-at-a-url-anyone-can-add.md)
(the MCP server)

## Context

An independent critic scored the Space MCP server 4.5/10 on 2026-09-29. Its worst finding (P0-1)
was that `read_trends` and `hiring_now` reported re-counting as hiring and stamped every answer
"Figures reconcile." The owner's framing: a journalist-facing tool that is confident and wrong in
sign is the worst failure it can have.

Measured on the live `/trends` on 2026-09-29, newest tick 2026-09-28 21:46:

- **Whole index, 30 days, coverage all:** openings 265,289 → 377,140. `reading.total.move` called
  +111,851 (+42.2%) "hiring", with `not_hiring` empty. Postings opened 17,032 and closed 17,546, a
  net of −514.
- **The same window under comparable coverage** (base at the window's start, as the page sends
  it): the base moves up to 2026-09-13, so the window is 15.4 days long. Openings 332,679 →
  289,543 (−43,136), and turnover nets −1,712.
- The window holds **15 Marked changes, and every one is unsized** (`sizes` empty). A 1-day window
  read −32,336, which is the ADR-0250 removal of Dormant Boards.
- Across the 25 categories of the 30-day view, the sign of a category's "hiring" matched the
  sign of its own opened-less-closed on 11 lines and differed on 14.
- `hiring_now` ranked Bosch Group first at net +439, with 23 postings opened and 32 closed.

**Why the index's changes are unsized.** Netting takes steps out only of a picked company's line.
`netting.py` says so ("With no pick nothing is taken out"), ADR-0221 kept the index view un-netted,
and `check_reading`'s invariant 6 enforces it. A Marked change is sized per company line
(`MarkedChange.sizes`), and with no pick there are no company lines. A Found Board is a sized step
only under a pick (`discovered`), and a Dormant Board's removal is not a Marked change at all. So,
with no pick, the whole change in openings reaches the reading's `hiring`.

**Turnover is recent.** It began at 2026-09-25 18:16 (`turnover_since`), so today it covers only
the last 3.1 days of any window.

## Decision

The owner's decision: **the MCP leads with turnover as the hiring figure.** It reports the change
in openings listed separately, and names the part that neither turnover nor a sized step
explains. The backend (`trends/line_reading.py`, `trends/trend_history.py`) is not changed,
because the website reads it. Everything below is the tools' rendering of fields `/trends` and
`/hot` already serve.

**`read_trends`, for the first row:**

1. "Hiring, as postings opened and closed: 17,032 opened, 17,546 closed, net −514."
2. The gaps in turnover, each said:
   - "counted only from 2026-09-25 18:16 … 3.1 of the window's 30.0 days" (`turnover_since`);
   - the runs it leaves out where a counting change landed (`turnover_left_out`);
   - closures that went uncounted on N Boards, so closed can run low (`closures_unseen`);
   - "closed not counted" where every Board of a line had such a run (`closures_uncounted`).
3. "Openings listed: 265,289 → 377,140 (+111,851)." That change is then split into three parts:
   - what postings opened and closed account for (their net);
   - what the counting changes HeadStart sized account for;
   - **the unsized rest**, which is the change less the other two. It is named as "not a hiring
     figure: HeadStart could not size it". It holds re-counting (Boards found or dropped, duplicate
     postings removed, and the counting changes not sized here), plus any hiring before turnover
     began.

   Where closed was not counted, the change is not split, and the answer says the rest mixes
   hiring with re-counting.
4. **Counting changes, each label once.** Each label is numbered once, with its dates, and a
   cause refers to it by number. When several changes share a label, their sizes are summed. Before
   this, Stripe's causes repeated one clause three times (rc04).
5. **The arithmetic check says what it checks.** "Figures reconcile." is gone. The answer now says
   "The Space's arithmetic check passed: each line's parts add up to its change. It checks sums,
   not that any figure is hiring." A failed check lists its violations.
6. **The site's own "hiring" figure appears only under `detail: full`**, labelled: "The Trends
   tab shows +111,851 (+42.2%, about +26,114 a week) as hiring: the change less the sized steps,
   the unsized change included." A concise answer gives no percentage and no weekly rate, because
   a percentage of the listed change is exactly the misleading headline.

**`read_trends`, for the lines:**

- Each line gives its turnover, then "listed A → B (±D)", its sized re-counting and its unsized
  rest. A line counted for less than 90% of the window says "counted for its last 3.2 of the
  window's 30.0 days". Without that, a per-week figure could exceed the whole change (mr04).
- Lines rank by the size of their opened-less-closed net, not by their change in openings.
- A concise cut says "detail full shows all N".
- A role breakdown's first row is labelled "Watched roles within X, added together (not the
  whole category)". The category's own figures follow, from a second read with the level split
  (cs01 read +8.9% and cs04 read −5.7% for one category).
- A category with no watched roles says so (ec10).

**New `read_trends` arguments:**

- **`coverage`** (`all` | `comparable`). Comparable sends `base` set to the window's start and no
  `since`, as the page's `trendsQuery` does. The answer names the base, and says why when it moved.
- **`measure`** (`openings` | `new`). This is `/trends`' `metric`. `new` is reported as "new this
  week, postings HeadStart first saw in the trailing 7 days", with a note that it is not a count
  of postings opened.
- **`since` / `until`** (dates). `since` overrides `days`, and `until` runs through the end of its
  day.
- The answer always says when the history starts more than a day later than asked (mr04: 365
  days became 48), and when a company is counted only from a later date.

**`hiring_now`:** rows keep the site's order and the site's numbers. Every row adds "(opened less
closed ±N)" beside its net. Three kinds of row are flagged:

- **A net not backed by postings opened:** |net| is more than (opened + closed) × pace. The pace
  is the window's days over the days turnover covers, and never less than 1. Where closed is not
  counted, the test is net > opened × pace. At the current pace of 7.0/3.1 this flags Bosch (+439
  against 55 opened and closed), Algoleap (+100 against 1) and AECOM. It does not flag Meta (+80
  against 73 opened, closed not counted).
- **A Rate row on a small base:** fewer than twice the ranking's 25-opening floor. The flag gives
  how many points one posting moves the rate.
- **A Rate row with more postings opened than are open now.**

The header gives turnover's start, and closing lines count the flagged rows and say what to
report instead. The description defines the Operators from `boards.board_operator`:

- employer: the company itself, and any company not on the curated list;
- services: an IT services firm posting client work it staffs with its own engineers;
- staffing: a staffing agency posting its clients' contracts;
- aggregator: a job board re-posting other companies' jobs.

**The default coverage is `all`**, chosen after measuring the unsized rest (the change less the
opened-less-closed net) on the whole index on 2026-09-29:

| window | all: change | all: net | all: rest | comparable: change | comparable: net | comparable: rest |
|---|---|---|---|---|---|---|
| 1 day | −32,336 | −253 | −32,083 | −46,376 | −534 | −45,842 |
| 3 days | −15,701 | +124 | −15,825 | −48,320 | −1,569 | −46,751 |
| 7 days | −12,341 | −514 | −11,827 | −78,497 | −2,022 | −76,475 |
| 14 days | +36,764 | −514 | +37,278 | −48,709 | −1,777 | −46,932 |
| 30 days | +111,851 | −514 | +112,365 | −43,136 | −1,712 | −41,424 |

- **Comparable does not turn the listed change into hiring.** Its rest is larger in 4 of the 5
  windows. The ADR-0250 removal and the counting changes land inside its cohort too, and it only
  takes out Boards found after the base.
- **The lead figure is complete only under `all`.** Opened already leaves out a Found Board's
  backlog (ADR-0227). Comparable coverage also drops the postings opened on those Boards: over 7
  days it counted 13,603 opened, against 17,032 under all.
- **`all` keeps the window as asked.** Comparable coverage cannot start before 2026-09-13.
- **`all` is the site's default.** Its openings listed are the count the site actually lists.

**The evaluation (`scripts/eval/space_mcp_eval.py`):**

- `trend_sign` now judges the sign of the Space's own opened-less-closed net. It falls back to
  the netted hiring only where a reading has no net.
- It also accepts "flat" for a net within 5% of all postings opened and closed.
- `stated_direction` reads a signed "net ±N" first.
- A new iteration task, t15, asks the whole-index question.
- The sealed held-out file is untouched, but its trend tasks are now judged by this rule.

## Options rejected

- **Change the backend now**, sizing the index's changes in `line_reading`: the website reads the
  same reading, so that is a change to the page's figures and wording, for the owner to take as
  its own step. The consequences below say what it takes.
- **Print the site's "hiring" beside turnover in concise answers too:** a model quotes the first
  large number it is given, and this was the number that read +42%.
- **Re-rank `hiring_now` by opened less closed:** the numbers and the order must match the page.
  A flag carries the correction without changing either.
- **Default to comparable coverage:** see the measurement above.

## Consequences

- **Answers are longer.** A whole-index answer is about 3,300 characters, up from 1,100, most of
  it the counting-change legend. Every answer stays within its tool's budget; `hiring_now`'s
  budget rose to 20,000 characters for 50 flagged rows.
- **The site and the server now use different words for the same numbers.** The page calls
  +111,851 "hiring", and the server calls −514 hiring. Every figure the server prints is the
  site's, or arithmetic on the site's, and `detail: full` prints the page's own figure, labelled.

### What sizing the Marked changes at the source takes

Measured on the HF `data/state` snapshot of 2026-09-28 21:46, loading `TrendHistory` over the
30-day whole-index window:

- Turnover covers the window's last 74 runs, from 2026-09-25 18:16. On every one of those runs,
  the index's change in openings equals opened − closed + (recounted_in − recounted_out), summed
  from the per-Board turnover rows, **exactly** (the largest gap is 0).
- Over those runs the index went from 391,965 to 377,140 openings, −14,825:
  - The runs turnover counts gave +19,268: opened less closed −205, re-counted +19,473.
  - The 20 runs where a counting change landed gave −34,093: opened less closed −44,552,
    re-counted +10,459. The ADR-0250 removal of Dormant Boards landed at one of those counting
    changes and was booked as closed.

So the ledger already sizes every re-count since turnover began. Sizing at the source would take:

1. **Serve `recounted`.** `/trends` already sums it per line and run (`_TURNOVER_KINDS`).
   `line_reading._served` strips each series' turnover from the wire, and `_hiring_turnover` reads
   only opened and closed. A line's re-counted sum, served as a "not hiring" cause, sizes its
   re-counting exactly on the runs turnover counts.
2. **Size each Marked change from its own runs.** A Marked change's size is the whole change on
   the runs it lands on (the change's run, and the run after it that `left_out_runs` also leaves
   out), not only their re-counted part. The ledger books some re-counting there as closed, as
   the Dormant removal shows. With that, the index's and every category's Marked changes get
   sizes, and `check_reading`'s invariant 6 ("with no pick nothing is taken out") must change
   with them.
3. **Leave the runs before turnover unsized, and say so.** No per-id record exists before
   2026-09-25. Sizing those runs would mean netting every company line, a replay of every Board
   (Hot's boot ranking does this for about 2,190 companies). That replay reaches back only to the
   Board-delta ledger's start (2026-09-13), and it still misses Boards that no directory company
   holds. From 2026-10-25, a 30-day window lies wholly inside turnover.
4. **Nothing in this tool changes when the backend does.** Once the backend sizes these, the
   unsized rest this tool reports shrinks to what turnover cannot see, with no change to the
   tool's wording. The page should adopt opened less closed as its hiring figure in the same
   change.

### Risks

- **The pace scaling assumes a steady pace of turnover.** `hiring_now`'s "not backed" flag
  assumes postings open and close at a steady pace across the week. It can miss real hiring from
  before 2026-09-25, and can flag a company whose turnover was unusually quiet in the counted
  days. The scaling stops mattering on 2026-10-02, when turnover covers the whole trailing week.
- **Flag thresholds are judgment, not measurement.** The small-base floor (twice the 25-opening
  floor) and the 5% flat band are both chosen, not measured.

### Tests

- `tests/test_space_mcp_server.py` covers the lead, the rest, the turnover notes, summed causes,
  closed not counted, spans, the window, coverage and measure parameters, refused dates, the
  late start, the comparable base, role breakdowns with and without watched roles, new postings,
  full detail, the flags and the budgets.
- `tests/test_space_mcp_eval.py` covers the turnover-signed verdict.
- `tests/test_space_mcp_against_space_app.py` runs the new wording against the real app.
