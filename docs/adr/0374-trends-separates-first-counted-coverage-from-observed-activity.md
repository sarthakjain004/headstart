# ADR-0374: Trends separates first-counted coverage from observed activity

**Status:** accepted · **Date:** 2026-10-02 · **Amends:** ADR-0248's UI coverage default;
builds on ADR-0143, ADR-0227 and ADR-0230.

Default the Trends UI to its existing comparable Board cohort, labelled **Sites tracked
at start**, while preserving explicit All. A new `coverage_summary` separates that fixed
first-counted population, entrant first-counted backlog, entrants' subsequent observed
activity, and all-known tech inventory. Added Boards of existing Companies are coverage
additions. Departed Boards stay in the original denominator; date changes disclose a
different population.

The fixed-cohort-only alternative hides subsequent newcomer activity. The all-known-only
alternative mixes discovery backlog with movement. Neither count history nor arithmetic
reconciliation establishes complete authoritative starting eligibility, successful
zero-Board reads, endpoint freshness or employer causes. Those remain explicitly unknown;
recorded Closed is labelled **recorded closures/removals**, including possible policy
evictions. The API default and existing field meanings remain compatible. No stock is
backfilled from posting dates. A later quality-aware population requires stored Board-read
history including successful empty sites and independent cause/coverage verification.

Response contract, limits and follow-up plan:
[coverage panels design](../trends/2026-10-02_coverage-panels-design.md).
