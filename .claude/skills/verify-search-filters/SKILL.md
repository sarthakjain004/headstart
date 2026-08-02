---
name: verify-search-filters
description: Extend, run, and analyze the live search-filter verification harness (scripts/eval/verify_filters.py) against the deployed Space — checks every filter's semantics and every ATS's job-link correctness, then diagnoses each violation to a root cause. Use when filters/UI/scrapers changed, after deployments, or when a user reports a wrong result or dead job link.
---

# Verify search filters (live Space)

The harness at `scripts/eval/verify_filters.py` fires a battery of semantic queries at the
deployed Space, validates every returned row against each filter's semantics (alone and in
combos), and checks per-ATS job-URL shapes plus live HTTP probes (the darwinbox lesson: a
link can 200 and still land on a dashboard). It writes a JSON report to
`data/eval/filter_checks/{timestamp}.json` and exits nonzero on violations.

**Ground every expectation in verified truth — never assume.** The expected shapes and
semantics you encode must come from reading the actual code (`deploy/hf-space/app.py`,
`src/headstart/scrapers/*.py`) — and even the code is a claim, not proof: scrapers have
confidently built URLs their ATS never routed (darwinbox's SPA wildcarded them to the
dashboard; the first "fix" from its route table was wrong too — only a browser-verified
URL settled it). When encoding a shape or diagnosing a failure, verify against live
behavior (fetch it, compare against a known-good example from a real browser session, or
ask the user for one). If you catch yourself writing a pattern "that's probably how it
works" — stop and check.

This skill is a loop, not a replay. Do all four steps:

## 1. Extend the harness first

The product moves; the script must chase it. Before running, diff the harness against the
current deployment and ADD what's missing (edit the script — it is meant to grow):

- Read `deploy/hf-space/app.py`: every query param `/search` accepts and every
  `_ETYPE_CLAUSES` key must have check cases. New filter param → new cases (single + at
  least one combo).
- **ATS coverage is a hard gate, never a footnote.** The harness gates on the union of the
  ATSes it sampled from the live index AND the scraper registry (`SCRAPERS − DISABLED_ATS`) —
  sampling alone is not enumeration; join/personio/trakstar measurably never rank into the
  4-query sweep. The summary's `atses_without_shape` must be empty: give every gated ATS a
  `URL_SHAPES` entry **in the same session** — the harness exits nonzero otherwise, and it earned that
  severity: three ATSes (eightfold, freshteam, successfactors) shipped unchecked while the
  old code printed `shape_ok=False` and counted nothing, and the gap surfaced as a
  user-visible sandbox row. Derive the expected job-URL shape from the scraper's `url=`
  construction in `src/headstart/scrapers/{ats}.py`, and distrust it: verify the shape
  against the ATS's real routing before encoding it (the darwinbox scraper confidently
  built URLs its SPA wildcard-routed to the dashboard).
- New Job fields surfaced by the API deserve integrity checks (non-null-ness, format).

## 2. Run it

```bash
.venv/bin/python -u scripts/eval/verify_filters.py            # full run with HTTP probes
.venv/bin/python -u scripts/eval/verify_filters.py --no-http  # faster, no link probes
```

Needs network. ~2–4 minutes with probes. If the Space is SLEEPING, the first request
wakes it (~1 min); retry once on timeout.

## 3. Analyze the report yourself

Read the JSON report it names at the end. Never stop at the summary counts — walk every
check with `n_violations > 0`, every `error`, every `shape_ok: false`, every
`http_error`/`http_status >= 400`, and every `title_on_page: false`.

## 4. Diagnose each finding to a root cause before reporting

Classify each violation — the same symptom has very different causes:

- **Code bug in the Space's filter clause** (`deploy/hf-space/app.py _build_filter`):
  reproduce against the local table (`lancedb.connect('data/lancedb')`, same where-clause)
  to separate query logic from data.
- **Stale table data awaiting healing**: rows carry old scraper output (URLs, date
  formats, missing fields) until their board is re-scraped + re-embedded (ADR-0021).
  Check the scraper's CURRENT output for that board before calling it a code bug — if the
  scraper is right and the row is old, it's a healing item, not a fix.
- **Scraper bug**: the scraper's current construction is wrong (verify live against the
  ATS, as in step 1).
- **ATS-side behavior**: link redirects, soft-404s, bot walls (`http_error` on a URL a
  browser opens fine is usually TLS fingerprinting — note it, don't chase it).
- **Harness gap**: the check itself misjudges (e.g. an employment-type synonym not in
  `_etype_ok`) — fix the harness, rerun.

Report findings grouped by root cause with severity, the affected ATSes/filters, and the
concrete fix or healing action for each. Fix-worthy code findings go in a branch + PR per
repo conventions; healing items get folded into the next eviction/upload window.
