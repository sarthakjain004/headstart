# Salary extraction — process and running index

One continuous initiative across every active ATS scraper: measure how each one shows salary
(structured field, embedded in the description, or not at all), extend the code where it helps,
and feed a shared `headstart.salary` extraction module so search gets real numeric salary
filtering with, in the founder's words, "extremely good coverage" — not just the boolean
`has_salary` presence check that exists today.

This file is the process write-up plus a running index, appended to after each ATS. It is not
per-ATS findings — those live one file per ATS in this same folder (`workable.md`, `workday.md`,
…). Raw captures (the actual API responses) never live in `docs/` — they're in
`experiment/salary-extraction/<ats>/artifacts/`, per this repo's usual experiment/docs split.

## Why this shape, not the usual `docs/<ats>/<topic>.md`

Every other per-ATS doc in this repo (`docs/darwinbox/`, `docs/eightfold/`, `docs/personio/`) puts
the ATS as the folder and the topic as the file — a one-off issue gets written up once, filed
under its ATS. This effort is the inverse on purpose: it's one continuous cross-ATS initiative
where each ATS's findings are meant to build on the last, so the *topic* (`salary-extraction`) is
the folder and the *ATS* is the file. A reader can open this `README.md` and see the whole arc; a
reader can also open `workday.md` alone and get everything about workday specifically.

## The process, per ATS

