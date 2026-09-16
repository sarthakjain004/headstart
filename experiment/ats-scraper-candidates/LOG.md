# ATS-expansion candidates: tech% / India% yield, 2026-09-16

## Question

`kalil0321/ats-scrapers` (github.com/kalil0321/ats-scrapers) is an open-source library covering
~60 ATS platforms, several of which HeadStart has no scraper for. Which of those are worth
building next, judged by tech-role yield and India share — the two numbers CLAUDE.md's TODO
list uses to prioritize?

## Method

The library publishes a daily hosted snapshot (no auth) at
`https://storage.stapply.ai/jobhive/v1/manifest.json` — 5.06M jobs / 80,390 companies across 63
sources as of this run (`generated_at: 2026-09-16T14:30:04Z`). Rather than live-sampling each
candidate ATS ourselves, this reads their **already-scraped, full per-ATS job data** (today's
whole published slice, not a sample) and runs it through HeadStart's own production gates:
`headstart.tech_filter.is_tech(title, department)` and `headstart.geo.classify(location) == "IN"`.
Script: `scripts/eval/ats_expansion_candidates.py`. Raw per-ATS stats (with sample titles) and the
downloaded parquet slices are in `artifacts/`.

**Caveat this isn't a sampling caveat**: the tech%/India% figures below are exact over the whole
population ats-scrapers currently has for each platform, not an estimate. What IS uncertain is
whether *our own* future scraper + company discovery would find the same company mix — this is a
third party's tenant list and scrape cadence, not ours.

**Excluded from the candidate table:**
- Already covered by an existing `src/headstart/scrapers/` module (28 sources — amazon, apple,
  ashby, bytedance, darwinbox, eightfold, google, greenhouse, icims, jazzhr, jobvite, join_com,
  keka, lever, meta, oracle, personio, phenom, recruitee, rippling, smartrecruiters,
  successfactors, teamtailor, tesla, tiktok, uber, workable, workday) or by a dedicated one-off
  (wellfound, `scripts/scrape/`).
- `taleo` → verified this is Taleo **Business Edition** (`{tenant}.tbe.taleo.net/.../careers/v2/
  searchResults`, the URL shape `taleo_be.py` already targets), not Enterprise. Already covered.
- **`recruiterbox` → NOT a new platform.** Verified live 2026-09-16: `recruiterbox.com` 301s every
  tenant straight to `{slug}.hire.trakstar.com` (Recruiterbox rebranded to Trakstar Hire years
  ago), and the "recruiterbox" rows in the hosted dataset already carry `*.hire.trakstar.com`
  URLs — `trakstar.py`'s own RSS namespace even hardcodes `recruiterbox.com/rss/job/`, so the
  codebase already knew the two names are one platform. Its 599 tech jobs / 272 companies are a
  **tenant-discovery gap in `trakstar.py`'s company list**, not a build gap — cheaper to close
  than any new scraper below, since the scraper already exists.
- Multi-employer job boards / government portals in the manifest (arbetsformedlingen, builtin,
  bundesagentur, eures, getonbrd, jobbankca, jobsch, manfred, programathor, wanted,
  welcometothejungle, weworkremotely, ycombinator): not per-company ATS platforms, so "build a
  scraper for this ATS" doesn't apply the same way. Out of scope for this comparison.
- **`adp`** has a scraper and a company CSV in ats-scrapers but **no hosted-dataset slice** — not
  in `manifest.json`'s `by_ats`. No data to measure here; would need a live probe, not attempted.

## Results (full population, ranked by tech-job volume)

