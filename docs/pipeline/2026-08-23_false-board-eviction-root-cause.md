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
  `bulletFields[0]` as the stable requisition id. It never is — across 87 confirmed false
  evictions on 25 different boards, `bulletFields[0]` was a location, a relative posted-date, a
  closing-date label, an employment-type tag, a store name, or a company subsidiary name, never a
  requisition id. The derived "id" changes almost every scrape for affected tenants, so `sync`
  evicts-and-reinserts the same live job forever. 24.3% of all fully-checked Workday evictions
  (87/358) were false; among ids evicted **repeatedly** across separate runs — the clearest
  fingerprint of this bug — the rate is 95%.
- **Greenhouse — 7 real false evictions, mechanism proven for 6 of them; the other 7 were never false.**
  Corrected 2026-08-23 by pulling the runs' actual scrape-fragment artifacts (§4.1). Greenhouse's
  API returns **silently short lists** — HTTP 200, valid JSON, no error — and `greenhouse.py` has
  no truncation detection at all, so `sync` evicts against the short list. Measured: `databricks`
  returned **816** jobs in run `32574982652` (821 live), `metrostarsystems` **84** in
  `32612152291` (90 live); the evicted ids were simply absent from those responses, and the shard
  reported the boards as cleanly scraped. The 7th (`metrostarsystems` in `32606136882`) came
  from a run whose scrape was *not* short (90 of 90) — same board, different run, still
  unexplained; see §4.1. **The other 7 (all of `vast`) were not false evictions
  at all** — that board scraped complete (166/166) and those 7 are non-tech (mechanical/thermal/
  structures/test-technician roles), so evicting them from a tech-only index is correct behaviour.
- **SuccessFactors — TWO independent root causes, both fixed.** *Corrected 2026-08-23, after this
  doc first shipped:* the detail-path gap below is real but was **not the whole story, and not the
  larger half**. A second, unrelated bug lived in the *listing*: the `/search/` walk stepped its
  offset by a 25-row floor regardless of the page it actually got, so every tenant serving fewer
  than 25 rows a page silently skipped the difference — `jobs.chartindustries.com` read 90 of 219,
  `jobs.bayer.com` 241 of 601 — and exited by the natural-end path reporting no truncation at all.
  The boards §4's table names below (`chartindustries`, `bayer`, `careers.gic.com.sg`) are exactly
  those sub-25-row tenants, so their evictions are listing losses, not detail losses. See §4.2.
