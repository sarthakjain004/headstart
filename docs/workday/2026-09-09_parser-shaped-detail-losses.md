# Workday's parser-shaped detail losses — 2026-09-09

Runs `34312743097`, `34316866965`, `34321068300`, `34327339789`. **All four are on head SHA
`fd15455`** (verified per run via `gh api .../actions/runs/<id> --jq .head_sha`), so nothing below
is a code-shipped-between-runs artefact.

The question: Workday's detail pass reports `N of M detail(s) failed mid-crawl`, and two of its
loss classes are *parser-shaped* rather than network-shaped — `no externalPath` (no detail URL to
fetch) and `unparseable` (a 200 whose body is not JSON). Neither is covered by a retry ladder or
the spare-egress fallback. HTTP 500, the largest class, is included because a 5xx driven by
request *shape* would be fixable where host flakiness would not.

**Verdicts up front.** `no externalPath` is inherent Workday listing behaviour, unrecoverable, and
**costs nothing downstream** — only its *reporting* was wrong, and that is what changed.
`unparseable` is ~1 in 40,000 and sits below what any probe run here could detect; no change.
HTTP 500 is host health, already bounded by ADR-0100's breaker; no change.

## 0. The brief's own figures did not reproduce — read these instead

The investigation was commissioned quoting 866 class-occurrences, split `no externalPath` 139 /
`unparseable` 41 / HTTP 500 381. Recomputed from the same cleaned log cache by two independent
paths that agree exactly (the four per-run directories, and the concatenated `ALL.txt`):

| class | postings | log lines | Boards |
|---|---|---|---|
| skipped after the 5xx break-off | 969 | 6 | 6 |
| **HTTP 500** | **745** | **366** | **301** |
| ConnectionError | 410 | 163 | 129 |
| **no externalPath** | **335** | **174** | **125** |
| HTTP 429 | 151 | 79 | 57 |
| HTTP 403 | 111 | 45 | 34 |
| **unparseable** | **50** | **48** | **48** |
| HTTP 404 | 30 | 7 | 4 |
| HTTP 522 / 200 / 520 / SSLError | 12 | 10 | 10 |
| **total** | **2,813** | **747 lines** | — |

`no externalPath` is 25% *larger* than the brief said, not smaller. Both counts of a class matter
and they are different questions: a **log line** is one Board's pass, a **posting** is one lost
detail, and the `xN` multiplicities separate them.

Note the "log lines" column sums to 898 against 747 lines in total — not an error. One line names
every class its Board lost to (`2 of 1858 detail(s) failed mid-crawl (unparseable x1, HTTP 429
x1)`), so a Board that lost to *k* classes is counted once in each of *k* rows. The 747 is the
count of distinct lines; the column counts line-appearances per class.

**The denominator is the number that reframes all of this.** `[scrape_join] workday.jsonl: 500216
lines from 15 shard(s)` — so ~500k Workday **Jobs** per run, ~2.0M detail attempts across four.
(*Jobs*, not *postings*: CONTEXT.md reserves "posting" for the raw record an ATS returns, which is
the right noun for a listing item but not for a normalized `workday.jsonl` line.)
Against that, the entire loss column is **0.14%**, `no externalPath` is 0.017%, and `unparseable`
is 0.0025%. The 866-lines framing reads as an incident; the rate does not.

## 1. `no externalPath` — the listing serves a requisition id and nothing else

### What the item actually is

Walked `accenture/avanadecareers`'s full listing live (610 reported, 605 read): **27 postings carry
no `externalPath`**, and every one of them has exactly one key.

```json
{"bulletFields": ["R00322521"]}
```

No `title`, no `locationsText`, no `jobFamilyGroup`, no `postedOn`. **This is not the scraper
reading the wrong field** — the hypothesis worth ruling out, given `externalPath` is also load-
bearing for `_vouched_by_url`'s `bulletFields` selection — because there is no other field to read.

