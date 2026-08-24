# Location field audit — every active ATS scraper, one by one

**Date:** 2026-08-24 · **Scope:** `Job.location` extraction across all 20 active scrapers
(`join` excluded per ADR — non-tech noise) · **Fixes:** darwinbox, successfactors, keka,
greenhouse · **Method:** live-sampled every scraper via the real registered `fetch_raw()` +
`parse()` path, then live-verified every fix against its full live board population

## Summary — verdict per ATS

| ATS | verdict | finding |
|---|---|---|
| ashby | clean | flagged "short" values are `"UK"` — a correct 2-letter country code, not a bug |
| darwinbox | **fixed** | embedded `\r` + empty comma segments leaked into 47.45% of served locations |
| eightfold | clean | 0.9% null on sample, no dirty values; multi-location silently truncates to the first entry (`_first_location`) — noted, not touched, see below |
| freshteam | clean | small sample, no issues found |
| greenhouse | **fixed** | `location.name` shipped un-trimmed trailing whitespace on 3.59% of jobs across the full 7,419-board population |
| keka | **fixed** | `jobLocations[].city` shipped a trailing space on some tenants; 1.10% of jobs across the full 819-board population |
| lever | clean | flagged "short" value was `"UK"`, same false positive as ashby |
| oracle | not sampled | zero live boards in the liveness ledger (curated single-company unlocks only, per CLAUDE.md) |
| personio | clean | no issues found |
| recruitee | clean | no issues found |
| ripplehire | clean | no issues found |
| rippling | clean | no issues found |
| sensehq | not sampled | zero live boards in the liveness ledger |
| smartrecruiters | clean | no issues found |
| successfactors | **fixed** | 35.2% null across the full live population (156,215 jobs, all 2,164 boards); some CSB tenants' job pages carry no location markup anywhere, though the URL SuccessFactors itself generated still has it |
| teamtailor | clean | no issues found |
| trakstar | clean | flagged "short" values are legitimate short place names ("Gurgaon") |
| workable | clean | no issues found |
| workday | clean | already has sophisticated rollup-detection + detail-page repair (see below) — the reference bar the other fixes were measured against |
| zoho | clean | 38.5% null on a 1,305-job sample, **100% correlated with `Remote_Job: True`** — fully-remote postings genuinely have no physical location upstream; the scraper is correctly passing that through |

Fourteen of twenty were genuinely clean, four had real bugs fixed here, and two (oracle,
sensehq) have no live boards to sample — curated single-company unlocks per the CLAUDE.md
ATS-expansion notes, not a scraped population. Three of the fourteen clean ones (ashby, lever,
trakstar) were flagged as suspicious by an automated heuristic and turned out to be correct
short values on inspection — two 2-letter country codes and one genuinely short city name — a
reminder that "looks short" is not itself evidence.

## Method

A single harness (`scripts/eval/...` — see note below on where this landed) ran every registered
scraper's real `fetch_raw()` + `parse()` against a sample of live boards drawn from
`load_active_companies()`, then scored the resulting `Job.location` values for null rate, HTML
entities, whitespace, and rollup-string leaks. Anything flagged was investigated by hand against
the scraper's actual code and, where the finding warranted it, a live re-fetch of the specific raw
record or detail page — never accepted on the strength of a single small sample. Three false
positives (ashby, lever, trakstar) were closed by widening the sample and inspecting the actual
flagged values, which turned out to be legitimate short place names — two 2-letter country codes
and one genuinely short city name, not the garbage a "looks short" heuristic alone would suggest.

## Fix 1 — darwinbox: an embedded `\r` and empty comma segments

**Root cause.** `parse()`'s single-location branch took the raw `locations` field with zero
cleaning — `location = j.get("locations") or None` — while the multi-location branch two lines
above it already comma-splits and strips each part. The raw API field itself ships a literal `\r`
character immediately before its comma on a real fraction of tenants:
`"Jhagadia, Gujarat\r, India"`.

**Fix.** Comma-split, strip each part, filter empties, rejoin — the same treatment the
multi-location branch already gives each `tool_tip_locations` entry, applied to the single-location
case too.

