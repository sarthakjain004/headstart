# Eightfold: SmartApply recovers the ~20% PCSX-403 class

**Date:** 2026-09-11 · **Fixes:** the scraper module docstring's "the `/api/apply/v2/jobs` 403 is
a different, apply-flow namespace" / "403-hardened" dismissal, which was wrong · **Code:**
`src/headstart/scrapers/eightfold.py` (`_smartapply_search`, `_smartapply_to_pcsx_shape`,
`_pcsx_disabled`)

## The claim that turned out to be wrong

The scraper's own docstring called `/api/apply/v2/jobs` ("SmartApply") "403-hardened" and
dismissed it as a different, apply-flow namespace not worth probing. That was never verified live
— it was carried over from the original research phase. Probed live 2026-09-11 against 3 tenants
named in the docstring's own fallback class (bayer, hsbc, libertymutual): all three answered
SmartApply with a plain HTTP 200 and a full position list, no auth, no cookies, no TLS spoofing
beyond this codebase's usual `curl_cffi` transport.

## Measured population: 23/23 (100%) recovered

Every one of the 103 live eightfold tenants in `data/validate/liveness/eightfold.csv` was probed
for its `_EF_GROUP_ID` and then its PCSX search first page. **23 (22.3%) 403'd** — matching the
scraper docstring's "~20%" estimate — all with a body naming PCSX explicitly, in one of two
locales:

```
"PCSX is not enabled for this user."                       (21 tenants)
"PCSX no está habilitado para este usuario."                (2 tenants: coca-colafemsa, oxxo)
```

All 23 were then probed against `/api/apply/v2/jobs?domain={d}&query=&location=&start=0&
sort_by=timestamp`: **23/23 answered 200 with a real position list.** Sample counts (SmartApply's
own `count` field): bayer 611, hsbc 1,634, libertymutual 220, fluor 624, netflix 492, tevapharm
591, stmicroelectronics 519, costar 399, coca-colafemsa 789, and 14 more, down to bluecrabconsulting-
class boards in the tens. Full tenant list and raw responses: the probe scripts that produced this
were run inline against the live API (not checked into the repo — this doc records the results).

## Pagination and replica stability

Same page shape as the primary search: `start` increments by 10, page size fixed at 10 (matching
the primary API). Full-pagination crawls on 2 tenants matched `count` exactly with **zero**
duplicate ids (albemarle: 43/43, mm-group: 49/49).

**No replica disagreement measured**, unlike the primary PCSX search (`pcsx-replica-instability.md`,
#142). The same-offset double-fetch method that method used was repeated here: 8 probes (offset 0
and a mid-list offset, 3-6s apart) across 4 tenants (fluor, costar, bayer, hsbc) — **0 ids differed,
0 order changes**, on every probe. `_smartapply_search` therefore does one straight pass with a
dedupe-by-id as a cheap safety net, not `_api_search`'s multi-sweep reconciliation — that
asymmetry is a measured fact about this surface, not an assumption.

## Field-shape differences from the primary search

A SmartApply `positions[i]` entry is differently shaped from a primary `/api/pcsx/search`
position. Sampled live across the 23 tenants (230 positions, first page of each):

| primary (`/api/pcsx/search`) | SmartApply (`/api/apply/v2/jobs`) | notes |
|---|---|---|
| `name` | `name` | same key |
| `locations` | `locations` | same key, same shape |
| `standardizedLocations` | *(absent)* | repair tier in `_first_location` is skipped, not broken |
| `department` | `department` | same key, **but list-shaped on 1/23 tenants** (fluor — every sampled position had `["Quality"]`-style values; all 22 others were plain strings) |
| `postedTs` | *(absent — synthesized from `t_create`)* | see below |
| `workLocationOption` | `work_location_option` | same value vocabulary (see below) |
| `positionUrl` | `canonicalPositionUrl` (absolute, sometimes cross-host) | not carried through — see below |
| — | `job_description` | **always `""`** in the 230 sampled positions — SmartApply's listing never carries description text, matching the primary search (description is always a separate detail fetch on both surfaces) |

**`department` non-null rate**: 191/230 (83%) across the sample. Two tenants (lgcns, mm-group)
had 0/10 non-null — consistently absent, not a sampling artifact (checked the full first page on
each). The sitemap fallback these tenants previously used could never carry `department` at all
(`_jobposting()` hardcodes `"department": None,  # not in the JSON-LD`), so even the two blank
tenants are no worse off, and the other 21 gain a field they never had.

**`work_location_option` vocabulary observed**: `onsite`, `hybrid`, `remote_local`,
`remote_global` — all four already keys (or intentionally absent, for `hybrid`) in the scraper's
`_REMOTE_OPTION` mapping. No new vocabulary to add.

