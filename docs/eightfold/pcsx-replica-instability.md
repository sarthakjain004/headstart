# Eightfold PCSX: how unstable pagination flapped our search index

**Date:** 2026-08-16 · **Issue:** [#142](https://github.com/sarthakjain004/headstart/issues/142) ·
**Fix:** dedupe-by-id + bounded re-sweeps in `EightfoldScraper._api_search` ·
**Harness:** `scripts/eval/flap_audit.py`

This document assumes no prior knowledge of the codebase. It walks through the background, the
bug, the investigation, the fix, and the general system-design lessons — in that order.

## Background: what this system does

HeadStart shows job openings that it reads directly from companies' **ATS boards**. An ATS
(Applicant Tracking System) is the software a company uses to run its hiring — Greenhouse, Lever,
Workday, Eightfold are all ATSes — and a **board** is one company's public list of openings hosted
on that ATS (for example, NVIDIA's jobs on Eightfold). We write one **scraper** per ATS; the same
scraper code reads every company board hosted on that ATS.

Every two hours a pipeline runs: it scrapes a slice of boards, filters the scraped jobs down to
tech roles, embeds the new ones as vectors, and then updates the **served table** — the LanceDB
database that the search UI actually queries. The pipeline step that updates the served table is
called **sync**, and it works by *diffing*:

- A job that the scrape found but the table doesn't have → **add** it to the table.
- A job that the table has, whose board *was scraped this run*, but which the scrape did *not*
  find → the posting has presumably been taken down → **evict** it (delete the row).
- Boards that were not scraped this run are left completely alone (a partial scrape must never
  cause deletions on boards it didn't look at).

The key assumption baked into that diff: **when a scrape says it covered a board, its list is
complete.** "Job X is not in the scrape" is treated as "job X was delisted". This document is
about what happened when that assumption silently broke.

## Background: how the eightfold scraper lists jobs

Eightfold career sites expose a JSON API. To list a board's jobs, the scraper calls:

```text
GET https://{board-host}/api/pcsx/search?domain={company}&start=0
GET https://{board-host}/api/pcsx/search?domain={company}&start=10
GET https://{board-host}/api/pcsx/search?domain={company}&start=20
...
```

Each call returns one **page** of 10 positions plus `data.count`, the board's total number of
postings. This is called **offset pagination**: "give me 10 items starting at position N of the
list". The scraper keeps fetching pages, accumulating positions, until it has as many as
`data.count` promised. That stop-condition — accumulated rows ≥ advertised total — is where the
bug lived.

## The symptom: index rows "flap"

Issue #142 reported that rows were oscillating in and out of the served table. Concretely, a job
id would go through this cycle across pipeline runs (each run ~2 hours apart):

```text
run 1:  in the table
run 2:  evicted   ← sync thinks the posting closed
run 3:  added     ← sync thinks it's a brand-new posting
run 4:  evicted
run 5:  added     ...
```

Measured over 8 consecutive production runs with the audit harness built for this investigation
(`scripts/eval/flap_audit.py`, which reconstructs every add/evict from the pipeline's logs):
**64% of all evicted ids came back within the window, and 32% of all "new" adds were ids we had
already seen.** Nothing about the job had changed at the company — the postings were open the
whole time. Our view of them was oscillating.

Why this matters beyond wasted work: each re-add re-stamps the row's `first_seen` (the "when did
we first index this job" timestamp), so a three-week-old posting looks freshly posted; every
count-based trend signal reads one flap as "a job closed AND a new job opened", which corrupted a
whole market-trends investigation; and all the churn rewrites the database file on every run.

## The investigation, step by step

**Step 1 — measure before theorizing.** The merge stage of the pipeline logs *every id* it adds
or evicts, with a distinct label per deletion path (there are three: sync's evict, plus two
sweep-style cleanups called prune). The audit harness downloads those logs for the last N runs and
intersects the sets: evicted-in-run-N ∩ added-in-a-later-run = flapped. This immediately gave two
discriminating facts:

- **Every flapped id left via sync's evict path** — so the other deletion paths (and the board
  liveness data they depend on) were innocent. The problem was in "board scraped, id missing".
- **96% of flapped rows were on eightfold boards.** For comparison, workday rows: 475 evictions,
  0% re-added — that's what genuine churn looks like. On eightfold, *93% of all evictions were
  wrong*.

**Step 2 — reproduce on one board.** The worst board (`ngc.eightfold.ai`, 3,685 postings) was
crawled locally with the real scraper code. The crawl fetched 3,685 rows — exactly the advertised
total, so it looked complete — but only **3,460 of them were unique**. 225 rows were duplicates,
which means 225 *distinct* jobs were never fetched at all. 6.1% of the board, silently missing,
from a crawl that believed it had everything.

**Step 3 — isolate the mechanism.** Fetching the *same page twice, seconds apart* returned
different ids: at one probe, 8 of 10 differed; at another offset, 18 of 20. Other offsets were
identical across fetches. So the server itself deals different cards to the same offset at
different moments. No scrape-timing, no rate-limiting, no code of ours involved — two identical
HTTP requests, two different answers.

## The root cause, from first principles

Offset pagination is a contract: "the list is the same list between my requests, so start=20
today is the same 10 items as start=20 two seconds ago". That contract only holds if the list has
a **stable total order** — every item has a well-defined position, with no ambiguity.

Eightfold's default ordering sorts by `postedTs`, the posting date — and `postedTs` has **day
resolution** (every job posted on the same day carries the identical timestamp). On a 3,685-job
board, hundreds of jobs tie. What order do tied jobs appear in? The sort doesn't say. Each
backend **replica** — one of several identical servers behind a load balancer, each holding a
copy of the data — resolves the ties in whatever order its internal index happens to produce.
The orderings agree on the sort key but disagree within the ties.

Now picture the crawl. Each page request goes through the load balancer to *some* replica:

```text
        replica A's order:  [x1 x2 x3 x4 | x5 x6 x7 x8 | ...]
        replica B's order:  [x2 x1 x4 x7 | x3 x8 x5 x6 | ...]

crawl:  page 1 → replica A → gets x1 x2 x3 x4
        page 2 → replica B → gets x3 x8 x5 x6   ← x3 again! and x7 never appears
```

A job that sits at offset 120 on replica A and offset 3,140 on replica B gets fetched **twice**
if both its offsets happen to be served by the "wrong" replica — and its mirror image is a job
fetched **never**. Duplicates and misses come in matched pairs: with the total fixed, every
duplicated row implies one distinct job you didn't get.

We probed for a fix on the server side: no sort parameter makes the order deterministic.
`sort_by=timestamp` is the (tied) default and `sort_by=relevance` is just a different unstable
order. This is the API we have.

## Eightfold's serving architecture, reconstructed from the outside

We can't see Eightfold's infrastructure — only its behaviour at the HTTP boundary. But this
project has now probed that boundary from several angles (this investigation, the original
scraper research, and the async fan-out throttle measurements), and the observations compose
into a fairly coherent picture. Everything below is labelled either **observed** (we measured
it) or **inferred** (the standard architecture that would produce that measurement).

### First, the two concepts the bug turns on

**A load balancer** is the front door of almost every serious web service. One hostname
(`ngc.eightfold.ai`) does not point at one server; it points at a device that receives every
request and forwards each one to any of a pool of interchangeable backend servers, typically
picking by round-robin or by who's least busy. This is how a service scales (add more servers to
the pool) and survives failures (a dead server is dropped from the pool). The crucial property
for our bug: **two requests sent seconds apart can be — and routinely are — answered by
different backend servers.** Load balancers *can* pin a client to one backend ("session
affinity", usually via a cookie), but that's an opt-in feature mostly used for logged-in
sessions, and plain anonymous API GETs like ours don't get it. Observed: nothing about our
requests (no cookie set, no header echoed) suggested affinity, and the same-offset probes prove
we hit differently-ordered sources on plain repeated GETs.

**A replica** is one of those interchangeable backends' copies of the data. To let N servers
answer read queries, the data is copied (replicated) N ways. The copies hold the *same rows* but
are *physically independent* — each maintains its own internal file layout and index structures.
Anything the query leaves unspecified (like the order of rows that tie on the sort key) falls
through to that internal layout, and so can legitimately differ per replica while every replica
is still "correct".

### What we observed, and what it implies

**One multi-tenant platform behind many hostnames** (observed). `careers.qualcomm.com`,
`jobs.nvidia.com`, and `{company}.eightfold.ai` all serve the identical PCSX app and API shapes;
the company is selected not by the hostname but by an explicit `domain={company}.com` query
parameter, which the careers page hands to the SPA as `_EF_GROUP_ID`. Inferred: the vanity
domains are DNS aliases onto the same shared edge, and every tenant's traffic lands in one
shared serving stack — one codebase, one API, tenant resolved per-request. This matters
operationally (below: rate limits are shared too).

**A search engine, not a simple database, behind `/api/pcsx/search`** (inferred, strongly). The
endpoint's behaviour has the fingerprints of an Elasticsearch/OpenSearch-class cluster: it
offers a `relevance` ordering (i.e. scored full-text search — that's what search engines do);
its default "timestamp" ordering ties at day granularity and breaks the ties in
replica-dependent order, which is exactly how search engines behave — rows equal on the sort key
fall back to internal document order, which depends on each replica's private segment layout and
merge history; and it returns an exact total hit-count (`data.count`) alongside each page.
Observed, and important: **the count is rock-stable while the ordering is not** — five boards
returned exactly matching counts across different clients and days (2,605 / 3,685 / 3,408 /
1,710 / 1,380), and the count never wavered during our probes. Counting matched documents gives
every replica the same answer; ordering their ties does not. That asymmetry is the whole bug in
one sentence.

**No session affinity on the API path** (observed). The same URL fetched twice, seconds apart,
from the same client returned differently-ordered pages — so consecutive requests genuinely
reach different replicas. Which offsets disagree changes minute to minute; inferred, that
snapshot of disagreement shifts as the load balancer's rotation and the replicas' background
index-maintenance (segment merges) move things around.

**A shared, metered edge across all tenants** (observed). The async fan-out measurements
(ADR-0047, `scripts/bench/probe_eightfold_throttle.py`) found detail fetches losing 78.6% of
requests at concurrency 100 and 49.9% at 25 — *across tenants*: the meter is per client origin
against the platform, not per board. Inferred: a rate-limiting gateway at the shared edge, in
front of whatever serves the API — standard token-bucket behaviour. This is why the scraper
caps eightfold detail concurrency at 25 streams and why re-sweeps deliberately re-fetch only
the cheap listing pages.

**Correction (2026-08-16):** an earlier version of this section claimed a static, per-route WAF
policy — `/api/apply/v2/*` always hard-403s non-browser clients, HTML surfaces always 405 a bare
fetch. That was wrong, and worth recording *why* it was wrong: the 405 this project actually hit
(commit fixing #121, "405 was what Eightfold's edge returned once its per-origin budget was
spent") is the **same shared rate-limiter** described above, not a separate per-route tier —
`http.fetch` already retries 403/405/429 as bot-wall blips (`headstart/http.py`, ADR-0047) and
honours `Retry-After`, so production scrapers never observed a raw first-attempt block; it was
silently absorbed. Direct, unwrapped probes on 2026-08-16 (bypassing the retry layer) found:

- `/careers/sitemap.xml` and `/careers/job/{id}` answer **200 to a single bare request** under
  *any* of: `curl_cffi` with Chrome TLS impersonation (`http.fetch`'s transport for every
  scraper) regardless of headers, `curl_cffi` with no impersonation but full browser-style
  headers, or plain stdlib `urllib` with full browser-style headers. Only stdlib `urllib` with
  **zero** headers (Python's own default `User-Agent: Python-urllib/3.x`, no `Accept`/`Referer`)
  was blocked, and with 403 — not 405.
- So there are two independent, weaker mechanisms, not one route-tiered WAF: a baseline
  UA/header plausibility check that a bare unbranded client fails (403, static, low-volume), and
  the shared per-origin rate/budget meter that trips under sustained concurrent load regardless
  of path (405, the #121 mechanism). Neither is route-specific — `/api/pcsx/search` itself would
  presumably also 405 under enough concurrent load, and every surface passes the baseline check
  once *any* plausible header set or TLS fingerprint is present.
- Every request in this codebase already goes through `http.fetch`, which supplies Chrome TLS
  impersonation unconditionally — so this was never a live risk for us, but the causal story
  (bot-hardened per-route policy) does not hold, and a design decision (sitemap-primary; below)
  built on the wrong story would have over-invested in browser-grade transport for surfaces that
  never needed it.

**A separate per-document read path** (observed). Job descriptions are not in the search
response at all; each is a separate `GET /api/pcsx/position_details?position_id=...` returning
~15 KB of JSON. Inferred: the classic search-system split — the search cluster stores only what
listing/filtering needs, and full documents live behind a keyed lookup (document store or
cache). That split is also why our fix is cheap: re-sweeping touches only the search tier, and
detail fetches — the expensive, metered path — stay deduplicated by id.

**A parallel SEO surface** (observed). Every tenant also publishes `/careers/sitemap.xml`
(sometimes as an index of child sitemaps) listing every job URL, and each job page embeds
schema.org `JobPosting` JSON-LD. That surface exists for Google, not for API clients — but it's
a genuinely independent listing path, which is why the scraper uses it as the fallback for the
~20% of tenants whose API 403s.

### The takeaway shape

```text
                        vanity CNAMEs (careers.qualcomm.com, jobs.nvidia.com, …)
                                          │
                              shared edge: CDN + WAF/bot rules
                              + per-origin rate metering
                                          │
                    ┌─────────────────────┼──────────────────────┐
              /api/pcsx/search      /api/pcsx/position_details   /careers/* HTML,
                    │                     │                      sitemap.xml (SEO)
              load balancer         keyed document lookup
              (no affinity)               │
             ┌──────┼──────┐         doc store / cache
         replica  replica  replica
           A        B        C      ← same rows, private tie-orders
```

None of this is exotic — it is the textbook architecture for a multi-tenant search-backed SaaS.
Which is exactly the point: **any** API built this way, sorted on a coarse key without a unique
tie-breaker, will exhibit the same unstable offset pagination. Eightfold is just where we met
it first.

## Why a 6% sampling error became a 64% eviction error

The scraper's stop-condition was:

```python
while len(positions) < total:   # total = data.count
```

`positions` included the duplicates. So a crawl carrying 225 dupes reaches `len(positions) ==
3685`, decides it is complete, and returns — missing 225 jobs it doesn't know it's missing.

The pipeline actually has **two defenses** against incomplete scrapes, and the bug threaded the
needle between them:

1. **Truncation reporting** (ADR-0053): a scraper that *knows* it came up short (rate-limited
   mid-crawl, error page) marks the board "unauthoritative", and sync then skips the board's
   evictions entirely — a short list must not read as mass delisting. But our crawl *didn't know*
   it was short: the row count matched the total. Never marked, board stays authoritative.
2. **The collapse guard** (ADR-0046): a board losing more than 25% of its rows in one run is
   presumed truncated, and the eviction is capped. But the miss was ~6% — far under 25%. The
   guard correctly stayed quiet.

So sync trusted the list, evicted the 225 missing jobs as "delisted", and — because the next
crawl mixes replicas differently — missed a *different* ~6% next run, which means last run's
evictees are back in the list and get re-added as "new". Repeat forever. That is the flap.

Note what the bug is *not*: it's not a crash, not an error in any log, not a wrong HTTP call.
Every component behaved exactly as written. The failure was an inconsistency between what the
scrape *claimed* ("this list is complete") and what was *true*, and the reconciliation machinery
downstream faithfully executed the false claim.

## The fix

Two changes to `_api_search`, both following from "measure completeness in distinct jobs":

1. **Dedupe as pages arrive** — positions are keyed by job id, so the accumulator can't
   double-count. Completeness is now `len(distinct ids) >= data.count`.
2. **Re-sweep when short.** When a full pass over the offsets ends with fewer distinct ids than
   the total, crawl the board again from offset 0 (up to 3 sweeps total). The point: the next
   sweep's requests land on differently-ordered replicas, which deal the previously-missed jobs
   to offsets we actually fetch. A ~6% miss per sweep shrinks geometrically — two extra sweeps
   almost always close the gap. Sweeps stop early when complete, or when a sweep finds nothing
   new (then the shortfall is real, and the board is honestly marked truncated so defense #1
   finally applies to it).

Cost: a re-sweep re-fetches only the cheap 10-jobs-per-page listing calls (~370 requests on the
largest board), never the expensive per-job description fetches — those are keyed by id and
already deduplicated.

Two regression tests pin the behaviour, using fake pages that replay the replica scenario: one
where a re-sweep completes the list (must return every distinct id exactly once, and must NOT
mark the board truncated), and one where the gap never closes (must mark it truncated with the
measured shortfall). Both fail on the old code.

## The system-design lessons

**1. Offset pagination is only as sound as its sort key.** `start=N` presumes a total order. A
sort key with coarse resolution (dates, priorities, scores) over many items is a *partial* order,
and everything inside a tie is up to the serving node. Any horizontally-scaled backend will break
ties differently per replica unless the query pins a unique tie-breaker (…`ORDER BY posted_at,
id`). This is exactly why cursor/keyset pagination exists — the cursor ("after item X") carries
the order with it. When consuming an API you don't control, you can't fix the ordering — but you
can *detect* it, because unstable ordering has a signature: **the same item on two pages**. If
you ever see a cross-page duplicate, you are also missing something.

**2. Measure completeness in the unit you care about.** The crawl counted rows fetched; the
system cares about distinct jobs. Any accumulator that can double-count will eventually launder a
gap into a "complete" result. The server even provided the exact expected cardinality
(`data.count`) — the one-line invariant `len(unique_ids) == data.count` would have caught this on
day one. When a source hands you a checksum, check it against the deduplicated thing.

**3. Layered defenses fail at the intersection of their blind spots.** Defense 1 catches a
scraper that knows it failed; defense 2 catches large losses. The surviving failure mode was
precisely "doesn't know it failed" ∧ "loses a little at a time". When you reason about
defense-in-depth, don't count layers — enumerate what each layer *cannot see*, and look at the
conjunction. That conjunction is where the next incident lives.

**4. Reconciliation loops amplify input noise into state churn.** Sync is a reconciler: it
treats the scrape as desired state and diffs it against actual state. Reconcilers don't average
noise away — they *execute* it, in both directions, every cycle. Feed one a sampled, unstable
observation and you get oscillation (here: hundreds of rows, every 2 hours, indefinitely). A
reconciler needs either a trustworthy observation or damping — hysteresis, N-strikes-before-
delete, quorum over multiple observations. We fixed the observation; damping remains an option if
another provider proves unstable (successfactors shows a smaller 21% version of the same
signature — tracked in #142).

**5. Derived data inherits upstream lies, and they compound quietly.** `first_seen` feeds
"posted recently" filters and alert watermarks; add/evict counts feed trend analysis. None of
those layers can tell a re-add from a genuinely new job. The wrongness stays *internally
consistent* — the trends math reconciled perfectly with the index while both described a market
that didn't exist. Validate observations where they enter the system; nothing downstream can
un-lie them.

**6. Log identities, not just counts.** The table's net size barely moved (~+570/run) while
1,300+ rows churned beneath it — invisible in every aggregate. What cracked the case was that the
merge stage logs the *ids* behind every add and evict, with a distinct label per deletion path.
That turned diagnosis into set arithmetic on logs: intersect evictions with later adds, group by
board, read off the answer. When a mutation matters, log which items — counts tell you *that*
something happened; identities tell you *what*, and they're the difference between "looks fine"
and "54% of these are the same rows oscillating".

## Reproduction artifacts

- `experiment/index-flapping/probe_pcsx_stability.py` — two-pass crawl diff (local only,
  `experiment/` is gitignored). Old-code result on `ngc.eightfold.ai`: pass 1 = 3,685 rows /
  3,460 unique (225 missed), pass 2 = 3,685 / 3,496 (189 missed), and the passes disagreed on
  ~200 ids (181 only-in-1, 217 only-in-2) — consecutive "complete" crawls covering different
  subsets, which is the flap in miniature.
- Same-offset double-fetch probes: inline, recorded in the #144 fix PR.
- `scripts/eval/flap_audit.py --runs 8` — the goal metric: already-known adds must sit **< 10%**
  overall (32% at diagnosis; 96% of flapped rows were eightfold).
- Regression tests: `tests/test_scrapers.py::test_eightfold_resweeps_an_unstable_list_to_completeness`,
  `::test_eightfold_marks_a_persistently_short_list_truncated`.
