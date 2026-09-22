# Frozen v3 vanity-host fingerprint sweep

Started locally at **2026-09-22 20:09:04 IST**, with the main-checkout harvest continuing.

- Source byte cutoff: `2403914993`; complete source rows: **293,197**.
- Selected only `_ats == "vanity"`: **135,917 postings / 8,391 distinct hosts**.
- After separating employer identities: **13,356 inputs**.
- Deep fallback mode, eight host workers, two browser slots, per-origin API gates.
- Highest-job-volume inputs are submitted first; completions stream out of order.
- `fingerprints.csv` and `progress.log` flush after each completion. The process is resumable.

Local artifacts (intentionally excluded from Git):

- `artifacts/vanity-snapshot.jsonl`: frozen minimal harvest records; no descriptions or API key.
- `artifacts/manifest.json`: cutoff, counts, channel version, and exact launch command.
- `artifacts/fingerprints.csv`: streaming classification/candidate output.
- `artifacts/progress.log`: unbuffered stdout/stderr.
- `artifacts/worker.pid`: exact local worker PID.

Inspect progress from this worktree:

```sh
tail -f experiment/indeed-fingerprint-2026-09-22/artifacts/progress.log
```

Resume the same snapshot:

```sh
uv run --extra discovery python -u scripts/discover/fingerprint_careers.py indeed \
  experiment/indeed-fingerprint-2026-09-22/artifacts/vanity-snapshot.jsonl \
  experiment/indeed-fingerprint-2026-09-22/artifacts/fingerprints.csv \
  --deep --workers 8
```

This log records the launch, not a completed census. Fingerprints are candidates; live/API job
verification and canonical alias checks are separate. The run does not edit liveness ledgers.

Method and smaller live comparisons: [fingerprinting report](../../docs/discovery/2026-09-22_indeed-fingerprinting.md).
