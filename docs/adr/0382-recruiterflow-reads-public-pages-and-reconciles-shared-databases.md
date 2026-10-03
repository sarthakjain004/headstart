# Recruiterflow reads public pages and reconciles shared databases

**Status:** accepted · **Date:** 2026-10-03 · **Relates to:** 0012, 0111, 0158, 0166, 0201

## Context

Recruiterflow was one of the largest unsupported families in the earlier company-resolution
audit. Its documented customer API requires a private key; its public career pages instead
embed the complete listing and each Job's details without credentials. The initial 41-Board
measurement found 662 Jobs and 137 tech matches. Later archive discovery found a 10,809-Job
Board, so neither the initial maximum of 112 nor a hard pagination ceiling is a valid limit.
See [the measurement](../recruiterflow/2026-10-03_public-api-measurement.md).

## Decision

A Board is its lowercase public path label; filters, job ids and tracking parameters are
not identity. Read `window.jobsList` once, select the department grouping, and join every
location grouping by native id. Details read `convertedToJSON`; no JavaScript is executed.
The listing's title and department remain authoritative, so the pre-detail tech gate is exact.
Always fetch a requested detail because it also supplies experience. A failed detail keeps
the listed Job and records the loss. Never substitute a JSON-LD title for an absent description.

An empty jobsList is live; a 404 or the measured inactive application template is gone.
Other failures are unknown. Requests are spaced 1.25 seconds across the process, within the
measured sequential envelope; this is a conservative bound, not a claimed vendor quota.

Readable and `db_...` labels can expose the same database without redirecting. Reconcile them
through `recruiterflow_shared_boards.py`: require both the same database identity and equal,
nonempty complete posting sets. Partial overlaps and empty sets do not prove an alias. Re-run
the validator after ledger refreshes. These initial aliases require no `DEDUP_VERSION` bump:
this change introduces the ATS rather than changing identities of previously supported Jobs.

The ATS lands enabled. The initial gated sample costs about 0.152 MB per tech Job, below
ADR-0158's approximately 2 MB comparison point. This purposive sample is not a market-wide
yield forecast. Board totals are computed from the committed ledgers in CONTEXT.md.

## Alternatives and consequences

The private API is unnecessary. A title-only or first-location parser loses source metadata;
the JSON-LD detail location alone would discard locations on 170 of the 662 sampled Jobs.
The public HTML protocol is not a vendor stability guarantee, so missing data raises an
unreadable-Board error rather than confirming an empty Board. The older three-year CC range
remains incomplete where the archive returned repeated 502 responses.
