# ADR-0164: Mark when the definition changed, not just when the data did

**Status:** accepted · **Date:** 2026-09-16 · **Extends:** [ADR-0040](0040-role-trend-ledger.md)
(the centroid `version` this generalises), [ADR-0057](0057-record-family-assignments-and-report-reassignment.md)
(the reassignment ledger whose confusion this closes one more instance of) · **Amended by:**
[ADR-0220](0220-a-trained-title-classifier-decides-a-role-family.md) — the sixth column is
`family_classifier_version`, renamed in place from `family_rules_fingerprint`

## Context

`role_trends` already segments its series by centroid `version` when a refit re-bases the whole
taxonomy — the one methodology break the ledger knows how to mark. Three more things silently
change what a family/band count *means*, with nothing recording when they did:

- **`config/role_families.json` can be re-curated without a refit.** Splitting a family or moving
  a cluster changes which jobs land where, at a specific moment, and `roles.load_families` only
  checks that the map's stamped `centroid_version` matches the fit — nothing checks whether the
  map's own content changed. A re-curation shows up in `role_reassignments.csv` (ADR-0057) as a
  burst of transitions timed to the edit — which looks **identical** to organic re-embedding
  drift, the exact confusion that ledger exists to resolve.
- **`headstart.tech_filter` has no version at all.** Widening or narrowing a pattern changes
  which jobs enter the served index, and therefore every downstream count, with zero record of
  when. This project is actively iterating on that filter (the description-recall analysis that
  immediately preceded this ADR recommends widening several patterns), so this is not a
  hypothetical.
- **`doc_prep.DERIVATIONS_VERSION` can reshuffle seniority bands** when `update_meta` re-derives
  already-indexed rows on a version bump, on a schedule unrelated to hiring.

Any one of these produces a step in the chart that looks exactly like a hiring trend. Given the
stated goal — trends that stay reliable over months while the rest of the project keeps changing
— this is the gap most likely to be hit, repeatedly, by the project's own normal development.

## Decision

**Stamp four values every tick, and record a row only when one of them differs from the last
recorded row.** New module `headstart.ingest.trends_epochs`, driven from `role_trends`:

- `centroid_version` — the existing fit version (ADR-0040).
- `family_map_fingerprint` — a content hash of `config/role_families.json`'s meaning (which
  cluster maps to which family, and the non-tech set), via `roles.family_map_fingerprint`. A
  hash rather than a hand-maintained counter: a curation edit is then detected automatically
  instead of depending on someone remembering to bump a version — the exact failure class
  CLAUDE.md's `DERIVATIONS_VERSION` rule documents happening twice already for a version a human
  has to remember to move. Free text (`label`, `note`) is excluded on purpose: rewording a
  family's description is not a change to what it counts.
- `tech_filter_version` — a new hand-bumped constant, `TECH_FILTER_VERSION` in
  `tech_filter.py`, mirroring `DERIVATIONS_VERSION`'s own discipline. Unlike the family map this
  is code, not data, and bumping it is the same deliberate judgement call `DERIVATIONS_VERSION`
  already asks for.
- `derivations_version` — read directly from the existing `doc_prep.DERIVATIONS_VERSION`.

`data/state/trends_epochs.csv` gets one row only when the four-value stamp changes — mirroring
`role_reassignments.csv`'s own "append only when there's something to report" shape, not
`role_trends.parquet`'s "append every tick." Every row in the file is therefore already a real
boundary, and the count stays small by construction (expected to change rarely).

**The `/trends` route exposes `epochs`: `{ts, changed}` for every boundary in the requested
window**, `changed` naming which of the four things moved in words a reader doesn't have to
decode from a raw version integer. It is filtered by `since`/`until` like the rest of the
response, but deliberately **not** by `ats` or the live centroid version — a refit is itself one
of the four things that can produce a boundary, so scoping epochs to "the live version" would
hide the exact event most worth marking.

**The chart draws a vertical marker at each epoch's stamp**, behind the series (same layer as the
gridlines and the reference line), with a native `<title>` tooltip naming what changed. A stamp
with no exact match in the visible `stamps` array (predates this feature, or fell outside the
window) is skipped rather than approximated.

## Rejected alternatives

- **A hand-curated changelog a human appends to.** Considered first, and rejected for the same
  reason `family_map_fingerprint` is a hash rather than a counter: a manual step is a manual step
  someone will eventually skip, and this project has already hit that failure twice for
  `DERIVATIONS_VERSION`. Automatic detection where the underlying thing is data (the family map)
  costs nothing and cannot be forgotten; a hand-bumped version stays appropriate only where a
  human is already required to make a judgement call about code semantics (`tech_filter`,
  `derivations_version` — both pre-existing or newly-added counters of that kind).
- **A hash of `tech_filter.py`'s source instead of a manual version.** Rejected: it would fire on
  any edit including comments or refactors with zero classification effect, producing false-
  positive epochs on every unrelated cleanup — noisier and less intentional than the counter
  pattern this project already trusts.
- **Extra columns on `role_trends.parquet` itself.** Would duplicate four small integers across
  every row of every tick for information that is per-tick, not per-row, and ADR-0057 already
  rejected the analogous "put a diagnostic on the served/ledger row" move for the same reason:
  it coerces a diagnostic about the taxonomy into a contract that doesn't need to carry it. A
  side ledger, changed-rows-only, costs nothing extra and stays out of the way.

## Consequences

- A level shift on the chart can now be read against `epochs`: if a marker sits at the same
  stamp, the shift is a definition change, not a hiring trend. Nothing about this stops anyone
  from changing the tech filter, the family map, or the extraction logic — it only makes the
  change visible in the one place it would otherwise be silently absorbed.
- **This does not close every version boundary in the ledger.** ADR-0143's Board-delta cohorts
  already isolate onboarding a new ATS or Board; centroid refits were already marked. This ADR
  closes the remaining three the register named. If a future change introduces a fifth kind of
  silent redefinition, it is not caught here automatically — it needs its own stamp added to the
  same tuple.
- `family_map_fingerprint` changing says *that* the map moved, not *what* moved within it — a
  reader who wants the detail still needs `git log config/role_families.json` for the actual
  diff. The epoch is a pointer to look, not the explanation itself.