**Verified, full live population** (281 boards, all with `min_jobs>=1`; 280 succeeded, 1 errored
independent of this change): **13,129 jobs, 6,230 changed (47.45%)**. Every changed value was
confirmed to be either (a) the same content with all whitespace characters removed — the `\r` fix
— or (b) an empty comma segment dropped (`" , Makati, Metro Manila, Philippines"` →
`"Makati, Metro Manila, Philippines"`, `"Serrano Ave,, San Juan..."` →
`"Serrano Ave, San Juan..."`), which is the same join-and-filter mechanism doing its job on a
second, related malformation. Zero regressions.

## Fix 2 — successfactors: recovering location from the job's own URL

**Root cause.** `_page_fields()` already tries JSON-LD, then CSB microdata/label spans. On a real
fraction of tenants, neither exists: the job page has a title, a description, an `og:title` — and
genuinely nothing else. Measured directly on a real page
(`careers.gallo.com/job/Concordville-Manager-I-Engineering-Program-PA-19331-0116/...`): the string
"Concordville" appears **nowhere** in the page body, not in `<title>`, not in any `og:meta`, not in
visible text. It exists only in the URL SuccessFactors itself generated when the posting was
created.

**Measured, 30-board sample (2026-08-24):** 1,805 jobs, **29.9% null**. Of those nulls, 62.5%
looked recoverable from the URL slug by a first-pass heuristic; the real algorithm below did better
on inspection (100% correct where the loose heuristic said 0% on at least one board).

**The pattern.** SuccessFactors builds job URLs as `{location}-{title}[-{state}-{zip}]/{id}/` —
e.g. `Charlotte-Account-Manager-Customer-Development-NC-28277` for a posting titled "Account
Manager - Customer Development".

**The algorithm** (`_location_from_slug`, gated as the last-resort tier in `_page_fields`, fires
only when both prior tiers return nothing): tokenize both the known title and the URL slug into
words. Require the title's own token sequence to appear *exactly*, contiguously, in the slug's.
Whatever comes **before** that match is the location. Anything **after** it is never trusted —
that is where a trailing requisition id lives
(`.../Foshan-City-Sr-Technician-528513/...` for a title of just "Sr Technician" leaves a bare
`528513`), and appending it would fabricate a location worse than reporting none.

Calibrated against real examples across five genuinely different tenants and locales (US wine
company, Czech fast food, Philippines/Australia electronics, Australian health, Vietnamese bank)
before being written, following this repo's own convention for pattern-based extraction
(measure real examples first, build guarded, verify at scale — the same discipline
`docs/salary-extraction/` used). One case is worth naming: on
`tuyendung.vietcombank.com.vn`, the location text ("Bình Dương") appears a *second* time embedded
inside the title's own bracketed code — the exact-contiguous-match requirement still isolates the
true prefix correctly rather than getting confused by the repeat, because the *entire* title token
sequence, not just a fragment, is what has to match.

**Costs, honestly:**
- Precision on US-style postings that carry a real `-NC-28277` state/zip suffix — those are
  reported at city grain only, e.g. "Charlotte" not "Charlotte, NC 28277".
- Original punctuation: words are joined with plain spaces, so a multi-part prefix like
  "Gaoming District, Foshan City" (comma in the source) comes back as "Gaoming District Foshan
  City" — recovering which gap was a comma would mean guessing, so it doesn't.
- Recall on titles containing a literal `/` (found in code review, round 1): the character
  decodes from its percent-encoded form before the URL path is split on `/`, which shifts every
  segment after it and breaks the id/slug alignment the function relies on. It fails safe —
  returns `None`, never a wrong location — rather than guess from the misaligned pieces.

All three are deliberate: a location that is always genuinely a place beats one that occasionally
isn't.

**Verified — three independent checks, not one:**

1. **Unit tests** against every calibration example, including the adversarial cases (repeated
   place name, trailing req-id, slug-is-the-title-verbatim on `careers.ijm.com`).
2. **Plausibility scan, 60-board random sample:** 3,511 jobs scanned, 390 slug-tier fills found,
   **0 suspicious** (no bare numbers, no hex-looking req-id fragments, nothing under 2 characters).
   Every fill was a real place name across many locales — Ottawa, München, Shenzhen, Hong Kong,
   Singapore, Wien, Linz.