- **SuccessFactors (detail path) — same shape as Greenhouse, confirmed root cause, fixed.** Of the
  22 false evictions on 6 boards originally attributed here, §4's own table apportions 9 of them
  (`chartindustries` 1, `bayer` 5, `careers.gic.com.sg` 3) to boards the listing bug above
  explains, leaving this mechanism **13 on 3 boards** (`jobs-offshore.hanwhaocean.com` 11,
  `careers.bv.com` 1, `careers.hcltech.com` 1). The split is not a convenient guess: measured
  2026-08-23, all three of those boards serve a `urlset` sitemap, so they never run the `/search/`
  walk and the listing bug cannot reach them — while all three boards moved to the listing bug do
  run it, at 10, 10 and 20 rows a page, every one under the 25-row floor. (The original 22 itself came from a
  follow-up pass that resolved the 90 ids the first 120s timeout left inconclusive, finding 2 more
  on `careers.bv.com` and `careers.hcltech.com` — see §4.) Most are clustered into 1-2 runs per
  board, same as Greenhouse.
  Precise mechanism confirmed by code and pinned by a regression test (§4): a detail page that
  loads (200 OK) but yields no parseable title falls through as a dict, not `None`, so the loss
  was invisible to the truncation-detection `mark_truncated` relies on — `sync` reads the board as
  fully scraped and evicts the Job. **Fixed and merged (PR #266)**. The `jobs.bayer.com` artifact
  check once cited here as independent confirmation does **not** support this mechanism — that
  board's shortfall is the listing bug's (see the correction in §4) — so the detail path rests on
  code reading and its regression test, not on artifact evidence. The 13 is an upper bound: none
  of them has been re-checked against §4.1's tech-filter predicate, and Greenhouse's own recheck
  turned half its count into correct evictions.
- **Every other ATS with evictions — clean across every evicted id, not just a sample.** ashby
  (40/40 genuine), darwinbox (4/4), eightfold (62/62), keka (3/3), lever (75/75, plus a
  separately-confirmed 72/72 repeat-eviction check), recruitee (3/3), ripplehire (10/10),
  rippling (1/1), smartrecruiters (22/22), teamtailor (3/3), zoho (112/112) — zero false evictions
  found anywhere, every id resolved (see §1 for the 2026-08-23 follow-up that closed the remaining
  timeouts, and §5.1 for eightfold's own bot-wall detour along the way).

**Fix status.** Workday's id derivation is fixed and merged (PR #265, options A1+A2 in §6).
SuccessFactors's silent detail-loss is fixed and merged (PR #266). **Greenhouse is diagnosed but
not fixed** — the natural per-ATS guard (`meta.total`) is deliberately unshipped because it is
unverified for the failure case, see §4.1. **Option B — the general cross-run grace period in
`plan_sync` — remains open, and is now the strongest remaining lever**: it is the only proposal
that covers Greenhouse without first proving Greenhouse's guard, and it would have absorbed every
false eviction in this document regardless of mechanism.

---

## 1. Scope

15 pipeline runs evicted **1,381** row-events, **1,217 distinct ids**, across **273 distinct
boards**. Every single one of those 1,217 ids was re-fetched live — not sampled:

| ATS | boards checked | ids: still live (false) | ids: confirmed gone (genuine) | ids: inconclusive | boards w/ ≥1 false evict |
|---|---:|---:|---:|---:|---:|
| workday | 148 | **87** | 271 | 0 | **25** |
| greenhouse | 24 | **7** (was 14 — see §4.1) | 325 | 0 | **2** |
| successfactors | 45 | **22** (upper bound — see §4) | 170 | 0 | **6** |
| zoho | 9 | 0 | 112 | 0 | 0 |
| eightfold | 20 | **0** | 62 | 0 | 0 |
| lever | 3 | 0 | 75 | 0 | 0 |
| ashby | 5 | 0 | 40 | 0 | 0 |
| smartrecruiters | 9 | 0 | 22 | 0 | 0 |
| ripplehire | 2 | 0 | 10 | 0 | 0 |
| darwinbox | 4 | 0 | 4 | 0 | 0 |
| recruitee | 1 | 0 | 3 | 0 | 0 |
| keka | 1 | 0 | 3 | 0 | 0 |
| teamtailor | 1 | 0 | 3 | 0 | 0 |
| rippling | 1 | 0 | 1 | 0 | 0 |

Zero inconclusive remain anywhere in the table — a 2026-08-23 follow-up pass resolved every id that
the original 120s-per-board timeout left unresolved for successfactors (90), zoho (54), and
smartrecruiters (9), plus every eightfold id the original pass couldn't reach at all (62).
successfactors/zoho/smartrecruiters needed nothing but patience: none of these boards are
bot-walled, they just have large or slow catalogs, and a **listing-only** fetch (skip the per-job
description pass; presence-checking never needed it) resolved all of them in
seconds-to-tens-of-seconds per board once the detail-fetch cost was cut out. Two of those checks
turned up real false evictions that the original pass's timeout had hidden — see the successfactors
update in §4.

**Eightfold needed a different fix and turned up a real bot wall along the way, but is now fully
resolved: 62/62, zero false evictions.** The original text here said eightfold "could not be
live-checked from this environment at all," inferred from the pipeline's own sudo-gated-WARP scrape
logs. Going and hitting the real boards directly instead (per CLAUDE.md's measurement rule) told a
more nuanced story: plain HTTP reached real content on every board at first (13 boards, 42 ids
resolved that way), then a rate-based AWS WAF Bot Control rule tripped after a burst of concurrent
pagination and walled every remaining board behind a real interactive CAPTCHA — confirmed directly
that neither plain HTTP nor a real JS-enabled Chrome browser can clear it non-interactively. The
wall's cooldown turned out to be real, though: waiting it out and switching the last 7 boards to a
single cheap sitemap request each (rather than paginated API calls) closed out the remaining 20 ids
without re-tripping it. Full account, including what didn't work, in §5.1.

**76 of 77** ids evicted more than once across separate runs (out of the 15) are Workday — repeat
eviction, the clearest fingerprint of an id that never stabilizes, is almost exclusively a Workday
phenomenon. The one non-Workday repeat (`successfactors:careers.hcltech.com:1364226855`, evicted in
runs `32574982652` and `32585966142`) was originally asserted here to be "genuinely absent" —
that was wrong, stated before this id had actually been checked (it fell inside successfactors's
120s-timeout inconclusive bucket at the time of writing). The 2026-08-23 follow-up pass live-checked
it: **it is still present**, a false eviction, not ordinary churn. Given careers.hcltech.com is a
10,700+-job board with per-job detail fetches (§4's documented transient-miss mechanism), the same
job unluckily missing its detail fetch twice in 15 runs is a plausible coincidence rather than a new
mechanism — but it means the repeat-eviction fingerprint is not *quite* Workday-exclusive: 76 of 77
are Workday, this one is a successfactors transient miss that happened to recur.

**And the two absences were not consecutive**, which matters enough to record because ADR-0083's
choice of N=2 rests on it. `careers.hcltech.com` was in the slice of **all 15** runs, so the two
evictions (`32574982652`, `32585966142`) had three scrapes between them; the scrape-fragment for
the middle one, `32579833859`, lists **9,230** jobs for the board and contains `1364226855`
among them. So the id went absent, came back, and went absent again — never twice running. The
eviction mechanics force that shape anyway (a second eviction requires a re-add, which requires
reappearing in `fresh_ids`), but it is checked here rather than argued: a grace period keyed on
*consecutive* absences only helps if the observed misses really are isolated, and this was the
one case that could have shown otherwise.

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
the 25 affected boards. The 87 confirmed Workday false-eviction ids show `bulletFields[0]` playing at
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

Two of the 87 (`cba/CommBank_Careers:REQ261245`, `frenckengroup/External:JR101259`) *look*
req-id-shaped and still false-evicted — worth the caveat that a plausible-looking id isn't proof
of stability either; a job can also close and reopen under a fresh req id, which is
indistinguishable from this bug by id shape alone. These two don't overturn the pattern (85 of
87 are unambiguously non-req-id text) but they're a reason a fix should verify stability
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
`src/headstart/scrapers/successfactors.py:353`: `id=f"{self.ats}:{self.slug}:{item['id']}"`) — so
this isn't Workday's mechanism. What they share instead: every false-evicted id on a given board
was evicted in the *same one or two pipeline runs*, not scattered across the 15-run window —

| board | false evictions | run(s) they were evicted in |
|---|---:|---|
| greenhouse `databricks` | 2 | one run (`32574982652`) |
| greenhouse `metrostarsystems` | 5 | two runs (`32606136882`, `32612152291`) |
| ~~greenhouse `vast`~~ | ~~7~~ | **not false evictions — non-tech, correctly evicted (§4.1)** |
| successfactors `careers.gic.com.sg` | 3 | two runs (`32568669902`, `32579833859`) |
| successfactors `jobs-offshore.hanwhaocean.com` | 11 | one run (`32594712165`) |
| successfactors `jobs.chartindustries.com` | 1 | one run (`32571222780`) |
| successfactors `jobs.bayer.com` | 5 | one run (`32592349834`) |
| successfactors `careers.bv.com` | 1 | one run (`32571222780`) |
| successfactors `careers.hcltech.com` | 1 | two runs (`32574982652`, `32585966142`) |

The last two rows are 2026-08-23 additions: `careers.bv.com` and `careers.hcltech.com` were part of
the 90 successfactors ids the original 120s-per-board timeout left inconclusive; a follow-up
listing-only pass resolved them and found both still live. `careers.bv.com` fits the single-run
clustering pattern exactly. `careers.hcltech.com:1364226855` doesn't — it was evicted in two
*separate* runs, the repeat-eviction fingerprint §1 otherwise treats as Workday-exclusive. It isn't
read as a second mechanism: careers.hcltech.com is this ATS's largest board by a wide margin
(10,700+ jobs, each needing its own detail fetch), so the same posting unluckily missing its detail
fetch twice in 15 runs is well within what "transient" can produce on a board that size, not
evidence the id itself is unstable the way Workday's derived keys are.

Genuine closures happen at different times for different postings and would scatter across the
15-run window; several-to-a-dozen ids all vanishing from one board in the *same single run* is
the signature of that one scrape being incomplete, not several unrelated real closures lining up.

**SuccessFactors: confirmed root cause, fixed.** The module docstring already named the shape —
*"A page that yields no title drops that job for the run... it returns next scrape"* — but the
precise gap was narrower and code-confirmed, not just inferred from the comment.
`_job_fields()`/`_job_fields_async()` (`src/headstart/scrapers/successfactors.py:324-338`)
returned `None` — the signal `report_detail_gaps` counts as a loss and that feeds `mark_truncated`
(ADR-0053) — **only on a hard fetch failure** (non-200, or an exception `fan_out` isolates to
`None`). A page that loaded fine (200 OK) but whose content didn't yield a parseable title (a
temporary placeholder, an anti-bot interstitial served with 200, any page shape neither parser
recognizes) fell through `_page_fields()`, which **always returns a dict, never `None`**, even
when every field inside it is empty. `parse()` correctly still drops that Job (`if not title:
continue` — there's nothing to keep it by), but that specific loss was invisible to
`report_detail_gaps`, so `mark_truncated` never fired for it: `index sync` read the board as
fully, authoritatively scraped and evicted the Job as a delisting, though nothing ever told the
scraper its list was short. Confirmed by a regression test that reproduces exactly this
(`_job_fields` succeeds with 200, the page body has no title in any shape the parser knows) and
shows `scraper.truncated` staying `None` on the unfixed code.

**Fixed**: a new `_titled_fields()` wraps `_page_fields()` and returns `None` when the parsed
`title` is empty, so a title-less-but-200 page now counts as a loss the same way a fetch failure
already did — closing the gap without touching `_page_fields()`'s own contract (still used
directly, unchanged, by three existing unit tests). Merged as PR #266.

**Independently confirmed by the same artifact method as §4.1**, for one board: `jobs.bayer.com`
in run `32592349834` scraped **242** jobs and all 5 of its evicted ids were **absent** from that
raw output, while `_shard_report.json` recorded the board with **no error and no truncation** —
a silent-loss signature. Note this is the opposite finding from Greenhouse's `vast`: these ids
really were missing from the scrape, not present-but-non-tech.

> **Correction, 2026-08-23 (later the same day).** This paragraph originally read that as *"the
> exact silent-loss signature **the fix** closes"*, meaning the detail-path fix. That attribution
> is wrong. §4.2 shows `jobs.bayer.com` was reading **241 of 601** postings because of the
> listing-side stride bug — so 242 scraped jobs is that bug's signature, not the detail path's,
> and these 5 absences are explained by it. The detail-path gap in this section is real, code-
> confirmed and worth fixing on its own, but **this board is not evidence for it**, and §4 is
> left without an independent artifact confirmation of the detail mechanism specifically. The
> caveat below applies with more force, not less.

**Caveat on the count.** Only `bayer`'s 5 were re-checked this way; the other 17 of the 22 have
not been re-tested against §4.1's tech-filter predicate, so some may turn out to be correct
evictions of non-tech jobs rather than false ones, exactly as 7 of Greenhouse's 14 did. The fix
itself does not depend on that number — the gap it closes is real and demonstrated — but the 22
should be read as an upper bound until re-audited.

### 4.1 Greenhouse — corrected 2026-08-23, from inference to proof

An earlier pass called Greenhouse's mechanism "inferred, not proven" (a suspected large-payload
timeout) and put its false-eviction count at 14. Both were wrong, and the method that settled it
is worth keeping: **the pipeline's own `scrape-fragment-N` run artifacts are still on GitHub, and
they contain the exact raw scrape output for that run.** Instead of reasoning about what the
scrape *might* have returned, download the fragment for the shard that owned the board (find it in
the same run's `scrape-assignments` artifact) and read what it actually got, alongside
`_shard_report.json`'s own `errors`/`truncated` entries for that board. That turns "was this a
scrape miss?" from an inference into a lookup.

**Finding 1 — 7 of the 14 were never false evictions.** All 7 `vast` ids were **present** in the
raw scrape, which was **complete** (166 of 166 — no shortfall at all). They were evicted because
they are not tech: `Senior Mechanical Engineer, Thermal Control Systems`, `Manager, Loads &
Dynamics`, `Test Technician (Second Shift)`, `SMT Test Technician`, and three like them, on
departments `Station Engineering` / `Structures and Dynamics` / `Fluid Systems`. `index sync`
takes its fresh ids from `data/jobs/tech/` (`index.py:92`, `_SOURCE`) while taking its eviction
*scope* from the full pre-filter scrape — deliberately, and documented in that module's own header.
So a scraped-but-non-tech job is **correctly** evicted from a tech-only index. The original
verification asked "is this job still on the company's careers board?", which is the wrong question
here: for a tech-filtered index the test is "still on the board **and** still tech". That flaw
inflated Greenhouse's count and would inflate any future audit run the same way — see §7.

**Finding 2 — the remaining 7 are real, and the mechanism is now directly observed.** Greenhouse's
API returned a **silently short list**: HTTP 200, valid JSON, no error, no truncation reported.

| board | run | jobs in that run's raw scrape | live now | evicted ids present in the scrape? | shard report |
|---|---|---:|---:|---|---|
| `databricks` | `32574982652` | **816** | 821 | no — both absent | no error, no truncation |
| `metrostarsystems` | `32612152291` | **84** | 90 | no — all 4 absent | no error, no truncation |
| `metrostarsystems` | `32606136882` | 90 | 90 | no — the 1 absent (composition differed) | no error, no truncation |

All 5 `metrostarsystems` ids are genuinely tech once their real departments are read
(`National Security`, `USCIS ESIS`, `AFM`, `DOS INR Cyber`, `DOS ADD` — a `?content=true` fetch
carries departments, the bare endpoint does not), as are both `databricks` ids (two
`Senior Software Engineer` roles). So these 7 are true false evictions.

This also retires the earlier "client-side truncation is ruled out" reasoning. That reasoning was
correct as far as it went — `fetch_raw()` is `json.loads(self._get())`, all-or-nothing, so a torn
download raises rather than yielding a short list — but it only ruled out *our* side. The short
list came from the origin already short and well-formed, which no client-side parse can detect.

**`greenhouse.py` has no truncation detection at all** — unlike `workday`, `eightfold`,
`successfactors`, `sensehq`, `darwinbox`, `join`, `ripplehire` and `smartrecruiters`, which all
call `mark_truncated`. It is the only scraper in this class that cannot report a short board.

**About `meta.total` — the obvious guard, deliberately NOT shipped on this evidence.** The
response carries a `meta` object, and it holds exactly one field: `{"total": N}`, the board's own
count. `greenhouse.py` never reads it (`parse()` takes `raw.get("jobs", [])`; the only occurrence
of "meta" in the file is an unrelated docstring line) — an omission, not a decision. A
`len(jobs) != meta.total -> mark_truncated` guard is the natural fix and is three lines. **But it
is unverified for the case that matters**: a sweep of **602 live boards** found `len(jobs)` and
`meta.total` agreeing **602/602** — which only establishes that they agree when the response is
healthy. Whether `total` stays authoritative *during* a partial response is exactly what would
make the guard work, and it cannot be recovered retroactively (the fragments hold parsed jobs, not
the raw envelope). Per CLAUDE.md's own rule — "a plausible-sounding guard built on an assumed
response is worse than none", learned from the #160 guard that died on contact — this is left
unshipped pending a real captured partial response, rather than merged on a guess.

**That capture is now instrumented** (same PR): `GreenhouseScraper.fetch_raw` logs a warning when
`len(jobs) != meta.total`, and does nothing else — no `mark_truncated`, no behaviour change.
Reading the result needs care in both directions. A warning means the envelope contradicts itself,
so the guard would fire on a real short response — ship it. **Silence is ambiguous on its own**:
it could mean no board went short, or that `total` shrank in step with `jobs` and the guard is
worthless. Disambiguating needs a board *known* to have gone short that run — diff its job count
across two runs' `scrape-fragment` artifacts, the method this section used — and then checking
whether a warning fired for it. Short board **with** warning confirms the guard; short board
**without** one kills it. Until one of those two things is observed, Greenhouse stays unfixed at
the per-ATS level, and Option B (§6) is what actually covers it. **Tracked as issue #268**, so
the instrumentation has an owner rather than sitting unread.

The one case this section does **not** explain: `metrostarsystems` in run `32606136882` scraped
**90 of 90** — not short at all — yet `7797942003` was absent from it while a different posting
was present. A same-size response with different membership is not the short-list mechanism, and
nothing here accounts for it; a brief unpublish/republish on Greenhouse's side would, but that is
inference, not evidence. Six of the seven are explained; this one is honestly open.

**Both ATSes still share the same general root cause (§6):** `plan_sync` treats one scrape's
absence as authoritative with no cross-run confirmation. Greenhouse's origin-side short list and
SuccessFactors's silent detail-fetch loss are different mechanisms that meet at the same place —
and Option B absorbs both **without needing to know either mechanism**, which is exactly why it
is the stronger fix for Greenhouse specifically, where the per-ATS guard is still unproven.

### 4.2 SuccessFactors — a second root cause, in the listing, found 2026-08-23

The detail-path fix (PR #266) shipped and the boards **kept flapping**. Chasing that (issue #269,
`/diagnosing-bugs`) found a second bug that is independent of the first, larger, and older.

`_search_job_urls` advanced its offset by `startrow += max(len(found), _SEARCH_STEP_FLOOR)`, with
the floor at 25. `jobs.chartindustries.com` serves **10 rows a page**, so the walk read rows 0-9,
then 25-34, then 50-59 — **skipping 15 of every 25 rows, permanently**. It then ran off the end,
saw no fresh ids, and exited by the *natural-end* path with `cut_short=None`. A board missing 59%
of its postings was handed to `index sync` as complete, and everything unread was evicted as
delisted. The module docstring already claimed the correct behaviour — *"pagination steps by the
observed size"* — the code just never did it.

Measured live against the total each board advertises in its own pagination label:

| board | page size | board says | walk got | missing |
|---|---:|---:|---:|---:|
| `jobs.chartindustries.com` | 10 | 219 | **90** | **129** |
| `jobs.bayer.com` | 10 | 601 | **241** | **360** |
| `mycareer.heraeus.com` | 20 | 222 | **180** | **42** |
| `careers.gic.com.sg` | 20 | 171 | **140** | **31** |
| `jobs.hollister.com` | 25 | 98 | 98 | 0 |
| `jobs.exxonmobil.com` | 25 | 606 | 606 | 0 |

Every board paging under 25 was short; every board at or above it was whole. That is the floor and
nothing else — `ceil(219/25) = 9` windows x 10 rows = the 90 observed.

**Why it presents as flapping rather than as a stable shortfall:** as new postings shift rows down
the server's ordering, a job crosses in or out of one of the sampled windows, so it appears and
disappears between runs — evicted, then re-added, each re-add re-stamping `first_seen` (ADR-0031)
and surfacing a year-old posting as brand new. That is the user-visible symptom in issue #269. On
the run-pair from that issue, 19 of 90 chartindustries ids swapped between two runs 46 minutes
apart, which is not plausible churn.

Fixed in PR #274, two parts: step by the board's own stated page size (falling back to the link
count), and check what was read against the advertised total before claiming the end, reporting a
shortfall through the ADR-0053 channel. A caveat worth carrying forward — **the link count is not
the page size**: `jobs.kaufland.com` labels 15 results but renders 19 `/job/` links (4 recurring
extras), so stepping by what you counted reintroduces the same skip at a smaller scale. The
board's own figure is the only trustworthy stride. The self-check covers only the ~17 of 30
sampled tenants that render a label; the stride fix covers all of them.