**`postedTs` mapping**: SmartApply carries no `postedTs` field. It has `t_create` and `t_update`
(both unix seconds). `_smartapply_to_pcsx_shape` maps `t_create` → `postedTs`, since "when the
posting was created" is the closer semantic match to the primary search's field than `t_update`
(which moves on every edit to the posting, e.g. Bayer sample job: `t_create` 2026-09-07,
`t_update` 2026-09-10 — a 3-day gap from one edit). This is a judgment call, not a measured
certainty — SmartApply's own numbers were checked against the same job's `position_details`
response (below) but that endpoint's `postedTs` (a real, stable value — not `t_create` or
`t_update` exactly) doesn't resolve which listing field it derives from.

**`posting_name`, `ats_job_id`, `display_job_id`**: present on every sampled position but not
carried into the translated shape — none of them is needed. `id` (the numeric PCSX position id,
e.g. `563087417450078`) is what every Job's identity and `position_details` lookup already key
on, on both surfaces, and it's populated directly (not only reachable via `ats_job_id`/
`display_job_id`, which are separate human-readable job codes with no equivalent field on the
primary search either). `posting_name` duplicates `name`. End-to-end runs against real tenants
(albemarle, fluor) produced correct ids (e.g. `eightfold:albemarle.eightfold.ai:1099555988595`,
matching the tenant's real position id) and working `/careers/job/{id}` links, confirming `id` is
the right key live, not an assumption.

**`positionUrl` / URL construction**: deliberately **not** carried through. SmartApply's own
`canonicalPositionUrl` is absolute and sometimes points at a *different* vanity hostname than the
tenant's `self.slug` — e.g. `bayer.eightfold.ai`'s SmartApply response carries
`canonicalPositionUrl: https://talent.bayer.com/careers/job/562949978519307`. Both hosts serve the
identical page (`bayer.eightfold.ai/careers/job/{id}` also returns HTTP 200, byte-identical body
length, verified live), so this isn't a correctness bug either way — but leaving `positionUrl`
absent lets the existing `_api_records` fallback (`f"/careers/job/{position_id}"` on `self.slug`)
build the URL exactly as it always has, keeping `url()` consistent with what `URL_SHAPES['eightfold']`
(`scripts/eval/verify_filters.py`) already expects. Verified live on all 4 sampled tenants
(bayer/talent.bayer.com, fcx/talent.fmjobs.com, and 2 more): the `self.slug`-hosted URL 200s in
every case.

## Detail-endpoint compatibility

`GET /api/pcsx/position_details?position_id={id}&domain={d}&hl=en` — the *same* endpoint the
primary path uses for descriptions — was probed with a SmartApply-sourced id on all 23 tenants
(the first position of each tenant's first page): **23/23 (100%) returned HTTP 200 with a
non-empty `jobDescription`.** No changes needed to `_description`/`_description_async`/
`_read_description`; `_api_records`'s existing detail-fanout code is reused unmodified — the
translated SmartApply positions flow through exactly the same code path the primary search's
positions always have.

## `DERIVATIONS_VERSION` — no bump

`headstart.ingest.doc_prep.DERIVATIONS_VERSION` governs re-derivation of exactly the fields
`experience.extract()`, `salary.extract()`, `extract_remote()`, and `classify_country()` produce
from a Job's stored raw inputs (`experience`, `salary`, `description`, `title`, `remote`,
`location`, `ats`). This change touches none of those inputs' *derivation logic* — it's a scraper
fix, not a change to `experience.py` or `salary.py`.

The one already-indexed field this recovers is `department`, which:
- is not one of `META_FIELDS`' derived columns — it's a raw passthrough from the scrape record,
  copied straight through `to_meta()`'s `{field: job.get(field) for field in META_FIELDS}`
- is not read by `extract()`, `extract_salary()`, `extract_remote()`, or `classify_country()` —
  none of those four functions take `department` as an argument

So this change cannot alter what any of those four functions return for input that's already been
scraped — the exact bar CLAUDE.md's `DERIVATIONS_VERSION` rule sets for skipping a bump ("provably
inert on anything already stored"). `description` was already being recovered by these 23 tenants'
prior sitemap fallback (its JSON-LD carries a real `description`), so this isn't a case of
`description` moving from `None` to populated either — it's the same field, now sourced from
`position_details` instead of a job page's JSON-LD, on the next ordinary scrape of these boards.

`remote` is a third raw passthrough this recovers, and unlike `department` it *is* one of
`extract_remote()`'s two arguments: the sitemap fallback only ever set it True on a JSON-LD
`TELECOMMUTE` `jobLocationType` (else None), while SmartApply supplies a real `workLocationOption`
value through `_remote_from`/`_REMOTE_OPTION` (True/False/None per the measured vocabulary above).
That still doesn't need a bump, for a different reason than `department`: `DERIVATIONS_VERSION`
only triggers `update_meta` to re-run the cascade on a row's *already-stored* raw inputs — it never
re-scrapes. A stored row's `remote` field only changes when that Board is scraped again (which
happens on the pipeline's own cadence, independent of this constant), at which point `to_meta()`
runs fresh on the new raw value automatically — the same "reaches a new Job for free" path
`department`/`description` take. Bumping the version would re-run `extract_remote()` against the
*old*, unchanged stored `remote` value and produce the identical result. No bump.
