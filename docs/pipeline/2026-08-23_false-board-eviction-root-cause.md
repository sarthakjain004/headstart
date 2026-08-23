# False board eviction across the pipeline — root cause

**Date:** 2026-08-23 · **Runs read:** 15 most recent completed pipeline runs at the time of
analysis, `32568669902` → `32612152291` (~15.3h span, all `conclusion: success`) · **Method:**
every distinct id evicted across those 15 runs — **all 1,217 of them, not a sample** — re-fetched
live against its real current board via the actual registered scraper, across all 273 distinct
boards. (An initial pass sampled 104 ids; a full recheck of every id was run after the sample was
found to understate real incidence — see §2.)

## Summary — read this first

**General root cause:** `index sync`'s per-board eviction
(`src/headstart/ingest/index_plan.py::plan_sync`) deletes any indexed id absent from a *single*
fresh scrape, with no per-id re-confirmation across runs. The only safety net (`COLLAPSE_RATIO`)
is a board-level circuit breaker for mass truncation in one run — it does not catch one run
quietly missing a handful of ids on an otherwise-normal-looking board. Any transient miss, from
any cause, becomes an immediate, permanent delete.

- **Workday — systemic, the dominant offender, confirmed root cause.** `_posting_key()` treats
  `bulletFields[0]` as the stable requisition id. It never is — across 121 confirmed false
  evictions on 25 different boards, `bulletFields[0]` was a location, a relative posted-date, a
  closing-date label, an employment-type tag, a store name, or a company subsidiary name, never a
  requisition id. The derived "id" changes almost every scrape for affected tenants, so `sync`
  evicts-and-reinserts the same live job forever. 24.3% of all fully-checked Workday evictions
  (87/358) were false; among ids evicted **repeatedly** across separate runs — the clearest
  fingerprint of this bug — the rate is 95%.
- **Greenhouse — real, but a different mechanism: transient scrape misses, not an id bug.** 14
  false evictions on 3 boards (`databricks`, `metrostarsystems`, `vast`). Greenhouse's id is the
  platform's own stable numeric job id, so this isn't id instability — every false-evicted id on a
  given board was evicted in the *same one or two pipeline runs*, the signature of a single bad
  scrape (not many independent real closures, which would scatter across runs) that `sync`'s
  no-grace-period design converted straight into permanent deletes.
- **SuccessFactors — same shape as Greenhouse, and self-documented.** 20 false evictions on 4
  boards, also clustered into 1-2 runs per board. The scraper's own docstring already names the
  mechanism: *"A page that yields no title drops that job for the run... it returns next
  scrape."* A transient detail-page failure silently excludes an otherwise-live job from that
  scrape's output; `sync` then deletes it outright instead of waiting to see if it comes back.
- **Every other ATS with evictions — clean across every evicted id, not just a sample.** ashby
  (40/40 genuine), darwinbox (4/4), keka (3/3), lever (75/75, plus a separately-confirmed 72/72
  repeat-eviction check), recruitee (3/3), ripplehire (10/10), rippling (1/1), smartrecruiters
  (13/13 confirmed, 9 inconclusive), teamtailor (3/3), zoho (58/58 confirmed, 54 inconclusive) —
  zero false evictions found anywhere. eightfold could not be live-checked (bot-walled boards +
  non-functional sudo-gated IP rotation in this environment — see §5), but its id scheme is
  structurally the same as this clean group's, not Workday's.

No fix is implemented yet — §6 lays out options for a decision, now including a concrete,
live-validated replacement pattern for Workday's `_posting_key()` (129 tenants sampled, 0
regressions found).

---

## 1. Scope

15 pipeline runs evicted **1,381** row-events, **1,217 distinct ids**, across **273 distinct
boards**. Every single one of those 1,217 ids was re-fetched live — not sampled:

| ATS | boards checked | ids: still live (false) | ids: confirmed gone (genuine) | ids: inconclusive | boards w/ ≥1 false evict |
|---|---:|---:|---:|---:|---:|
| workday | 148 | **87** | 271 | 0 | **25** |
| greenhouse | 24 | **14** | 318 | 0 | **3** |
| successfactors | 45 | **20** | 82 | 90 | **4** |
| zoho | 9 | 0 | 58 | 54 | 0 |
| eightfold | 20 | not live-checked | — | 62 | — |
| lever | 3 | 0 | 75 | 0 | 0 |
| ashby | 5 | 0 | 40 | 0 | 0 |
| smartrecruiters | 9 | 0 | 13 | 9 | 0 |
| ripplehire | 2 | 0 | 10 | 0 | 0 |
| darwinbox | 4 | 0 | 4 | 0 | 0 |
| recruitee | 1 | 0 | 3 | 0 | 0 |
| keka | 1 | 0 | 3 | 0 | 0 |
| teamtailor | 1 | 0 | 3 | 0 | 0 |
| rippling | 1 | 0 | 1 | 0 | 0 |

"Inconclusive" = the board's fetch timed out (120s) against a large or slow catalog and wasn't
counted either way — not evidence of a problem, just unresolved. Every board that *did* resolve
resolved cleanly to one of the two answers above.

**76 of 77** ids evicted more than once across separate runs (out of the 15) are Workday — repeat
eviction, the clearest fingerprint of an id that never stabilizes, is almost exclusively a Workday
phenomenon. The one non-Workday repeat
(`successfactors:careers.hcltech.com:1364226855`) is genuinely absent — ordinary listing churn on
a 9,227-job board.

## 2. Why the full recheck, not the sample

The first pass sampled up to 10 ids per ATS (104 total) and found the greenhouse/successfactors
pattern looked like two isolated one-off incidents. Checking every evicted id instead — not just
one per board — changed that conclusion: `databricks` went from "1 sampled id, found false" to "2
of 2 total evicted ids, both false"; boards that had zero sampled ids evicted (`metrostarsystems`,
`vast`, `gic.com.sg`, `chartindustries`, `bayer`) turned out to have several-to-a-dozen false
evictions each once their *complete* evicted-id sets were checked. A 1-id sample per board
systematically undercounts a phenomenon that shows up as "a handful of ids on this board, out of
many," because most single draws from that board come back genuine. The general lesson: for a
board-level phenomenon like this, sampling per-id understates incidence — check every id on a
board once any of its ids are suspect, not just the sampled one.

Two real bugs surfaced in the recheck tooling itself along the way (both fixed before trusting the
numbers above): a case/format mismatch between the liveness ledger's tenant strings and the
company/site shape actually embedded in Workday job ids (was silently defaulting ~140 Workday
boards to an unresolvable slug and erroring every one); and Workday's own `fetch_raw()` fetching a
full per-job detail page for every posting even though presence-checking only needs the listing
pass (`_posting_key()` reads only `bulletFields`/`externalPath`, never the detail response) — fixed
by calling the scraper's own listing-only internals directly, cutting a large-tenant check from
minutes to seconds with byte-identical results, verified against a known board before trusting it
at scale.

## 3. Workday — the confirmed mechanism

`src/headstart/scrapers/workday.py:697-703`:

```python
def _posting_key(item: dict[str, Any]) -> str:
    """Stable per-posting id: bulletFields[0] (requisition id on tenants that
    surface it) else the externalPath tail."""
    bullet = (item.get("bulletFields") or [None])[0]
    if bullet:
        return str(bullet)
    return (item.get("externalPath") or "").rsplit("/", 1)[-1] or "unknown"
```

The docstring's premise — that `bulletFields[0]` is the requisition id — is false on every one of
the 25 affected boards. The 121 confirmed false-eviction ids show `bulletFields[0]` playing at
least **six different roles**, never the req id:

| role | examples |
|---|---|
| location | `Chandler, Arizona`; `United Kingdom`; `Bremen`; `AE - Abu Dhabi`; 60+ more across `airproducts`, `parsons`, `xylem`, `otis`, `ppg`, `honorhealth`, `braunintertec`, and 10 other boards |
| relative posted-date | `Posted 25 Days Ago` / `Posted 29 Days Ago` (`astro`, same job, two different scrapes — the value moved by exactly the elapsed days); `Posted 30+ Days Ago` (`walshgroup`) |
| closing-date label | `Closing Date:`; `Closing Date: 25/08/2026` (`rbs`) |
| employment-type tag | `Casual` (`usyd`) |
| store/site name | `0018 - Shaler - Supermarket` and 6 more (`gianteagle`); `SSC Enon OH` (`my7elevenhr`) |
| company/subsidiary name | `Apogee Services Inc.` (`apog`); `NKG Commercial Services Company Ltd` (`nkg`); `Roy Anderson Corp` (`tutorperini`) |
| location rollup | `5 Locations` (`taylor`) |