| ATS | jobs | tech | tech% | India | India% | tech∩IN | companies | known pool | language |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| bamboohr | 19,365 | 2,667 | 13.8% | 309 | 1.6% | 164 | 2,745 | 5,632 | mostly unlabeled, sampled titles read English |
| paycom | 122,386 | 2,462 | 2.0% | 248 | 0.2% | 15 | 5,102 | 5,135 | unlabeled, English (US SMB/HR platform) |
| breezy | 29,294 | 2,039 | 7.0% | 169 | 0.6% | 84 | 888 | 1,384 | unlabeled, English |
| cornerstone | 30,149 | 1,936 | 6.4% | 248 | 0.8% | 63 | 334 | 564 | unlabeled, mixed EN/NL/DE (large multinationals) |
| pinpoint | 11,272 | 1,644 | 14.6% | 269 | 2.4% | 137 | 338 | 406 | unlabeled, English |
| gem | 3,542 | 1,379 | **38.9%** | 189 | 5.3% | 134 | 377 | 496 | unlabeled, English (startup-heavy) |
| beisen | 80,959 | 1,247 | 1.5% | 0 | 0.0% | 0 | 221 | 221 | 100% zh |
| ukg | 10,094 | 997 | 9.9% | 25→**17*** | 0.2%→0.17% | 7 | 105 | 107 | unlabeled, English |
| herp | 14,159 | 860 | 6.1% | 3 | 0.0% | 2 | 946 | 969 | unlabeled, ja-heavy (sample titles mostly Japanese) |
| moka | 31,814 | 747 | 2.3% | 0 | 0.0% | 0 | 186 | 198 | 93% zh, 7% en, <1% ja |
| avature | 4,215 | 649 | 15.4% | 112 | 2.7% | 72 | 95 | 126 | unlabeled, English |
| dayforce | 11,803 | 574 | 4.9% | 30→**22*** | 0.3%→0.19% | 13 | 334 | 348 | 98% en, 2% fr/de |
| gupy | 24,313 | 401 | 1.6% | 275→**~1*** | 1.1%→~0.0% | 0 | 557 | 589 | 100% pt (Brazil) |
| hrmos | 36,331 | 354 | 1.0% | 1 | 0.0% | 0 | 959 | 960 | 100% ja |
| softgarden | 20,141 | 331 | 1.6% | 0 | 0.0% | 0 | 1,786 | 392 | unlabeled, mostly DE/EU |
| pageup | 4,356 | 145 | 3.3% | 4→**0*** | 0.1%→0.0% | 0 | 21 | 21 | 100% en |
| paylocity | 1,257 | 45 | 3.6% | 0 | 0.0% | 0 | 129 | 48 | unlabeled, English |
| mercor | 428 | 35 | 8.2% | 1 | 0.2% | 1 | 9 | 1 | special-cased, see below |
| beisen_legacy | 6,231 | 0 | 0.0% | 0 | 0.0% | 0 | 6 | 8 | 100% zh |

`companies` = unique companies with ≥1 job in today's slice. `known pool` = row count of
ats-scrapers' own `ats-companies/{ats}.csv` tenant list (fetched same day) — a rough ceiling on
how many tenants exist, not how many are live.

**\* India-corrected columns** — see next section; the raw `classify()` output over-counts for
gupy, dayforce, ukg, pageup because of a bug this run surfaced in `headstart/geo.py`, not in the
candidate data.

## Side-finding: two unguarded India aliases produce false positives on real production data

Spot-checking the flagged India rows (`classify(location) == "IN"`) against country context found
two classes of false positive **in the shipped `src/headstart/geo.py`**, not specific to this
dataset — anything HeadStart already scrapes with a matching location string would hit the same
bug today:

1. **`goa` (unguarded CITIES alias) matches Portuguese "lagoa" (lagoon).** 274 of gupy's 275
   flagged-India rows are Brazilian cities — `Alagoas`, `Sete Lagoas`, `Arapiraca, Alagoas`,
   `Lagoa Santa` — all contain `goa` as a raw substring with no exclusion guard (unlike `surat`/
   `thane`, which do have one). Real gupy India share is ~0%, as expected for an all-Portuguese
   ATS.
2. **`anand` (unguarded CITIES alias, Gujarat) matches unrelated place names.** `Sananduva, Rio
   Grande do Sul, Brasil` and `Canandaigua, NY, United States` both contain `anand` as a raw
   substring. Confirmed on two unrelated countries, so this isn't a one-off.
