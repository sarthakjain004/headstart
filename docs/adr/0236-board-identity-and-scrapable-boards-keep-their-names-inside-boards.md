# ADR-0236: `board_identity` and `scrapable_boards` keep their names inside `boards/`

**Status:** accepted · **Date:** 2026-09-26 · **Supersedes in part:**
[ADR-0232](0232-the-shared-library-is-grouped-into-packages-by-the-question-each-module-answers.md)
(two rows of its `boards/` table)

## Context

ADR-0232 planned `board_identity` → `boards/board_key.py` and `scrapable_boards` →
`boards/scrapable.py`. [ADR-0235](0235-where-the-package-layout-keeps-a-name-and-what-its-rewrites-leave-alone.md)
kept the Search-filter modules' `_filter` suffix because a module sharing a name with what its
callers handle is hidden by the first local of that name. A dry run of the `boards/` step found
both planned names in that trap:

* `board_key` is also the function most callers import from the module
  (`from headstart.board_identity import board_identity, board_key, lower_key`), so a file that
  needs the module and the function cannot hold both under one name.
* `scrapable` is already a local in `ingest/scrape_plan.py` and `ingest/update_ledgers.py`.

## Decision

Both move into `boards/` under their own names: `boards/board_identity.py` (ADR-0155's "board
identity") and `boards/scrapable_boards.py` (CONTEXT.md's **Scrapable Board**). The rest of the
`boards/` table stands.

## Consequences

* `boards/scrapable_boards.py` stutters, as `search_filters/india_filter.py` does, for the same
  reason.
* Their log tags stay `[board_identity]` and `[scrapable_boards]`.