Two of the 121 (`cba/CommBank_Careers:REQ261245`, `frenckengroup/External:JR101259`) *look*
req-id-shaped and still false-evicted — worth the caveat that a plausible-looking id isn't proof
of stability either; a job can also close and reopen under a fresh req id, which is
indistinguishable from this bug by id shape alone. These two don't overturn the pattern (119 of
121 are unambiguously non-req-id text) but they're a reason a fix should verify stability
empirically, not just by shape, before shipping.

**Two eviction rates, measuring different things:** 24.3% of *all* fully-checked Workday
evictions were false (87/358) — most Workday evictions are ordinary one-time job closures,
correctly evicted. But among ids evicted **repeatedly** across separate runs, 95% were false
(62/65 in the original targeted repeat-eviction check) — repeat eviction is close to a direct
readout of this bug, since a genuinely-closed job is evicted once and never seen again, while an
unstably-keyed live job cycles in and out of the index every run.

**Secondary, distinct mechanism observed in passing:** during the recheck, `maersk/maersk_careers`
self-reported `1 of 64 page(s) failed mid-crawl — Board unauthoritative this run` — Workday's own
pagination-truncation detector firing on a real mid-crawl failure. ADR-0053 already excludes
boards the scrape marks unauthoritative from `sync`'s eviction scope, so this specific mechanism
should already be guarded against — it's noted here as evidence that genuine pagination failures
do happen for Workday tenants, a second, smaller, already-mitigated failure mode distinct from the
`bulletFields[0]` bug that dominates the numbers above.

### Ranked hypotheses (Phase 3)

1. **(Confirmed, leading) Index-0 field misidentification.** `bulletFields[0]` is assumed to be
   the requisition id; it's actually whichever bullet a tenant's Workday template puts first, and
   the real req id floats at a later, tenant-dependent index. Confirmed directly on 25 boards, zero
   counterexamples among the 119 clearly-non-req-id false evictions.
2. **(Considered, lower confidence) Intermittent field absence** — `bulletFields` sometimes
   omitted per-request rather than mis-ordered per-tenant. Would predict scattered,
   mostly-single-occurrence evictions spread across many tenants; observed data instead shows
   false eviction concentrated tenant-by-tenant (the same 25 boards, not a random scatter across
   all 148) — a tenant-level template property, not per-request flakiness.
3. **(Considered, low confidence, mostly ruled out) Pagination/ordering inconsistency**
   independent of id derivation. Would predict evicted-then-reappearing ids that look stable (the
   id itself unchanged, just missing from one page). Contradicted by the majority of examples,
   which are ids that change value by construction (`Posted N Days Ago`, `Closing Date: <date>`) —
   not page-order noise. The `maersk` mid-crawl failure shows this mechanism is real but separate,
   and already has a mitigation (ADR-0053) that the dominant bug doesn't need to touch.

## 4. Greenhouse and SuccessFactors — transient scrape misses, not id bugs

Both ATSes' ids are the platform's own stable numeric job id
(`src/headstart/scrapers/greenhouse.py:31`: `id=f"{self.ats}:{self.slug}:{j['id']}"`;
`src/headstart/scrapers/successfactors.py:314`: `id=f"{self.ats}:{self.slug}:{item['id']}"`) — so
this isn't Workday's mechanism. What they share instead: every false-evicted id on a given board
was evicted in the *same one or two pipeline runs*, not scattered across the 15-run window —

| board | false evictions | run(s) they were evicted in |
|---|---:|---|
| greenhouse `databricks` | 2 | one run (`32574982652`) |
| greenhouse `metrostarsystems` | 5 | two runs (`32606136882`, `32612152291`) |
| greenhouse `vast` | 7 | one run (`32592349834`) |
| successfactors `careers.gic.com.sg` | 3 | two runs (`32568669902`, `32579833859`) |
| successfactors `jobs-offshore.hanwhaocean.com` | 11 | one run (`32594712165`) |
| successfactors `jobs.chartindustries.com` | 1 | one run (`32571222780`) |
| successfactors `jobs.bayer.com` | 5 | one run (`32592349834`) |

