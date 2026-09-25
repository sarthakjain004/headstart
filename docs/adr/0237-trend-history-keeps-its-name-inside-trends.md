# ADR-0237: `trend_history` keeps its name inside `trends/`, and `trend_reading` becomes `line_reading`

**Status:** accepted · **Date:** 2026-09-26 · **Supersedes in part:**
[ADR-0232](0232-the-shared-library-is-grouped-into-packages-by-the-question-each-module-answers.md)
(one row of its `trends/` table) and
[ADR-0233](0233-trends-serves-reconciled-line-readings-and-the-page-only-formats.md) (the name it
gave `trend_reading` in `trends/`)

## Context

ADR-0232 planned `trend_history` → `trends/history.py`, and ADR-0233 said `trend_reading` would
become `trends/reading`. A dry run of the `trends/` step found both names already bound as locals:
`history` in `deploy/hf-space/app.py` and four test modules, `reading` in `app.py` and
`tests/test_space_app.py`, each of which also imports the module. That is the trap
[ADR-0235](0235-where-the-package-layout-keeps-a-name-and-what-its-rewrites-leave-alone.md) and
[ADR-0236](0236-board-identity-and-scrapable-boards-keep-their-names-inside-boards.md) resolved
for the Search-filter modules, `board_identity` and `scrapable_boards`: a module named like what its
callers handle is hidden by the first local of that name.

## Decision

`trend_history` moves to `trends/trend_history.py` under its own name, the name its `TrendHistory`
class shares. `trend_reading` moves to `trends/line_reading.py`: CONTEXT.md's **Line reading**,
which ADR-0233 itself introduced, and a name no local holds. The rest of the `trends/` table stands.

## Consequences

* `trends/trend_history.py` stutters, for the same reason `boards/scrapable_boards.py` does.
* Its log tag stays `[trend_history]`.