3. **The country-level "india" substring test is missing some US place names**, the same class
   already guarded for `Indiana`/`Indian Head`/`Indian Trail`/etc. in `INDIA_EXCLUDE`:
   `Indian Creek Correctional Center` (pageup, all 4 of its flagged rows), `Indianwood Ave` /
   `Indian Street` (dayforce, 8 of 30), `Indiantown, FL` (ukg, 4 of 25).

None of this affects the ranking above in any way that would change the recommendation — the
affected ATSes (gupy, dayforce, ukg, pageup) were already low-India regardless. Flagging it
because it's a real gap in code that's live in production today, verified against real data, not
because it changes this report's conclusion. A fix would be a few lines (guard `goa`/`anand` the
same way `surat`/`thane` already are; extend `INDIA_EXCLUDE`) — not applied here since it's out
of scope for this task; happy to do it as a quick follow-up if wanted.

## Reading the table

- **Volume ≠ value.** paycom is the largest single pool (122k jobs) but only 2.0% tech — building
  it means scraping ~120k irrelevant rows (this project's tech gate is post-hoc, ADR-0017, so
  scrape cost is paid on the full board regardless of what's indexed) to reach 2,462 tech jobs.
  Its tenant pool (5,135, nearly 1:1 with companies actually live) says the *count* of boards
  is real; the *content* is mostly non-tech (US SMB/HR-heavy platform).
- **gem is the standout on purity** (38.9% tech, best of any candidate here) but small
  (377 companies, 3.5k jobs) — cheap to build, cheap to run, low absolute reach. Sample titles
  ("Senior NodeJS Backend Engineer", "Founding Senior ML Engineer") read like startup/scale-up
  hiring, consistent with Gem's product being a recruiting CRM startups use.
- **bamboohr is the best volume/purity combination**: highest tech-job count of any candidate
  (2,667) at a respectable 13.8%, over a large known pool (5,632 tenants, only half of which
  surfaced jobs today — real headroom for a scraper with its own discovery pass).
- **pinpoint and avature** both clear 14-15% tech with meaningful India shares (2.4%, 2.7%) and
  three-figure company counts — solid mid-tier picks, smaller lift than bamboohr/paycom.
- **beisen/beisen_legacy/hrmos/herp/moka are China/Japan-market platforms** (100% zh/ja for the
  labeled ones, and the unlabeled ones' sample titles read the same way) — CLAUDE.md scopes the
  search corpus to English-only for now, with non-English boards scraped but held out of the
  index until multilingual retrieval ships. Their tech% is also the lowest in this set. Lowest
  priority regardless of raw counts.
- **India is a minor signal across the board here** — nothing in this candidate set is
  India-concentrated the way keka/darwinbox were when those got built; the largest *tech*∩*India*
  counts are pinpoint (137) and gem (134), both off small bases. None of these candidates read as
  an India-specific opportunity; they'd all be justified on general tech-job volume, if at all,
  which matches CLAUDE.md's "global, not India-only" scope.

## Recommendation

In rough priority order: **bamboohr** first (best volume × purity, biggest pool with real
headroom), then **gem** (cheap build, best purity, worth it even at modest volume), then
**pinpoint** or **avature** as the next tier. **paycom** only if raw reach matters more than
selectivity — its low tech% means a worse cost/useful-job ratio than any of the above. Everything
else in this table is a lower-conviction pick than what's already in CLAUDE.md's TODO queue.
**Two things outrank writing any new scraper**, both cheaper than a build: fold `recruiterbox.com`
tenants into `trakstar.py`'s discovery (599 already-real tech jobs sitting behind a rebrand we
already scrape), and fix the two `geo.py` alias guards above (a few lines, verified-live bug,
independent of this report's topic).

`adp` is unevaluated (no hosted data) — worth a manual probe if it's otherwise attractive, not
covered by this run.