Genuine closures happen at different times for different postings and would scatter across the
15-run window; several-to-a-dozen ids all vanishing from one board in the *same single run* is
the signature of that one scrape being incomplete, not several unrelated real closures lining up.

**SuccessFactors has a named, self-documented mechanism for exactly this**
(`src/headstart/scrapers/successfactors.py` module docstring): *"A page that yields no title
drops that job for the run... it returns next scrape."* A transient detail-page parse failure
(network hiccup, momentary malformed markup, a slow response) silently excludes an otherwise-live
job from that one scrape's output — the job was never actually gone, the scrape just couldn't
confirm it that cycle. `plan_sync` has no way to tell "confirmed gone" apart from "couldn't
confirm this run," so it deletes either way.

**Greenhouse has no equivalent documented mechanism**, but the same clustering evidence applies.
Its `.url()` is a single GET returning the whole board in one response
(`boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`, no pagination on our side), fetched
via `BaseScraper._get()` with a 30s timeout and "retry lives there" per its own docstring
(`src/headstart/scrapers/base.py:168-180`). `?content=true` inlines full descriptions at "~12x the
payload" per `greenhouse.py`'s own docstring — a large board's single response is a large,
slower download, and a transient timeout, retry-exhaustion, or an incomplete response from
Greenhouse's own side would show up exactly this way: several ids from one board, missing in one
run, never seen missing again. This is the best available explanation given the clustering
pattern, but — unlike Workday's and SuccessFactors's — it isn't independently confirmed by a
scraper-side comment or a directly observed partial-response event; flagged as inferred, not
proven, pending further instrumentation if it recurs.

**Both are explained by the same general root cause (§6):** `plan_sync` treats a single scrape's
absence as authoritative with no cross-run confirmation, so whatever produces the transient miss —
SuccessFactors's documented detail-fetch failure, Greenhouse's inferred large-payload timeout —
becomes a permanent delete instead of surviving to the next scrape.

## 5. Every other ATS

ashby, darwinbox, keka, lever, recruitee, ripplehire, rippling, smartrecruiters, teamtailor, zoho
— **every evicted id across the full 15-run window**, not a sample, was genuinely gone from its
real board. Code review of every scraper's id construction confirms all of them key off the
platform's own raw id field (`j['id']`, `o['id']`, `p['id']`, `jid`, `item['id']`, `jobSeq`) — none
use a derived/mutable-text fallback like Workday's. zoho (54 ids), smartrecruiters (9 ids), and a
portion of successfactors's total (90 ids) hit the 120s per-board timeout on unusually large or
slow catalogs and were left unresolved rather than guessed at.

**Eightfold** could not be live-checked from this environment at all: its boards are bot-walled
(403/405) and the scraper's spare-egress IP-rotation fallback requires `sudo` to restart
`warp-svc`, unavailable non-interactively here — every attempt across all 20 of its boards looped
on "rotation failed: sudo: a password is required" until hitting the timeout. Pre-existing,
separate infrastructure limitation, not part of this bug. Code inspection stands in:
`_POSITION_ID = re.compile(r"/careers/job/(\d+)")` — both Eightfold's API path and its
sitemap-fallback path resolve to the same real, numeric, Eightfold-issued `position_id`
(`src/headstart/scrapers/eightfold.py:64,272-273,340,501-503`). Structurally this is the clean
group's shape, not Workday's — not expected to share the bug, but not independently live-confirmed
the way every other ATS above now is.

## 6. Fix options

**Status: A1 and A2 implemented together in PR #265** (`fix/workday-posting-key-stability`),
per explicit direction — ship the id-derivation fix now rather than treating A2 as a deferred
fast-follow, since it's a small, already-verified addition once A1 is being built anyway. **B is
deliberately not part of that PR** — scoped out, not missed; it touches `plan_sync` generally
(every ATS, not just Workday) and is left as a separate follow-up decision. A1's fallback order
below was corrected from the version originally validated here — see the callout after the code
block — after live verification surfaced a live collision the original order would have shipped.

