# Freshness measurements after the source critique

## Withheld freshness (HS-C07)

`index sync` now updates `data/state/board_freshness.csv.gz` and
`data/state/board_freshness_report.json`, carried by the existing HF state cycle.
The CSV tracks each Live Board's last observed authoritative run, when its current
exclusion streak was first observed, and consecutive exclusion observations.
An unselected Board keeps its history; a complete observation resets the streak;
leaving the Live set drops its history. Times are sync observations, not exact ATS
fetch times. History before this instrumentation is **unknown**, never inferred
from `first_seen`, a cost estimate, or an old local snapshot.

The report includes every unresolved exclusion, even if that Board sat out this
run, and aggregates by ATS:

- `protected_rows`: indexed rows on Boards whose last observed attempt was excluded.
- `not_reseen_rows`: the subset absent from this corpus on Boards actually attempted
  and excluded this run. Per-Board values are null for unselected Boards, not zero.
- Known age since the last authoritative observation and the count whose age is unknown.

Neither count establishes that the Jobs are closed. This instrumentation does not
change scope exclusion, the two-absence grace period, or prune. Measurement failures
are logged and do not stop index maintenance. The ledger is compressed; per-Board
history is bounded by the current Live set. The report makes future freshness
decisions measurable without introducing a blind deletion age.

Synthetic verification covers two-day known age, unknown initial age, an unselected
Board, a later complete observation, and leaving the Live set. Production history
starts only after this change is deployed and runs; no pipeline was triggered here.

## Description differences (HS-C11)

Run `.venv/bin/python -u scripts/eval/measure_content_drift.py`. It pins the freshest
HF revision and downloads only the selected ATS's description fragments, then
compares a seeded bounded sample with its current public Board API. Private text
stays in ignored experiment artifacts; logs and per-Board results stream as work
completes. The default sample is three Lever Boards and ten held Jobs each, with a
64 MiB reference-download budget; Ashby and Greenhouse are supported alternatives.

This measures unchanged/different descriptions, not an organic employer-edit rate:
parser changes also count as differences. Missing Jobs, missing current text,
truncations and request failures stay separate; a single absence is not confirmed
closure. These inline-description ATSes do not price the more expensive detail-pass
ATSes. Results inform a later refresh decision; ADR-0021's deferred policy remains.

### First bounded live check

Measured 2026-09-12 against HF revision `2fb4c2120a9a1bb59b699e5107b011f18083cb0a`
(two Lever description files, 21.0 MiB), seed `20260912`, at most ten held Jobs per Board:

| Board | Unchanged descriptions | Different | Not returned |
| --- | ---: | ---: | ---: |
| `lever:base-flowa` | 4 | 0 | 0 |
| `lever:perforce` | 5 | 0 | 1 |
| `lever:findem` | 1 | 0 | 0 |

All three live probes completed. Ten current matches provide positive controls for
the comparison; the one absence is not a confirmed closure. The sample has only
eleven held Jobs and does not support a corpus-wide churn estimate or refresh cadence.
The working tree was dirty and that fact is captured in provenance. Local private
artifacts: `experiment/critique-content-drift/artifacts/2026-09-12T11-16-11.253146Z/`.
