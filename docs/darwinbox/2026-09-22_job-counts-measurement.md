# Darwinbox `job_counts` and `experience_from`/`experience_from_num` — live measurement

Issue #549 asked whether `/ms/candidateapi/job/alljobs`'s `job_counts` field (upstream's own
pagination terminator, `kalil0321/ats-scrapers`) is real, given a prior pass could not verify it —
a direct `curl_cffi` POST to `1mg.darwinbox.in` 403'd behind Cloudflare (ADR-0056). This scraper
already has a `browser_http` escalation path for exactly that wall (`darwinbox.py`'s own
`_fetch_raw_browser`), so this pass used it directly rather than re-trying curl.

## Method

`headstart.network.browser_http.BrowserFetcher`, one Board at a time: navigate
`https://{slug}.darwinbox.in/ms/candidate/careers` once to clear the wall, then POST
`/ms/candidateapi/job/alljobs?companyId=main` on the warmed tab, reading the full JSON envelope
(not just `data`, which is all the scraper reads today).

10 Hiring Boards, drawn from `data/validate/liveness/darwinbox.csv`'s live rows: the 4 largest by
the ledger's own `jobs` column (all reporting exactly 100 — worth checking directly, since that
figure comes from a `limit=100` single-page probe and could itself be a cap artifact) plus 6
random live boards for size spread.

## Results

| tenant | page(s) fetched | len(jobs) per page | `job_counts` | `experience_from_num` present |
|---|---|---|---|---|
| 1mg | 1 | 30 | 30 | no |
| kotaklifeinsurance | 1, 2, 3 | 100, 100, 71 | 271, 271, 271 | no |
| kotaksecurities | 1, 2, 3 | 100, 100, 80 | 280, 280, 280 | no |
| atherenergy | 1 | 94 | 94 | no |
| scripbox | 1 | 12 | 12 | no |
| vymopeopleconnect | 1 | 10 | 10 | no |
| smileshrms (.com) | 1 | 100 | 156 | no |
| theguardiansindia | 1 | 2 | 2 | no |
| greenkogroup | 1 | 28 | 28 | no |
| gommt | 1 | 74 | 74 | no |

`job_counts` was present and non-null on all 10. Two boards (kotaklifeinsurance,
kotaksecurities) span more than one page: `job_counts` held the *exact same* value across all
three pages of each, including the terminal short page (71 and 80 respectively) that ends the
scraper's own pagination loop naturally — and that value equals the precise sum across pages
(100+100+71=271, 100+100+80=280), not an approximation. Every single-page board's `job_counts`
equalled `len(jobs)` on that one page exactly. No board showed `job_counts` disagreeing with the
true total in either direction.

`experience_from_num` was absent from every sampled job on every board (0/10). Only
`experience_from`/`experience_to` (e.g. `"1"`/`"2"`, as strings) are real — the scraper's own
`experience` field (`"1 - 2 Years"`) is a pre-composed string from the same two numbers and was
present and well-formed on every job checked, so the numeric pair isn't recovering anything the
string loses. Not wired as a result.

## What shipped

`_alljobs` and `_fetch_raw_browser` both now stash the envelope's `job_counts` and, when the
pagination loop ends on a short page short of that total, call
`BaseScraper.mark_truncated_unless_negligible` (ADR-0121) rather than leaving the shortfall
undetected. The existing hard page-cap exit (`_MAX_PAGES`) is unchanged — still an unconditional
`mark_truncated`, since that shortfall is unreachable rather than measured, exactly per
`mark_truncated_unless_negligible`'s own documented scope.

10 boards, 2 of them multi-page, is evidence rather than proof (CLAUDE.md's own bar): it rules out
`job_counts` being static/stale garbage or a per-page rather than per-board figure, but does not
rule out some tenant somewhere serving a wrong one. `mark_truncated_unless_negligible`'s own
tolerance (`MIN_AUTHORITATIVE_SHARE`) is the intended backstop if that ever happens on a real
board — a small, measured disagreement is absorbed rather than scope-excluding the Board.