3. **Full-population fill-rate + gating check, all 2,164 live boards** (job cap 200/board to bound
   wall-clock; real per-job detail fetches made this the slow check — full run took several hours).
   **Completed: 2,142 boards processed (22 errored, independent of this change — same order of
   magnitude as the other three fixes' own error counts), 156,215 real jobs checked. Old-code
   nulls: 54,948 (35.2% of jobs checked). New code fills 31,070 of those (56.5% of nulls) — the
   rest genuinely have no location signal anywhere, on the page or in the slug, and correctly stay
   None. The gating invariant (the new tier can only fire when both prior tiers found nothing —
   provable directly from the `if not fields.get("location")` guard, not just measured) held with
   zero violations across all 156,215 jobs. Zero regressions.**

## Fix 3 — greenhouse: un-trimmed `location.name`

**Root cause.** `location = (j.get("location") or {}).get("name")` — no `.strip()` anywhere.
Real values ship with trailing padding: `"Hybrid in Boston, MA   "` (three trailing spaces),
`"Washington D.C. "`, `"Remote - Pacific Time "`.

**Fix.** `.strip()` the extracted name, same one-line shape as every other clean scraper in this
codebase.

**Verified, full live population: all 7,419 live boards, 212,934 jobs, 7,639 changed (3.59%),
zero unexpected.** Every changed value differed from its predecessor by whitespace alone.

## Fix 4 — keka: a dirty `city` field, second field wins the `or`

**Root cause.** `loc.get("city") or loc.get("name")` — on at least one real tenant, `city` carries
a trailing space (`"Ahmedabad Center "`) while the sibling `name` field for the exact same
location is clean (`"Ahmedabad Center"`). Because `city` is checked first and a non-empty
whitespace-padded string is truthy, the dirty field always wins.

**Fix.** Strip each of `city`/`name`, `state`, `countryName` individually before joining and
filtering.

**Verified, full live population: all 819 live boards, 12,867 jobs, 142 changed (1.10%), zero
unexpected.** Five changed values looked structurally different at first pass (`" , DL, India"` →
`"DL, India"`) — a whitespace-*only* city field, once stripped to empty and filtered like any
other empty part, drops its leading comma along with it. Confirmed as the same class of
improvement darwinbox's empty-segment cleanup produced, not a regression: no real content was
ever present in the dropped segment.

## Deliberately not touched

**Eightfold's `_first_location`** silently keeps only the first entry of a multi-location posting
(`locations: list -> locations[0]`). This is a real simplification, not a bug in the sense the four
fixes above are — every value it returns is genuinely correct for that one location, it just
doesn't surface the others. Left alone here because it's a scope/design question (does the product
want every location a job spans, or the first one is fine?), not a data-quality defect, and this
audit's mandate was "are we missing or mangling data," not "should this ATS report multi-location
postings differently than it does today."

**Zoho's `remote=True, location=None`** is the correct representation of a fully-remote posting in
Zoho's own data model — confirmed by measuring the correlation directly (100% of null-location zoho
jobs, n=502) rather than assumed. Worth flagging as a *product* question, not a bug: other ATSes
(ashby, lever) represent "fully remote" as `location="Remote"`, a string, while zoho represents it
as `location=None, remote=True`, a flag. If the search UI or the location filter treats these two
representations differently, that inconsistency is worth its own decision — but it is a
cross-ATS normalization choice, not a fix to either scraper.

## What would have prevented some of this

Every one of the four bugs found here is the same shape: a raw upstream field trusted verbatim,
with no `.strip()` between the API response and the served row. Three of the four ATSes already
had a *sibling* code path in the same file doing the stripping correctly (darwinbox's own
multi-location branch, and the general pattern every other clean scraper in this codebase follows)
— the gap was always in one specific branch, never a missing idea. A lint rule flagging a
dict-`.get()` chain assigned straight to a `Job(...)` field constructor argument without an
intervening `.strip()` call would be too blunt (plenty of fields are legitimately pre-clean), but
it's worth naming as the recurring shape if a similar audit is run again on another field.
