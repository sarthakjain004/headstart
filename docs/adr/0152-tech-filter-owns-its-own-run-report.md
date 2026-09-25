# ADR-0152: `tech_filter` owns its own run report

**Status:** accepted · **Date:** 2026-09-15 · **Relates to:** [ADR-0028](0028-ingest-package.md) (the ingest package layout), [ADR-0017](0017-tech-role-filter.md) (the tech gate this stage runs)

## Context

An architecture review of `src/headstart/ingest/` flagged `filter_tech.py` as a candidate for
deepening. `filter_tech.main()` did two things: it called `tech_filter.filter_jobs(src, dst)`, and
it then read the returned `{ats: (kept, total)}` stats to print a per-ATS table, detect and warn
about ATSes that contributed zero rows (with reasoning about how `harvest` opens one file handle
per ATS in a shard's list, so a failed, deferred, or genuinely empty ATS all leave the same empty
file), and log an ERROR line if the whole run produced zero rows. That is ~45 lines of
interpretation logic sitting in the entry point, with `tech_filter.filter_jobs()` reduced to a
function that returns raw counts nobody but its one caller knows how to read.

The review's first idea — delete `filter_tech.py` and fold everything into `tech_filter.py` — does
not survive ADR-0028. That ADR mandates exactly one module per pipeline stage step, run as
`python -m headstart.ingest.<module>`, and `python -m headstart.ingest.filter_tech` **is** stage 3's
required entry point (`src/headstart/ingest/__init__.py`'s stage list, `.github/workflows/pipeline.yml`).
Every sibling stage — `scrape_plan`, `scrape_run`, `scrape_join`, `embed_plan`, `embed_run`,
`embed_merge`, `update_meta`, `index`, `role_trends` — keeps the same one-file-per-stage shape even
where (`embed_plan`, `role_trends`) the module is not itself thin. Deleting `filter_tech.py` would
not remove code, only relocate it while breaking that convention for one stage alone.

What the deletion test does correctly identify is narrower: the module *survives* as a pipeline-stage
entry point, but its *contents* — the reporting logic specifically — were shallow for where they
sat. Interpreting `filter_jobs`' own stats (what counts as "empty", what a zero-row run means) is
domain knowledge about the tech filter, not about being a `python -m` entry point, and it belongs
next to the function whose output it interprets.

## Decision

Move the reporting logic from `filter_tech.main()` into `headstart.jobs.tech_filter`, as two new
functions:

- `tech_filter.report(stats, dst_dir, logger) -> None` — the per-ATS table, the zero-ATS warning
  (with its comment about `harvest`'s per-ATS file handles and the failed/deferred/genuinely-empty
  ambiguity), and the corpus-wide zero error, all logged through the given `logging.Logger`.
- `tech_filter.filter_jobs_and_report(src_dir, dst_dir, logger) -> stats` — `filter_jobs` followed
  by `report`, i.e. what the entry point actually needs to run.

`filter_tech.main()` now parses `--src`/`--dst`, validates the source directory, and calls
`filter_jobs_and_report(args.src, args.dst, _log)` — argument parsing plus one call into the real
logic module, the same shape `scrape_plan.py` and `embed_plan.py`'s entry points already have.
`tech_filter.filter_jobs()` itself is untouched: it still just filters and returns stats, and
`headstart.__main__` (the curated-feed path, which does its own simpler summary and must not import
from `ingest` per CLAUDE.md's Repo Conventions) keeps calling it directly, unaffected by this change.

**`report` takes the caller's logger rather than opening its own.** `log._Formatter` stamps every
line with the logger name's last dotted segment as its `[tag]` (ADR-0039), and
`scripts/runlog/fanout_corpus.py`'s `TECH`/`TECH_TOTAL`/`TECH_EMPTY`/`TECH_ZERO_TOTAL` patterns hard-
code `\[filter_tech\]`. If `tech_filter.py` opened its own logger via `log.get(__name__, __spec__)`,
its lines would tag as `[tech_filter]` instead — a silent break of that consumer contract, since
`tests/test_log_contract.py` only checks that the tag *declared* for the `filter_tech` entries
matches what a real run emits, not that the code lives in any particular file. Passing the entry
point's own `_log` down keeps every emitted record's logger identity — and therefore its tag —
exactly as it was before this move; `test_report_logs_through_the_callers_logger_so_the_tag_is_preserved`
in `tests/test_jobs_tech_filter.py` pins this directly.

## Consequences

- `filter_tech.py` drops from 81 lines (main() ~50) to 43 lines (main() ~12) and now reads like
  every other thin ADR-0028 stage entry point.
- `tech_filter.py` gains the reporting logic beside `filter_jobs()`, so the module that owns the
  tech gate (ADR-0017) also owns how to read and present its own run's stats.
- New unit coverage lives directly on `report`/`filter_jobs_and_report` in `tests/test_jobs_tech_filter.py`
  (per-ATS table + total, an ATS present with zero rows vs. one never in the slice at all, the
  corpus-wide zero, and the logger pass-through), independent of `tests/test_log_contract.py`'s
  existing end-to-end checks (`_tech_gate`, `_tech_gate_nothing`) — both now exercise the same code,
  reached through `filter_tech.main()` for the contract and directly for the unit tests.
- No behavior changed: log levels, wording, and the `[filter_tech]` tag are identical to before this
  move. This is a pure relocation, not a functional or interface change to what a pipeline run does.
- No new pipeline stage, no new module count under `src/headstart/ingest/` — ADR-0028's one-module-
  per-stage rule is unaffected; only what lives *inside* the existing stage module changed.