1. **Sample.** `scripts/enrich/salary_sample.py <ats> [--n 3000]` — up to 3000 live boards (the
   full liveness-CSV live-board count if that's smaller), via `config.load_active_companies` (the
   same liveness-ledger source and dedup every other production consumer uses — never hand-parse
   the CSV, it has documented duplicate-row issues that function already handles). Fetches through
   the *real* registered scraper and its real `parse()`, not a reimplementation. Listing-only ATSes
   (`has_detail_pass = False`) get one request per board — that's the whole sample. Most detail-pass
   ATSes get a bounded adapter (~3 per-job detail fetches/board, calling the scraper's own endpoint
   methods directly — several detail-pass scrapers bake a full per-board fan-out into `fetch_raw()`
   itself, which a naive call would trigger, blowing past the bounded-request intent) — built
   per-ATS as that ATS is reached, never assumed in advance. **zoho is the deliberate exception**
   (PR #242): its listing never paginates, so calling `fetch_raw()` directly costs nothing beyond
   the detail fetches it needs anyway, and capping those specifically was undercounting real
   coverage for no cost saving — see `zoho.md`'s "Post-merge correction" section. Runs at `--workers 32` by default
   (bumped from an initial 8 partway through the workday pass, since 3000 boards at 8 workers was
   impractically slow) — safe at that width specifically because sampled boards spread across many
   different companies/instances, not one tenant's own rate limit. Spare egress is automatic:
   every fetch goes through the scraper's own `_get()`/`_post()`/`_job_detail()`, which already
   carries `egress_fallback_on`, so an ATS with it set (workday: `{429}`) transparently falls back
   to `headstart.spare_egress`'s WARP route the same way the real pipeline does — never build a
   new adapter that calls `http.fetch` directly, that silently skips it.
   **Local operational note** (doesn't apply to CI): running a sample that actually triggers the
   fallback dials this machine's own registered WARP client and leaves it connected afterward —
   `systemctl`-based rotation doesn't exist on macOS, so it's one alternate route per process, not
   full rotation. A connected local client left running when the test suite runs next can make
   `tests/test_spare_egress.py`'s tight cooldown-timing tests flake (a real network round-trip
   through a real listening proxy takes measurably longer than the fast-failing one those tests
   are timed against) — `warp-cli --accept-tos disconnect` before running tests if a sample was
   just run.
2. **Measure.** Two numbers, both required: % of jobs (and % of boards with ≥1 job) carrying a
   populated structured `salary` field, and % where a salary figure only shows up inside the
   description text (a loose detector for this measurement pass — `headstart.salary` is the real
   extractor, built from what this step finds, not the other way around).
3. **Read the real shapes, and audit whether "no signal" is genuinely non-disclosure.**
   `--misses <ats>` re-reads captured artifacts (no new network calls) and samples substantial
   no-signal jobs for manual reading — the "widen the pattern" half of the loop, mirroring
   `scripts/enrich/experience_coverage.py --misses`. On top of that open-ended read, run a
   language-independent structural check over the *whole* no-signal population — a symbol or ISO
   code adjacent to a number (`[€$£]\d`, `\d[€$£]`, `\bEUR\b\d`, etc.), gated on no English or
   German label word at all — before reporting any coverage number as a ceiling. This turns
   "coverage looks low" into a measured claim (X% genuinely has no currency mention anywhere, Y%
   does and is a real, chase-able gap) instead of an assumption that a low number means the ATS
   just doesn't disclose. Added after personio's pass (2026-08-22,
   `docs/salary-extraction/personio.md`'s "Post-merge coverage audit") found this exact audit
   surfaced two real `_num()` locale bugs and a new Tier-2 pattern that also lifted coverage on
   every other already-merged ATS — required for every pass from here on, not optional.
4. **Extend `headstart.salary` and/or the scraper**, informed by what was actually read — never
   speculative patterns written ahead of real evidence. Code changes to a scraper are in scope
   (e.g. fixing a raw-field ambiguity at the source, or adding a query param that unlocks a
   compensation field) — make them freely, but every one gets live-verified, not just tested
   against the frozen sample.
5. **Live-verify.** After any code change, fetch a fresh, small (~20–50), differently-seeded sample
   against real current boards and confirm the change actually works on live data — not a replay
   of the original capture, which can't catch either an ATS-side shape change or a bug invisible on
   the frozen sample.
6. **Document.** One `docs/salary-extraction/<ats>.md`, following the template below.
7. **Carry it forward.** The next ATS's doc opens with what it read from prior docs and applied or
   deliberately avoided because of them.

## Per-ATS doc template (required sections)

Every `<ats>.md` must contain, in this order:

1. **Methods tried** — N sampled, live-CSV size, date, what was tried and abandoned. A negative
   result ("this ATS's field is always null") is a first-class finding, not a section to skip —
   see `freshteam`'s existing scraper docstring for the tone: specific, conclusive, evidenced.
2. **Instruction-adherence self-assessment** — an explicit checklist: sampled up to 3000 or the
   full live-CSV count? measured both required percentages? live-verified after any code change?
   went beyond the ask, and how, stated explicitly rather than implied? This section exists because
   the founder asked for it directly — it is not optional or a formality.
3. **Live-verification review** — the fresh re-sample from step 5, with its own numbers.
4. **Patterns found** — real worked examples, not paraphrased.
5. **Coverage** — the three numbers: % showing salary at all, % description-only, overall.
6. **What changed in code, and why.**
7. **Carried forward** — what this ATS's pass learned from earlier ones, and applied.

## Design decisions already locked in (do not re-litigate per-ATS)

Recorded in full in
[ADR-0082](../adr/0082-salary-extraction-a-two-tier-cascade-no-estimate.md) and in the
`salary-extraction-decisions` memory. In short:

- **Currency**: period-normalized (hourly/monthly → annual), native currency kept as its own
  column. No FX conversion, no single cross-currency figure.
- **No estimate fallback**: unlike `headstart.experience`'s seniority-based Tier 3, salary never
  fabricates a number when none is stated anywhere. Unknown stays unknown, and unknown is never
  treated as exclusionary — a job with no discoverable salary still passes any salary-related
  filter rather than being hidden.
- **Sampling depth**: cheap-first and bounded — one listing fetch per board, ~3 detail fetches/
  board cap where needed. Roughly 3,000–12,000 requests per ATS, not tens of thousands.

## Processing order

Pilot (`workable`) first to prove the whole loop cheaply, then large-unexamined-high-value, then
already-partially-handled (extend with description mining), then remaining unexamined, then the
two lightweight single-company unlocks last. Full reasoning and the complete 20-ATS order live in
the planning record; the table below is the live status.

## Status

| ATS | live boards | status | coverage | doc |
|---|---:|---|---|---|
| workable | 971 (190 live w/ openings) | done (pilot) | 0.0% field, 15.4% overall | [workable.md](workable.md) |
| workday | 16,964 (3,000 sampled) | done | 0.0% field, 27.6% overall | [workday.md](workday.md) |
| greenhouse | 7,503 (3,000 sampled; 9,152 was a stale liveness snapshot) | done | 0.0% field, 36.1% overall | [greenhouse.md](greenhouse.md) |
| smartrecruiters | 5,659 (3,000 sampled; 10,845 was a stale liveness snapshot) | done | 0.0% field, 10.0% overall | [smartrecruiters.md](smartrecruiters.md) |
| zoho | 5,337 (3,000 sampled; 6,550 was a stale liveness snapshot) | done | 0.0% field, 10.0% overall (corrected PR #242, was 9.2%) | [zoho.md](zoho.md) |
| teamtailor | 3,764 (2,985 sampled after a rate-limit retry; 4,686 was a stale liveness snapshot) | done | 9.8% field, 14.1% overall | [teamtailor.md](teamtailor.md) |
| ashby | 3,823 (3,000 sampled; 4,347 was a stale liveness snapshot) | done | 38.9% field, 49.7% overall | [ashby.md](ashby.md) |
| recruitee | 3,534 (3,000 sampled, 2,995 clean after a rate-limit retry; 3,970 was a stale liveness snapshot) | done | 36.5% field, 38.2% overall | [recruitee.md](recruitee.md) |
| personio | 2,534 (2,534 sampled, 2,510 clean; 2,992 was a stale liveness snapshot) | done | 10.7% field, 10.5% overall (corrected, was 10.2% — see personio.md's "Post-merge coverage audit" for the full investigation and its real, hand-traced side effects on every other ATS below) | [personio.md](personio.md) |
| rippling | 2,062 (2,062 sampled, 2,058 clean; 2,941 was a stale liveness snapshot) | done | 24.3% field, 46.4% overall | [rippling.md](rippling.md) |
| lever | 2,179 (2,187 sampled pre-fix, 2,165 clean; 2,784 was a stale liveness snapshot; 8 vendor demo tenants found and excluded, PR #246) | done | 30.5% field, 41.3% overall | [lever.md](lever.md) |
| keka | 916 | not started | — | — |
| darwinbox | 446 | not started | — | — |
| ripplehire | 79 | not started | — | — |
| successfactors | 2,204 | not started | — | — |
| trakstar | 1,632 | not started | — | — |
| freshteam | 1,412 | not started (Tier-2-only; Tier 1 already known dead) | — | — |
| eightfold | 103 | not started | — | — |
| oracle | — (single-company) | not started | — | — |
| sensehq | — (single-company) | not started | — | — |
