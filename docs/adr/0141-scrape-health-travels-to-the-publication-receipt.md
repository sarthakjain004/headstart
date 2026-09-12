# ADR-0141: Scrape health travels to the publication receipt

- **Status:** Accepted
- **Date:** 2026-09-12
- **Amends:** [ADR-0045](0045-per-shard-run-reports.md)

## Context

A successful pipeline process is not evidence that its fresh coverage was healthy or that every
state directory published. Five reviewed executions included two green runs while Workday Board
failures rose to 35.3% and 80.8%; another execution built a new index locally but published only
the embedding store before its LanceDB write guard stopped the remaining uploads.

The shard reports already carry successful, failed and partial Board outcomes. This change adds
structured listing/detail loss counters, but leaving their aggregation separately implemented in
`scrape_run` and `scrape_join` immediately produced two different report shapes. A later merge job
also cannot correlate coverage with publication if the join leaves the verdict only in its log.

## Decision

`headstart.ingest.observability.ScrapeHealth` is the one aggregation and rendering contract for:

- attempted, successful, failed and partial Boards per ATS;
- listing pages, fetch calls, settled status failures and request failures per ATS;
- detail Jobs, Jobs for which a request was attempted, loss events, settled HTTP failures and
  circuit-breaker skips per ATS;
- bounded listing/detail cause summaries with the number of affected Boards.

The verdict is strict and threshold-free: any failed or partial Board makes fresh coverage
`DEGRADED`. That word is a visibility verdict, not a pipeline failure or an assertion that every
lost observation has user impact. It avoids an arbitrary percentage that would hide a complete
outage on a small ATS or need recalibration as the Slice changes.

`scrape_join` writes the same aggregate to `data/state/scrape_health.json`. It is small, streams to
the merge job inside the existing `corpus-state` artifact, and later publishes with the other
pipeline state. The upload step records whether the embedding store, LanceDB index, description
store and pipeline state were published, failed, conflicted, or were not reached. An `always()`
step writes one GitHub summary containing both the fresh-coverage verdict and those receipts, plus
one complete-publication verdict.

Scrape health remains telemetry. A missing or unwritable health artifact cannot sink the join, and
the verdict does not alter eviction scope, retry, quarantine, indexing or publication behavior.

## Rejected alternatives

- **Infer health from the workflow conclusion.** Board isolation intentionally keeps a run green
  through individual failures; the conclusion answers orchestration, not coverage.
- **Choose a degradation percentage.** No measured threshold separates routine failures from a
  material outage across ATSes of very different sizes. Strict visibility adds no control-flow
  consequence, so it needs no tolerance.
- **Keep only prose logs.** Publication runs in another job, and parsing free text would recreate
  the audit tooling inside production.
- **Implement shard and join summaries separately.** The telemetry field list and wording already
  drifted during the first implementation.
- **Make telemetry fatal.** Losing a summary must never lose a scrape or prevent safe publication.

## Consequences

The run page distinguishes “fresh coverage degraded” from “publication incomplete,” and can show
both at once. Every ATS keeps its own denominators; a dominant ATS can no longer hide smaller loss
categories inside a global total. New counters or wording belong in `ScrapeHealth`, not separately
in each caller.

The health file is latest-run state rather than historical storage. Durable trend analysis still
belongs in captured run artifacts or a purpose-built ledger.