## 5. Every other ATS

ashby, darwinbox, keka, lever, recruitee, ripplehire, rippling, smartrecruiters, teamtailor, zoho
— **every evicted id across the full 15-run window**, not a sample, was genuinely gone from its
real board. Code review of every scraper's id construction confirms all of them key off the
platform's own raw id field (`j['id']`, `o['id']`, `p['id']`, `jid`, `item['id']`, `jobSeq`) — none
use a derived/mutable-text fallback like Workday's. zoho (54 ids), smartrecruiters (9 ids), and a
portion of successfactors's total (90 ids) originally hit the 120s per-board timeout on unusually
large or slow catalogs; a 2026-08-23 follow-up pass (§1) resolved every one via a listing-only
fetch (skipping the per-job description pass that presence-checking never needed) — all confirmed
genuinely gone except 2 successfactors ids, folded into §4's count.

### 5.1 Eightfold — 62/62 resolved, zero false evictions, and a real bot wall found along the way

The original text here said eightfold "could not be live-checked from this environment at all," on
the strength of the pipeline's own scrape logs (sudo-gated WARP rotation failing non-interactively).
A 2026-08-23 follow-up went and hit the real boards directly instead of inferring from those logs,
per CLAUDE.md's measurement rule. The picture was more nuanced than either "fully blocked" or
"fully reachable" — but every id got resolved in the end:

**Most of it just worked over plain HTTP.** `GET /careers` and `GET /api/pcsx/search` both answered
200 from this environment, direct and unproxied, on boards the pipeline's own logs show as fully
bot-walled. That resolved 13 boards cleanly by calling the same listing-only internals used for
successfactors above (`_group_id()` + paginated `/api/pcsx/search`, or the sitemap fallback — no
per-job description fetch, presence-checking doesn't need it): 42 of the 62 evicted ids, all 42
genuinely gone.

| board | ids resolved | method |
|---|---:|---|
| ascendion, nab, faurecia, haleon, ericsson, globalfoundries, kraftheinz, vale | 10 | api, single sweep |
| careers.qualcomm.com | 1 | api, 3 sweeps, 1891/1891 complete |
| bms.eightfold.ai | 2 | api, single sweep, 629/675 (93%) |
| microsoft.eightfold.ai | 10 | sitemap, complete (2,083 jobs) |
| caci.eightfold.ai | 14 | api, 2 sweeps, 1661/1714 (97%) |
| citi.eightfold.ai | 5 | api, 2 sweeps, 2652/3414 (78%) |

**Then a real, volume-triggered bot wall showed up.** Concurrent pagination (needed for the larger
boards above to finish in reasonable time — sequential single-threaded fetching works but would
have taken the better part of an hour across the remaining boards) tripped a rate-based AWS WAF Bot
Control rule — measured from the request log at the point the first 405 appeared, roughly 350-400
requests in quick succession. Once tripped, **every eightfold
board — not just the one being fetched — started answering `GET /careers` and the API with HTTP 405
and an actual "Human Verification" page** (`awswaf.com` challenge.js + captcha.js, a real
image-CAPTCHA widget, not a silent proof-of-work). This is the mechanism ADR-0063 already documents
in the scraper's own code comments ("403 and 405 are the two shapes this edge returns once a
shard's per-origin budget is spent") — direct evidence of it, not an inference this time.

Two things were tried to get past it while it was up, per this task's instruction to route around
the wall via a real browser rather than the sudo-gated WARP rotation — **neither worked**:
- **`headstart.browser_http`** (this repo's real-Chrome-via-CDP transport, built for darwinbox's
  Cloudflare wall) — navigated the tab to a walled `/careers` URL and still got the 405 CAPTCHA
  page back. Expected on reflection: that transport deliberately blocks all `.js` requests on every
  tab (`_install_blocking`, `docs/darwinbox/cloudflare-wall.md` — darwinbox's wall renders nothing
  and Turnstile never runs, so blocking JS is free performance there) — but eightfold's wall
  requires `challenge.js`/`captcha.js` to run to have any chance of clearing, so this transport's
  core design assumption is inverted for this wall.
- **A standalone headful Chrome via `pydoll` directly** (not `browser_http` — a fresh script, JS
  *allowed*, `--disable-blink-features=AutomationControlled` set), navigating to a walled `/careers`
  page and waiting 8s for `challenge.js` to run and any silent auto-clear to happen: the page still
  showed "Human Verification" after the wait, and the API still 405'd. This is a real interactive
  CAPTCHA (`CaptchaScript.renderCaptcha` rendering an actual puzzle widget), not a silent
  device-fingerprint challenge a script-driven browser can pass just by existing.

**What actually cleared the rest: waiting, then dropping the per-board request count back to one.**
The wall's cooldown turned out to be real: a subset of boards (bms, microsoft) answered clean again
on the next probe, timestamped roughly 15-20 minutes after the first trip with no further requests
in between — an observed gap, not a documented cooldown window. Resuming even
moderate concurrent pagination (10 workers) against 2 more boards re-tripped it within a couple of
minutes and walled the remaining 7 boards before they could be reached — confirming the budget is
small and shared across the whole `eightfold.ai` + vanity-domain fleet, not per-tenant. Waiting it
out a second time and switching those last 7 boards (`dexcom`, `eaton`, `ford`, `jobs.nvidia.com`,
`ngc`, `portal.careers.hsbc.com`, `worley` — 20 ids) to **one sitemap request each** instead of
paginated API calls closed out every remaining id without a third trip: all 20 genuinely gone. The
sitemap counts line up with the API's own `data.count` from the earlier probe on every board
(dexcom 268/268, ford 567/567, ngc 3666/3666, worley 1190 vs 1183, eaton 2268 vs 2271, nvidia 2651
vs 2650 — the small deltas are most likely ordinary listing churn between the two probes rather
than missed pages, but that reading isn't independently verified; either way none of the 20
evicted ids fell in a gap, so it doesn't change the false-eviction count).

**Net result: 62/62 eightfold ids resolved, zero false evictions, zero boards affected.** Its id
scheme is exactly what §5's clean group uses — a real, numeric, platform-issued `position_id`
(`_POSITION_ID = re.compile(r"/careers/job/(\d+)")`,
`src/headstart/scrapers/eightfold.py:64,272-273,340,501-503`) — and the full check confirms it
behaves like the rest of that group, not like Workday's derived-key bug. The bot wall is real and
worth remembering for future eightfold work (evidence for ADR-0063, and a caution that
`browser_http` is the wrong tool for a wall that needs JS to run, not one that needs JS blocked),
but it did not end up being a blocker for this investigation's actual question — a single
sitemap-per-board pass stays well under whatever the rate-based rule's threshold is, even right
after a fresh trip.

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
  happened to have an eviction in the 15-run window). The subagent's originally-validated
  implementation (**superseded** — see the collision fix below for the version actually shipped):

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

  **The actual shipped `_posting_key()`** (`src/headstart/scrapers/workday.py`), reordered per
  the collision fix and folding in A2's `jobReqId` tier (below) — this is the version to copy,
  not the one above:

  ```python
  def _posting_key(item: dict[str, Any]) -> str:
      detail_req_id = (item.get("_detail") or {}).get("jobReqId")
      if detail_req_id:
          return str(detail_req_id)
      bullet_fields = item.get("bulletFields") or []
      candidates = [f for f in bullet_fields if isinstance(f, str) and _looks_like_req_id(f)]
      if candidates:
          return re.sub(r"\s+", "", candidates[0])
      tail = (item.get("externalPath") or "").rsplit("/", 1)[-1]
      if tail:
          return tail
      bullet = (bullet_fields or [None])[0]
      return str(bullet) if bullet else "unknown"
  ```

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

**A third lesson, from this investigation's own wrong answer (§4.1).** "Is the job still on the
company's careers board?" was used as the definition of a false eviction. For a **tech-filtered**
index that is the wrong predicate, and it silently inflated Greenhouse's count from 7 to 14: a
non-tech job that is still posted is *supposed* to be evicted. The correct predicate is "still on
the board **and** still passes `tech_filter.classify()`" — and it needs the job's **department**,
not just its title, because department flips the answer both ways (Greenhouse's bare `/jobs`
endpoint omits departments; only `?content=true` carries them, and every one of the five
`metrostarsystems` ids reads differently with and without). Any future eviction audit should
classify before concluding.

**And a method worth reusing.** The thing that finally settled Greenhouse — after a shared-
`updated_at` correlation looked compelling and then collapsed under its own control (all 166
`vast` jobs shared the timestamp, so it carried no signal at all) — was not more reasoning about
the scraper. It was reading the pipeline's own `scrape-fragment-N` artifacts, which persist on
the run and contain the literal raw scrape output plus a `_shard_report.json` naming every board
that errored or truncated. When a question is "what did this run actually see?", that artifact
answers it directly; several hours of plausible inference here were worth less than one download.
