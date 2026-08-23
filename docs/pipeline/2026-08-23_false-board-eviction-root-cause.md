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
- **SuccessFactors — same shape as Greenhouse, confirmed root cause, fixed.** 22 false evictions
  on 6 boards (updated 2026-08-23: a follow-up pass resolved the 90 ids the original 120s timeout
  left inconclusive and found 2 more false evictions, on `careers.bv.com` and
  `careers.hcltech.com` — see §4). Most are clustered into 1-2 runs per board, same as Greenhouse.
  Precise mechanism confirmed by code and pinned by a regression test (§4): a detail page that
  loads (200 OK) but yields no parseable title falls through as a dict, not `None`, so the loss
  was invisible to the truncation-detection `mark_truncated` relies on — `sync` reads the board as
  fully scraped and evicts the Job. Fixed in `fix/successfactors-truncation-detection`, not yet
  merged.
- **Every other ATS with evictions — clean across every evicted id, not just a sample.** ashby
  (40/40 genuine), darwinbox (4/4), eightfold (62/62), keka (3/3), lever (75/75, plus a
  separately-confirmed 72/72 repeat-eviction check), recruitee (3/3), ripplehire (10/10),
  rippling (1/1), smartrecruiters (22/22), teamtailor (3/3), zoho (112/112) — zero false evictions
  found anywhere, every id resolved (see §1 for the 2026-08-23 follow-up that closed the remaining
  timeouts, and §5.1 for eightfold's own bot-wall detour along the way).

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
| successfactors | 45 | **22** | 170 | 0 | **6** |
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
`_job_fields()`/`_job_fields_async()` (`src/headstart/scrapers/successfactors.py:285-299`)
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
directly, unchanged, by three existing unit tests). `fix/successfactors-truncation-detection`,
not yet merged.

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

Two things ruled out attempting to build a Phase-1 feedback loop (`/diagnosing-bugs`) for this
specifically: `fetch_raw()` uses the default `BaseScraper.fetch_raw` (`json.loads(self._get())`)
— an all-or-nothing parse, so a torn/truncated download raises rather than silently yielding a
shorter-but-valid job list, which rules out simple client-side truncation as the mechanism for a
handful of *specific* jobs going missing while hundreds of others in the same response survive.
And a differential poll (`databricks`/`metrostarsystems`/`vast`, 5 rounds, 3s apart) found zero
flapping — expected, given the historical incidents are 1-2 per board across a 15-hour, 15-run
window; a few seconds of polling isn't the right timescale to catch something that rare, so this
neither confirms nor rules out a genuine Greenhouse-side transient state. No further client-side
avenue was found; per the skill's own guidance, this is reported as genuinely unconfirmed rather
than forcing a specific fix onto unverified evidence. Greenhouse remains a candidate for Option B
(§6) — a general grace period would absorb whatever this turns out to be without needing to know
the exact mechanism.

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