Two independent problems, fixable independently or together:

**A. Workday's id derivation.** Stop trusting a fixed index into `bulletFields`.

- **A1 — pattern-match the req id (validated, recommended).** Scan `bulletFields` for the entry
  shaped like a requisition id and prefer that over any fixed index; keep `bulletFields[0]`, then
  the `externalPath` tail, as fallbacks only when nothing matches. Live-validated by an independent
  subagent pass against **129 sampled tenants** (114 usable, 1,029 real listing items, board sizes
  0–4,183 jobs): **98.9% of items matched exactly one confident candidate, 0% were ambiguous**
  (no item ever produced 2+ matches), and **every one of the 1,018 matches was independently
  confirmed as a literal substring of that item's own `externalPath`** — a ground-truth check
  against data the pattern itself didn't use. Of the 1,029 items, 127 (12.3%) would get a
  *different* id than the current code — every one individually checked and confirmed a fix, zero
  regressions. This also surfaced 6 more affected tenants beyond the 25 already found by
  live-checking evictions: `monnoyeur`, `elara`, `worldpay`, `thales` (a 4-field `bulletFields`
  with the req id at index 1), `bunnings` (req id at index 2), `printpack`, `rsc` — the true
  affected-tenant count is at least 31, not 25 (the smaller number only reflects tenants that
  happened to have an eviction in the 15-run window). The validated implementation:

  ```python
  _ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{1,2}$")
  _REQ_ID_SHAPE = re.compile(
      r"^(?:[A-Za-z]{1,5}[-_ ]?){1,2}\d[\dA-Za-z]*(?:[-_ ]+\d[\dA-Za-z]*)*$"
      r"|^\d[\dA-Za-z]*[-_][A-Za-z]{1,3}$"
  )
  _BARE_NUMERIC = re.compile(r"^\d+$")

  def _looks_like_req_id(field: str) -> bool:
      field = field.strip()
      if not field or _ISO_DATE.match(field):
          return False
      if _BARE_NUMERIC.match(field):
          return len(field) >= 4  # needs enough length to not be a stray flag/count
      return bool(_REQ_ID_SHAPE.match(field))

  def _posting_key(item: dict[str, Any]) -> str:
      bullet_fields = item.get("bulletFields") or []
      candidates = [f for f in bullet_fields if isinstance(f, str) and _looks_like_req_id(f)]
      if candidates:
          return re.sub(r"\s+", "", candidates[0])
      bullet = (bullet_fields or [None])[0]
      if bullet:
          return str(bullet)
      return (item.get("externalPath") or "").rsplit("/", 1)[-1] or "unknown"
  ```

  Went through 3 iterations against live data before landing here: the first cut
  (`^(JR|R-?)\d+`, from this doc's original 6-tenant sample) already missed real shapes in the
  wider sample (`PT-JR042569`, `REQ2026 - 9929`, bare-numeric ids like `26027605`, a reversed
  `2409195-R` shape) and had a real ambiguity bug (bare `"0"`/`"1"` bulletFields on one tenant
  matching the numeric fallback) — both fixed and reverified in the version above. Residual risk,
  not observed in any of the 1,029 items: a bare 4+-digit field that *isn't* a req id (e.g. a
  standalone zip code) would still false-positive; no tenant in this sample has that shape, but it
  isn't provably impossible elsewhere.

  **A fourth issue found live-verifying the shipped code, after this validation pass: the
  fallback order above (`candidates` → `bulletFields[0]` → `externalPath` tail) has a real
  collision bug.** Re-checking all 25 confirmed-affected boards found 4 (`tutorperini`, `nkg`,
  `braunintertec`, `usyd`) with no req-id-shaped `bulletFields` entry at all, where
  `bulletFields[0]` is a value **shared across every posting on the tenant** — `tutorperini`'s is
  the literal string `"Tutor Perini Corporation"` on all of them. Measured: 228 real
  `tutorperini` postings resolved to just **15** distinct ids under the order above — a
  collision, worse than the instability this whole fix exists to address, since a collision
  silently drops postings rather than merely cycling their id. The shipped code ranks
  `externalPath`'s tail — Workday's own URL slug, `{title}_{req-id-or-similar}`, specific to one
  posting by construction — above `bulletFields[0]`, which now sits last, reached only when
  `externalPath` is itself empty. Reverified after reordering: `tutorperini` 228/228,
  `nkg` 56/56, `braunintertec` 120/120, `usyd` 63/63 distinct keys — zero collisions.
