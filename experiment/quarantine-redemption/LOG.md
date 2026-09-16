# Is a quarantined Board actually gone? — live re-probe, 2026-09-16

**Question.** ADR-0058 quarantines a Board after five consecutive 404/410 scrapes and then never
looks again (the clearing branch is unreachable once the Board leaves the slice — see the
2026-09-16 five-run review, `experiment/pipeline-review-2026-09-16/artifacts/join-stage.md`
finding 2). Before building a drain, measure how many of the quarantined Boards are actually
recoverable, and whether recoverability depends on how long ago the Board was struck out.

**Method.** `data/state/board_failures.csv` pulled from HF (`imPoseidon/headstart-index`,
2026-09-16 — the repo's local copy is gitignored and stale by construction). Every row at or over
`QUARANTINE_AT` strikes — **757** Boards, all of them at exactly 5 strikes, because a quarantined
Board never scrapes again and so never takes a sixth — was probed once with
`probe_quarantined.py`: instantiate the Board's own scraper class from
`headstart.scrapers.registry` and GET whatever `.url()` returns, concurrency 12, 30 s timeout.
Raw results: `artifacts/2026-09-16_quarantined-reprobe.jsonl` (one JSON row per Board).

## Result

| outcome | Boards |
|---|---|
| still 404 | 666 |
| **HTTP 200** | **23** |
| ConnectionError | 56 |
| ReadTimeout | 6 |
| probe could not build a URL | 6 |

**23 of 757 (3.0%) answer 200 today.** Re-fetching each and counting the listing: 16 carry at
least one live posting, 5,593 in total — but see "That raw count is the wrong number" below. The
figure that matters is **12 Boards serving 264 tech postings**.

```
greenhouse:svetness      4980      ashby:airapps        498      greenhouse:solarisbank   29
greenhouse:evolver         18      greenhouse:binance    10      ashby:sxaler              9
greenhouse:summlinkbv       8      ashby:todyl            7      greenhouse:highground     6
greenhouse:axial            5      greenhouse:coverai     5      greenhouse:vectara        5
greenhouse:lookout          4      greenhouse:lookoutinc  4      ashby:tolken              4
greenhouse:goprojobs        1      trakstar:dogplanet   ~24 links (HTML board)
ashby:furiosa-ai / ashby:panacea / greenhouse:capellaspace / goprocareers / govtechbarbados
                                   / skyloom — 200 but empty
```

`lookout`/`lookoutinc` and `goprojobs`/`goprocareers` are the same employer under two slugs: the
Board did not die, it moved, and the old spelling's 404 struck out the tenant.

## That raw count is the wrong number

Only tech roles are embedded, indexed or shown (ADR-0017), so a recovered Board contributes what
survives `tech_filter.is_tech(title, department)` — the same two arguments `filter_jobs` passes.
Re-run with `tech_share_of_recovered.py`; per-Board results in
`artifacts/2026-09-16_recovered-tech-share.json`.

| | Boards | postings |
|---|---|---|
| answered 200 | 23 | — |
| ≥1 posting | 16 | 5,593 |
| **≥1 tech posting** | **12** | **264 (4.7%)** |

```
ashby:airapps          216 / 498  (43.4%)   greenhouse:evolver      11 /  18
greenhouse:solarisbank   8 /  29            greenhouse:summlinkbv    8 /   8  (100%)
ashby:todyl              6 /   7            greenhouse:coverai       4 /   5
ashby:sxaler             2 /   9            ashby:tolken             2 /   4
greenhouse:lookout       2 /   4            greenhouse:lookoutinc    2 /   4
greenhouse:vectara       2 /   5            greenhouse:goprojobs     1 /   1
answered 200 with postings but 0 tech:
greenhouse:svetness      0 / 4980           greenhouse:binance       0 /  10
greenhouse:highground    0 /   6            greenhouse:axial         0 /   5
```

**One Board is 89% of the raw total and contributes nothing.** `greenhouse:svetness` is 4,980 of
the 5,593 and is a personal-training franchise — *Circuit Training Instructor*, *In-home Personal
Trainer*, *CIRCUIT TRAINING INSTRUCTOR* — **0 of 4,980 tech**. Quoting 5,593 as recovered coverage
overstates the benefit by **21x**. The Board genuinely worth recovering is `ashby:airapps`, whose
216 tech rows are 82% of everything recovered.

Two things to know before re-running this:

- **Pass department, not just the title.** airapps scores 206 title-only and **216** with
  department — 10 `Data Analyst` rows sit under a `Platform` department. `filter_jobs` passes
  both, so 216 is the production-accurate number.
- **`classify()` returns a `Verdict` dataclass, which is always truthy.** Calling it in a boolean
  context reads every posting as tech (it scores svetness at 100%). `is_tech()` is the bool.

Even 264 is a ceiling: `lookout`/`lookoutinc` are one employer under two slugs, so 2 of them are
the same postings twice, and the recall-biased gate counts two "Forward Deployed Recruiter - SWE"
rows on `sxaler` — tolerated creep per ADR-0017, not software jobs.

**Caveat on the 56 ConnectionErrors — they are the probe's fault, not findings.** Most are
Personio, whose ledger key is a bare tenant while `PersonioScraper.url()` expects a whole host, so
the probe built `https://a4g/xml`. None are counted as recoveries; 3.0% is therefore a *floor*.

## Does recoverability decay with age?

No — it is roughly flat over the whole 28 days the ledger covers (it starts 2026-08-18, when
ADR-0058 shipped). Age = days between `last_seen_gone` and 2026-09-16.

| days since quarantine | quarantined | now 200 | rate |
|---|---|---|---|
| 0–6 | 98 | 2 | 2.0% |
| 7–13 | 94 | 9 | 9.6% |
| 14–20 | 144 | 3 | 2.1% |
| 21–27 | 381 | 8 | 2.1% |
| 28+ | 23 | 1 | 4.3% |

≈0.8 Boards/day become reachable again. That kills exponential backoff as a design: there is no
age past which looking again stops paying.

## What a re-probe costs

Every one of the 757 has a row in `data/state/board_cost.csv` from its last real scrape:
**p50 0.10 s, p90 0.94 s, max 16.75 s, Σ 354 s** for the entire population. The binding cost is
not CI seconds — it is the 20,000-Board slice cap (757 is 3.8% of it) and the request volume
aimed at origins that have already said 404.

Note the asymmetry the tech gate creates: `svetness` is simultaneously the most expensive Board
in the cohort to re-scrape and worth zero. At 4,980 postings on a list-only ATS that is still
seconds, so it does not move the cadence — it would if a future cohort held a detail-fetching
Board of that size.

**Conclusion → ADR-0161:** expire the verdict at 7 days rather than re-probe every run, and state
the benefit in tech postings (264 on 12 Boards), never the raw count.
