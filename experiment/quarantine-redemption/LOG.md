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

**23 of 757 (3.0%) answer 200 today.** Re-fetching each and counting the listing: **16 carry at
least one live posting, 5,593 postings in total.**

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

**Conclusion → ADR-0161:** expire the verdict at 7 days rather than re-probe every run.
