# ADR-0256: SenseHQ Boards land from a probe that reads the listing's own error

**Status:** accepted · **Date:** 2026-09-28 · **Relates to:**
[ADR-0012](0012-liveness-ledger.md) (the liveness ledger),
[ADR-0158](0158-jazzhr-and-jobvite-are-worth-their-storage.md) (the storage-per-tech-Job bar)

## Context

The SenseHQ scraper has been registered since before this change, but it had no liveness probe
and no ledger, so `scrapable_boards.load` never offered one of its Boards and it served nothing.
Its only named tenants were a test fixture (`zetwerk`) and an excluded sandbox (`trm-dev`).
Measurement: `docs/sensehq/2026-09-28_careers-api-measurement.md`.

## Decision

- **A Board is a subdomain label**, `{label}.sensehq.com`, keyed as the bare label, which is what
  the scraper's slug already is.
- **The probe asks the listing's page 0 and reads its answer:**
  - A 200 with an integer `data.count` is live, counted by `count`, 0 included.
  - A 500 carrying `master.career_page' doesn't exist` or `no organization found with subdomain`
    is dead.
  - Anything else is unknown.
  - A DNS failure is unknown too, because `*.sensehq.com` is a wildcard zone.
- **The pool is 82 labels**, drawn from Common Crawl, Wayback and a sieve of 11,704 tenant labels
  from seven Indian ATS ledgers. The first probe found 35 live Boards and 47 dead.
- **It lands active.** It costs about 13 KB per tech Job (3.28 MB for 250 tech Jobs), far under
  ADR-0158's bar of about 2 MB.
- **Scraper fixes made alongside, from the 2026-09-28 critique:**
  - A walk short of the stated `count` is marked through `mark_truncated_unless_negligible`
    rather than only logged.
  - A row without an id or title is skipped and counted, not raised on.
  - A single-place location gains its office's country.
  - `code` fills `requisition`.

## Alternatives considered

- **Read a DNS failure as dead**, the default for most probes. On a wildcard zone that failure is
  the local resolver, so it proves nothing.
- **Append the office's country to every location.** The location is free text and often lists
  places in several countries: nishith-desai lists Palo Alto, Singapore and New York beside its
  Mumbai office. The office's country would mislabel those.
- **Read the whole Board in one request with `pageSize`**, which works (`pageSize=1000` gave all
  279 rows). Not taken, because on Boards this small it saves a few requests a run and no finding
  asked for it.

## Consequences

- SenseHQ's live Boards enter the scrape on the next run.
- The Board figures in README and CONTEXT move with this ledger.
- The pool is small and has not been swept exhaustively. A later discovery pass (another label
  sieve, SERP) can land more rows under the same probe.