### It cannot be recovered

Three independent attempts, all negative:

| attempt | result | n |
|---|---|---|
| listing `searchText: "<req id>"` | returns `total: 1` and **the same stub** | 3 |
| CXS detail addressed by req id (`/job/{id}`, `/jobs/{id}`, `/job/x/{id}`) | 404, 406, 404 | 3 shapes |
| the board's own `siteMap.xml` (607 URLs, found via `robots.txt`) | **0 of 27** stub req ids present | 27 |

The sitemap result is the decisive one: Workday publishes 607 job URLs for this board and none of
them is a stub. These requisitions are not linkable, not renderable, and not addressable. They
exist only as an id inside the listing envelope.

*(Aside worth keeping: `robots.txt` named four sitemaps, and the path is `siteMap.xml` — a
lower-case `sitemap.xml` returns the SPA shell at 200. The "check robots.txt before concluding an
ATS has one listing surface" habit paid again here.)*

### It costs nothing downstream — the brief's stated cost is wrong for this class

The brief framed the damage as "ADR-0021 null fields on the emitted Job, and an ADR-0050
description-store gap entry … a Job served with no description". Traced through the real code, a
stub does not get that far:

```
parse()            -> Job(title="Untitled", url=<board root>, description=None, id=…:R00322521)
tech_filter        -> classify("Untitled", None) = Verdict(is_tech=False, reason='no-tech-signal')
filter_tech        -> dropped; never written to data/jobs/tech/
update_descriptions-> reads data/jobs/tech/ only, so no ADR-0050 gap entry either
index              -> never sees it
```

So there is no served row, no null-field row, and no description-store gap. The cost is zero.
Two secondary properties are worth recording because they *could* have made it non-zero:

- **The id is stable.** `_posting_key` falls past `_vouched_by_url` (no path to vouch with) to the
  shape tier, where `R00322521` matches `_REQ_ID_SHAPE`. So these do not churn ids run to run —
  ADR-0097's fix already covers them.
- **The bad URL never ships.** `parse` falls back to `url=<board root>` when `external_path` is
  empty, which would be a user-visible dead link — but only for a Job that passes the tech gate,
  and a stub cannot, because its title is literally `Untitled`. If a tenant ever serves a stub
  *with* a title, this becomes a real serving defect — so the scraper now **counts titled stubs
  and warns**, rather than leaving the assumption undefended (§What changed).

### Two populations, not one

| | Boards | shape |
|---|---|---|
| chronic (all 4 runs) | **1 of 125** | `accenture/avanadecareers` — 29/588, 24/589, 28/598, 30/576; ~5% of the board, every run |
| intermittent (2-3 runs) | **34 of 125** | 1-6 postings per run, e.g. `medtronic`, `db/DBWebsite`, `citi/2` |
| one run only | **90 of 125** | typically 1-3 postings on a 1,000-4,400-posting board |

The live sweep confirms the split. Re-walking 22 of the 125 Boards — **31,028 postings read** —
found **38 stubs on 6 boards**: avanade 27, `walmart` 3, `ag/Airbus` 3, `cnx` 2, `thales` 2,
`mastercard` 1. Meanwhile `db/DBWebsite` (6 logged), `citi/2` (6) and both `medtronic` boards
(6 each) show **0 stubs now**. So outside avanade this is a momentary listing-index state, not a
tenant property.

**Every one of the 38 carried `bulletFields` and no other key, and `with_title` was 0 on all of
them.** That is the measurement the code's carve-out rests on, and it is why the scraper now counts
titled stubs separately and *warns* if one ever appears (22 of 125 Boards is a sample, not the
population — this repo has been bitten before by generalising one sample into a documented fact).

### What changed

`_report_detail_losses` now pops `_NO_DETAIL_URL` out of the mid-crawl tally and reports it on its
own line — the same mechanism `_PAGE_RECOVERED` and `_COOKIE_RECOVERED` already use, and for a
sharper reason: those are popped because they are *not losses*; this one is popped because it is
not a *fetch failure*. No request is made, so neither the retry ladder nor the spare-egress
fallback was ever in play, and reporting it as though they were is what pointed this investigation
at a network fix for a listing-side artefact.

The count is subtracted from `missing` as well as popped from the tally, or the difference
resurfaces as `unclassified` — an unnamed loss, which is exactly what the tally's invariant exists
to prevent.

**The line claims only what the scrape establishes.** An earlier draft had it assert that the Job
"is dropped at the tech gate, not served short" — true on the 22 boards swept, but a claim about
*other modules* generalised from a sample to all 125 Boards, and asserted per Board in a scrape
log. That is precisely how this repo has previously turned one sample into a documented fact. The
line now says only that the listing gave no detail URL so none was fetched, and points here. The
downstream consequence is defended by a **tripwire instead of an assertion**: a posting with no
`externalPath` but *with* a title is counted separately and logged at WARNING, because that is the
one shape that could pass the tech gate and ship the board root as a job link. The subtraction cannot go negative: both note sites return `None` immediately, so every
counted stub is one of the `None`s being subtracted from, and the threaded `fan_out` fallback's
racing `Counter` increments can only *under*count.

One concrete consequence beyond tidiness: `missing / len(details) > _MAX_LOST_DETAIL_SHARE`
escalates the line to `WARNING`, which under Actions is an annotation against a run-level quota
(ADR-0039's 2026-09-08 amendment). A Board whose only losses are stubs crossed that threshold and
raised an annotation for a pass in which **no request had failed at all** — reproduced in a test
against the unfixed code.

## 2. `unparseable` — real, but below detection

50 postings across four runs against ~2.0M detail attempts: **~1 in 40,000**, ~12 per run, spread
**1 per board across 48 different boards**. That spread is itself informative — an anti-bot
interstitial or a per-tenant HTML error page would repeat on a board, and none does.

**What it is remains unanswered, and the code cannot answer it.** `_parsed_detail` catches the
`ValueError` and discards the response, so no log line records the status, headers, or body. The
hypotheses that fit a 1-per-board spread — a truncated body, an empty 200, a redirect landing page
— are not distinguishable from what is recorded.

Two things were tried and neither settled it:

- **A live hunt with the same client.** `curl_cffi` `AsyncSession(impersonate="chrome")` at width
  25 (`_DETAIL_STREAMS`), against the boards that actually showed the class, capturing any 200 that
  will not parse. **Zero found.** This is *not* evidence of absence: at 1-in-40,000 the expected
  count over the ~4k-fetch sample completed here is ~0.1, and even a full 38k-fetch sweep expects
  ~1. Detecting this with power needs ~120k requests. **Sample size reported precisely because it
  is too small to conclude from.**
- **A rotation correlation.** In CI, Workday walls the runner — `scrape_run` reports `workday:
  walled; spare egress rescued 33,372/33,379 walled request(s) (100%)` — so essentially all Workday
  traffic rides the WARP tunnel, which rotates its egress IP 47-148 times per shard. A rotation
  restarts `warp-svc` and drops connections in flight, which would truncate an already-answered
  200. Tested across 60 shard logs: **Pearson r(rotations, unparseable) = 0.018**. No support. But
  the test has low power — every shard rotates, so there is no zero-rotation control, and the
  per-shard counts are 0-5, where Poisson noise dominates.

**A measured non-finding worth keeping.** `scripts/runlog/fanout_retries.py`'s egress detector —
the 429-per-network ratio, which separates a WARP-healthy shard from one degraded to direct without
trusting a log string — reports **0 of 15 shards DIRECT in every one of the four runs**, and 0 of
15 logged `degrading to direct`. So no shard lost its spare egress, and none of these losses can be
attributed to a shard running bare and eating origin errors. One shard per run sits in the
detector's ambiguous `?` band (429/net ≈ 2.8) rather than being forced to a verdict.

**Verdict: no change.** The rate does not justify a fix, and there is nothing measured to fix
*toward*. The one cheap improvement that would make the next look conclusive — capturing a bounded
body excerpt once per pass, the way `successfactors._titled_fields` treats a 200 that parses to
nothing as a loss — is **deliberately deferred, not rejected**: it is a genuine diagnostic gain,
but it adds logging to a path this repo has just spent three commits de-flooding, and it should be
a decision rather than a side effect of this change.

## 3. HTTP 500 — host health, already bounded

745 postings over 301 board-rows, but the distribution is bimodal:

| | Boards | postings | per board |
|---|---|---|---|
| episode boards | 6 | 247 (33%) | **40-43** |
| background | 295 | 498 | ~1.7 |

**The 40-41 is a signature, not a coincidence.** All six also log `skipped after the 5xx break-off`
(969 postings, the largest class in the whole table), which is ADR-0100's breaker firing at
`_DETAIL_BREAK_STREAK = 30` consecutive settled 5xx plus the streams already in flight at width 25.
`prudential` shows it plainly: `522 of 550 detail(s) failed mid-crawl (skipped after the 5xx
break-off x481, HTTP 500 x41)`. The breaker is doing exactly its job — spending 41 requests and
then stopping, instead of grinding 550.

Two independent checks say host, not request shape:

- **Intermittent per board.** All six appear in all four runs' logs, but lose details in only 1-3
  of them. A request-shape defect would be constant.
- **Clean on live re-probe.** Re-running the detail pass with the same client against all six
  episode boards — `xcelenergy` 208/208, `prudential` 300/300, `nwis` 300/300, `zendesk` 97/97,
  `myhcm` 93/93, `radiancetech` 74/74, **1,072 details** — plus 16 boards sampled from the
  1-per-board background tail. **22 boards, 5,410 details, zero non-200.**

Per CLAUDE.md's "a status code doesn't imply its mechanism", the status alone was not trusted: the
break-off co-occurrence, the per-run intermittency and the live re-probe are three separate
readings, and they agree. **Verdict: no change.**

## 4. Confounds — what these measurements do not establish

1. **The live probes are on a different network path than CI.** This laptop reaches Workday
   directly; CI is walled and rides WARP with frequent IP rotation. A clean local re-probe
   therefore does **not** prove the HTTP 500s or the `unparseable` bodies would be clean from a
   runner. It rules out request shape and persistent host state, not the egress path.
2. **Boards over the 2,000 cap are only sampled to 2,000.** The probe paginates one unfaceted
   query; the scraper subdivides by facet and reads the whole board. Stub counts on `citi/2`,
   `thermofisher` and the like are floors, not totals.
3. **The probes ran hours after the runs.** A transient stub that has since resolved is invisible
   to a later walk — which is consistent with, but does not prove, the transient population.
4. **`unparseable` was not reproduced at all.** Everything said about its mechanism is hypothesis;
   only its rate and its board-spread are measured.
5. **Sync-path counts are approximate.** On the `HEADSTART_ASYNC_FANOUT=0` fallback the class
   `Counter` increments race across threads. The async path is the default and is exact.

## 5. Scope note

The commissioning brief also asked about a "Workday vs successfactors, same mechanism, opposite
thresholds" inconsistency in the ADR-0053 truncation gate. That premise is false and **no
truncation behaviour was touched**. Both scrapers follow one rule — `mark_truncated` iff the
returned id set is short — and both are correct: successfactors takes every field from the job
page, so a failed page drops the Job and the list really is short; Workday takes `ats_id` from the
*listing* item and emits the Job with null fields when the detail fails (ADR-0021, ADR-0097), so
its id set stays complete. Workday's *listing-page* failures do mark truncated, in `_paginate`.
