# ADR-0362: A Spire2Grow Board is its career-site host, and the ATS lands enabled

**Status:** accepted · **Date:** 2026-09-30 · **Relates to:** #975, [ADR-0001](0001-per-ats-slug-derivation.md) (a scraper's slug is its own to define), [ADR-0121](0121-a-negligible-shortfall-is-still-an-authoritative-list.md) (a measured shortfall), [ADR-0158](0158-jazzhr-and-jobvite-are-worth-their-storage.md) (the storage bar), `docs/spire2grow/2026-09-30_career-api-measurement.md` (every number below)

## Context

Myntra, a benchmark company absent from every ledger, hires through Spire2Grow (Spire
Technologies' iExchange): `jobs.myntra.com` is a Flutter app on Myntra's own host that reads a
public API at `io.spire2grow.com/ies/v1/p`. Issue #975 carried the 2026-09-29 coverage probe's
findings: a domain lookup that names the workspace (`MYNTRA-93as3`), a listing that wants three
headers, 52 postings with about 6 tech-looking titles, no other tenant known, and a suggestion to
key the Board on the workspace id. It left open the `workflowid` rule, paging, the public job link,
the language and discovery, and asked for the storage cost per tech Job to be measured early
because 6 of 52 is thin.

## Decision

**The Board is the lower-cased career-site host** (`spire2grow:jobs.myntra.com`), and the scraper
resolves it to its workspace on every run. The workspace id loses on three measurements: the public
job link is `https://{host}/jobs/{displayId}` and no endpoint maps a workspace back to a host; the
id is case-sensitive while Board keys compare case-folded; and an unknown workspace answers 200
with zero rows, where an unknown host is a real 404 ("No Workspace Found for the domain name", 24
of 24 non-tenant hosts).

**One `_search` per Board, no detail pass.** `size=1000` reads every production Board whole (the
largest is 211; `size` was unclamped to 2,000 on a 1,547-row UAT demo), `total` equalled the rows
served on 4 of 4, and the per-posting detail repeats the listing row. Rows are de-duplicated by
`displayId`, because small pages repeat and drop rows on ties (2 of 3 walks at `size=50`), and a
shortfall against `total` goes to ADR-0121. Past the 10,000-row result window the walk stops and
marks the Board truncated.

**Only `workspaceid` is sent, with the app's `language: en`.** `workflowid` is ignored (7 of 7
values, absent included, answered alike), which settles the issue's open question.

**Every `_search` goes through one process-wide pacer at 31 s.** The search is metered per client
across all workspaces, at about two calls a minute on each of two servers (8 of 14 at 10 s
spacing; a drained budget refused the other tenants 6 of 6); a 429 rests the pacer for the stated
`X-Rate-Limit-Retry-After-Seconds`, and three refusals fail the Board. The prober reads the
unmetered `_count` (600 calls at 167 req/s, no 429) after the lookup, never `_search`.

**A workspace lands under one host.** Tata Communications' workspace answers on four hosts; the
company's own (`jobs.tatacommunications.com`) is the Board and the other three sit in
`PARKED_BOARDS`, since each would serve all 210 postings again.

**The ATS lands enabled.** The ledger holds 23 rows: 7 live over 4 workspaces, 3 of them parked,
so 4 Scrapable Boards and 3 Hiring Boards. Those three list 307 postings, 77 of them tech by
`is_tech(title, department)` (25.1%): Myntra 6 of 53, Tata Communications 60 of 211, Spire 11 of
43. All 307 are English. One run fetches 1,551,149 bytes, about 20 KB per tech Job; priced as
ADR-0158 priced jazzhr (~107 KB stored per posting), 307 postings cost about 33 MB for 77 tech Jobs,
**about 0.43 MB per tech Job against the ~2 MB bar**, more than four times under it. The yield
is small in absolute terms, not per Job.

## Alternatives considered

- **Key on the workspace id, as the issue suggested.** It would need the host anyway to build a
  link, carried in the ledger's `url` column beside a case-sensitive key, and its dead signal
  would have to come from `workspace/theme/{id}` rather than the listing. The host carries all
  three facts in one string.
- **Walk `size=100` pages, as the app does.** Three calls per large Board against a meter of about
  two a minute, and small pages are where the repeats and misses were measured.
- **Land disabled on thin yield.** The absolute yield (77 tech Jobs, 11 of them 16 months old on
  Spire) is small, but the storage bar is per tech Job, and at 0.43 MB it is well inside it. A
  disabled scraper would also leave Myntra, the benchmark company that prompted this, unserved.
- **Bury the second Tata hosts in an alias ledger.** CONTEXT.md names the alias ledger as the
  home of a Board published under a second hostname, which these are, and the existing
  `subset-reqs` signal would fit them (each lists exactly the postings of the one workspace, as
  Radancy's language twins do, ADR-0265). But a signal's first ledger for an ATS bumps
  `DEDUP_VERSION` (ADR-0222's amendment to ADR-0188), an epoch paid across the served table for
  three hosts whose rows were never served, and the ledger would want a per-ATS script to rewrite
  it (`dedupe_boards.py` groups on each scraper's `alias_key`, which here is the vendor API host,
  not the workspace). Parking the three hand-found hosts, with the measurement beside them, costs
  neither. If more second hosts turn up, a `subset-reqs` ledger with its own script becomes the
  better home.

## Consequences

- Myntra (53 postings, 6 tech), Tata Communications (210, 60 tech) and Spire (43, 11 tech) reach
  the index on the next pipeline run; Go Digit's workspace is live and empty.
- A run spends about a minute and a half on this ATS (94 s measured), almost all of it pacing; the four Boards
  are read one after another whatever `harvest`'s concurrency.
- A new host that resolves to a workspace already held is a second host, not a Board: park it, as
  CLAUDE.md's landing rule now says. Hosts that resolve only on `io-uat.spire2grow.com` are the
  vendor's staging copies and read dead.
- `verify-search-filters` can check this ATS's served rows only after a pipeline run has indexed
  them; its coverage gate passes now, since `URL_SHAPES` is generated from `url_shape`.
