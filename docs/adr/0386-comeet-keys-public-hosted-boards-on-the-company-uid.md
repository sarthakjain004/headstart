# Comeet keys public hosted Boards on the company UID

**Status:** accepted · **Date:** 2026-10-03 · **Relates to:** ADR-0023, ADR-0158, ADR-0219

## Context

Comeet (Spark Hire Recruit) embeds complete public posting JSON in its hosted board HTML.
No careers token or private API is needed. Renamed labels answer on both routes without a
redirect: echosoftware and echo share company UID 9a.006 and their posting ids. The label is
therefore an address, while the UID identifies the Board.

## Decision

Fetch the lowercase `label/uid` route and key the Board `comeet:{uid}`. Job ids append the
native posting UID. Read the two public JSON assignments; an absent envelope fails, a named
empty positions array is live-empty, a 302 to the vendor homepage is dead, and consent pages
are unknown. Exclude employee-only postings. Serve the canonical public posting URLs.

Before landing/refreshing the ledger, run `comeet_canonical_boards.py`: retain one verified
canonical public label per live UID in both pool and ledger. Only a successful canonical-page
read may replace previous rows. This prevents a dead old label's newer verdict from shadowing
its live replacement under ADR-0219. The initial normalization removed 54 redundant rows;
the final 5,063 rows contain 591 live Boards, 4,277 dead rows and 195 consent unknowns.
No new alias-ledger signal or served deduplication migration is required.

Descriptions/requirements, company, location, department, experience and type come from the
same page. Explicit workplace type wins over the misleading location boolean (Port's hybrid
Account Manager has `is_remote=true`). No posted date is fabricated from `time_updated`.
No native structured salary was observed. No detail pass or tech-detail gate is needed.

Enable it: 113,839,488 bytes / 3,094 tech postings ≈ 37 KB/tech posting. Exclude the Demo
Company's 695 fabricated postings. Pace process-wide starts at one second; a courteous
8-request test at concurrency 4 succeeded, and the census saw no 429. The knee is unknown.

## Alternatives considered

The Careers API would require extracting a public token but adds no needed data. Label-based
job identity would change on rename. Keeping every old label beside a UID-folded live row
would let a later dead alias supersede the current live address.

## Consequences

The current address can change without re-keying jobs. Canonicalization is a required landing
operation; failed canonical reads preserve previous evidence. Consent pages are not bypassed.
The [measurement](../comeet/2026-10-03_public-api-measurement.md) records discovery coverage,
non-English prevalence and the post-pipeline served-row verification still required.

Review follow-up: explicit On-site now overrides contradictory Remote location text
(41 captured jobs; Zero Networks 83.26C is the regression fixture). Canonical normalization
preserves capture timestamps, rejects proofs older than any ledger verdict, streams each
attempt, and atomically checkpoints dated proofs; `--resume` continues a partial scan.
Captures with no reliable timestamp are declined rather than stamped as current.
