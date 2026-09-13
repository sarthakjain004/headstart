# PR #449 post-merge critique and fixes

Reviewed shipped commit `c8004ae5143022157627a9f4f9e6ef8583c7079e` against its
parent. A dedicated independent critique identified six defects. The fixes here
do not add back the documentation or scripts excluded from that PR.

## Findings and remediation

1. **P1: interrupted aggregate writes lose delta effects in the consumer.** The
   producer persists Board deltas before the aggregate measurement. Replay formerly
   visited only aggregate timestamps: an intervening durable delta was ignored.
   Replay now visits every delta timestamp but emits only recorded measurements.
   Regression: measurements T1/T3 with deltas +10 at T1 and +5 at T2 yield 10/15.
2. **P2: default comparable coverage selects unsupported history.** Aggregate
   history predates Board deltas. An omitted baseline now selects the earliest
   supported measurement; an explicitly earlier baseline still returns no history.
3. **P2: an emptied cohort loses its final measurement.** Removing zero groups
   removed the endpoint from the response timeline. Replay now retains zero groups,
   including for an ATS-filtered cohort, so 10 to 0 remains visible.
4. **P2: comparable coverage ignores the display start.** `since` now filters both
   coverage modes independently of `base`, which continues to determine membership.
5. **P2: freshness uses pre-sync IDs.** Newly added and re-embedded Jobs were omitted
   from protected-row counts. Telemetry now receives retained IDs minus evictions
   plus additions. A real sync regression verifies two protected rows after an
   addition and an upgrade on an Unauthoritative Board.
6. **P1: telemetry failure aborts publication after table mutations.** Expected
   filesystem and malformed-state failures now produce one bounded warning without
   suppressing the sync witness. Indexing errors remain outside that exception
   boundary. Injected I/O, value and missing-field failures verify successful sync
   and its witness. Corrupt gzip payloads and incompatible persisted timestamps are
   covered too; the final fault-injection matrix covers seven exception types.

The two freshness regressions were run against the shipped code and failed before
the implementation change. No atomic-subscription or resume regression was
demonstrated by this critique. These are local fixes, not a production deployment.

## Verification commands

- `uv run pytest -q`
- `node --test tests/js/*.test.js`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `git diff --check`

Results: full Python run: **2925 passed, 1 skipped, 2 xfailed, 1 failed**.
The sole failure is `test_every_adr_has_exactly_one_index_row`: locally preserved,
untracked ADRs 0105–0109 exist without index entries, as requested when excluding
them from #449. No excluded file was deleted or added back to fix that local-only
condition. After completing the telemetry exception matrix, the focused suite
passed **172 tests**. JavaScript: **310 passed**. Ruff lint/format and diff whitespace
checks passed.

## PR #450 two-axis review

Fixed point: `c8004ae5143022157627a9f4f9e6ef8583c7079e`; reviewed commit `277c2984`.

### Standards

No findings: no documented-standard violations or actionable judgment-call smells.

### Spec

One P2 finding: an omitted `base` still fell back to `since`, contradicting the
earliest-supported baseline and independent display-window requirements. Fixed by
passing only `base` into replay. Two regression cases cover a display start before
supported history and after the baseline, with a newly introduced Board excluded
in both cases. Both cases failed before the fix.

Post-fix targeted verification: 174 tests passed; Ruff lint/format and diff checks
passed. Standards: 0 findings. Spec: 1 finding, fixed; none deferred.