- **A2 — fetch a canonical id from the detail response (shipped alongside A1).** Assessed here as
  a fast-follow that "shouldn't block A1" — shipped together with it instead in PR #265, since
  the marginal cost of wiring in an already-verified field was small once A1 was being built.
  `jobPostingInfo` (fetched already for tenants needing a detail pass, but only
  `description`/`startDate`/`timeType`/`location`/`additionalLocations`/`remoteType` are
  currently kept) always carries an explicit `jobReqId` field — checked across 7 tenants spanning
  every `bulletFields` shape found (1/2/3/4 elements, and the one all-null-bulletFields tenant),
  and it matched A1's pattern-matched value byte-for-byte every time (`astro→JR00258`,
  `thales→R0336998`, `bunnings→R065143`, ...), including `cfr/buc-careers` — one of the two
  tenants (`cfr`, `morningstar/Sales-and-Client-Service`) whose `bulletFields` comes back `null`
  entirely (1.1% of the sample), which A1 can't help at all since there's nothing to scan. The
  catch: `_posting_key()` runs once during listing crawl (before any detail fetch) and again in
  `parse()` (after) — `jobReqId` could only ever supplement the second call, never replace the
  first, and a failed detail fetch (already tolerated for `description`) would still need A1's
  fallback. Given A1 alone already covers 98.9% of items with 0 ambiguity and 0 cross-check
  failures, treat A2 as a small fast-follow closing the remaining ~1% `bulletFields: null` gap,
  not a blocker for shipping A1.
- Either way, changing Workday's `id` shape for affected tenants means every currently-indexed row
  on an affected board gets a new id on the next scrape (a one-time version of the bug this fixes),
  and anything referencing today's ids by value (saved jobs, alerts, if either exists downstream)
  breaks for those rows.

**B. The general no-grace-period eviction.** Require an id to be missing across **N consecutive
scrapes** (not just one) before `sync` deletes it, instead of acting on a single absence.

- Fixes Greenhouse's and SuccessFactors's transient-miss pattern (§4) and any future one-off
  scrape hiccup on *any* ATS, not just the two seen here — including SuccessFactors's own
  documented "no title drops the job" mechanism, without needing a SuccessFactors-specific patch.
- Does **not** fix Workday on its own — an id that changes value every run is never observed twice
  in a row, so it would never accumulate the N-run streak needed to confirm removal; A1/A2 are
  still required for Workday specifically.
- Adds a small amount of latency between a job genuinely closing and it leaving the index (N runs'
  worth — at ~19 runs/day, N=2 is well under two hours).

**Recommendation (as originally assessed):** A1 together with B — B closes the general gap that
let Greenhouse and SuccessFactors leak through, with a named mechanism on SuccessFactors's side,
and guards against the next one-off on any ATS. **What actually shipped in PR #265: A1
(reordered per the collision fix above) + A2, without B.** B remains open — Greenhouse's and
SuccessFactors's transient-miss pattern (§4) is unaddressed by this PR and needs its own,
separate decision on `plan_sync`'s grace period. This was exactly the kind of architectural call
CLAUDE.md's "weigh design choices" rule reserves for the user; A1+A2-without-B was that decision,
made explicitly rather than picked silently.

## 7. What would have prevented this

`_posting_key()`'s docstring asserted a fact about Workday's API shape (`bulletFields[0]` is the
req id) that was never verified against real tenant data — it reads as confident and shipped
unquestioned. The same class of bug recurs across this repo's own documented history (ADR-0066:
"a review caught `_RANGE_TAIL` turning a false match... coverage went *up*") — an assumption about
what a field means, made from a plausible-looking sample, unchecked against a wider live pull. A
second, softer lesson from this investigation itself: the first pass here made the same mistake at
smaller scale — sampling 1 id per board and trusting the result, which understated Greenhouse's
and SuccessFactors's real incidence by an order of magnitude until every id was checked. The check
now exists for both (§3, §4): sample widely and completely before trusting a rate, not narrowly
and once.
