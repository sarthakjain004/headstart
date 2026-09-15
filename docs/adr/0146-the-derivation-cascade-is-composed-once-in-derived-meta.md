# ADR-0146: The derivation cascade is composed once, in `derived_meta`

**Status:** accepted · **Date:** 2026-09-15 · **Relates to:**
[ADR-0061](0061-refreshable-metadata.md) (facts vs. derivations, `DERIVATIONS_VERSION`),
[ADR-0138](0138-a-materialized-country-column-serves-the-india-filter.md) (`country`, the most
recent family added to the cascade)

## Context

Four field-extractors — `headstart.experience`, `headstart.salary`, `headstart.geo`,
`headstart.remote` — derive a Job's `remote`, `country`,
`min_years`/`max_years`/`experience_source` and
`min_salary_annual`/`max_salary_annual`/`salary_currency`/`salary_source` columns. Two callers run
this composition, independently:

- `doc_prep.to_meta` (`src/headstart/ingest/doc_prep.py`) — the cold-start path, called from
  `embed_plan`/`embed_run` when a Job is embedded for the first time.
- `update_meta.refresh_row` (`src/headstart/ingest/update_meta.py`) — the repair path (ADR-0061),
  called from the merge job to re-observe facts and, on a `DERIVATIONS_VERSION` bump or a
  per-row rederive, re-run the cascade against a held description.

Each wrote out the same four extractor calls and the same span-to-dict assembly by hand, and no
test exercised both: `to_meta` is covered by `tests/test_embed_run.py` and
`tests/test_taleo_be.py`, `refresh_row`'s branching policy by `tests/test_update_meta.py` (42
tests), and the two files never mention each other's function. A future edit to what an
extractor's result maps to — a renamed key, a dropped field — could land in one file and not the
other with nothing to catch it.

**Direct comparison found no existing disagreement.** Read side by side, `to_meta`'s and
`refresh_row`'s held-description assemblies were already byte-for-byte identical — same argument
order into `extract`/`extract_salary`, same span-to-dict field names for every one of the four
families. The risk this ADR closes is a latent one (the next edit), not a live bug; no
`DERIVATIONS_VERSION` bump accompanies this change; see Consequences.

## Decision

Extract the composition into a new module, `src/headstart/ingest/derived_meta.py`, alongside
`doc_prep`/`update_meta` (the only two callers, both ingest-pipeline stages — CLAUDE.md's repo
layout keeps this kind of shared pipeline logic in `ingest/`, not `headstart/` proper, which the
curated feed path must never import from).

The module exports one function per family (`experience_meta`, `salary_meta`, `country_meta`,
`remote_meta`), each running its extractor and returning that family's meta keys as a small dict,
plus `derive(job: dict) -> dict`, which calls all four against a job-shaped dict and merges their
results. `to_meta` calls `derive(job)` directly — it always wants all four families from the
Job's current facts. `refresh_row` calls the four per-family functions individually from its
existing four blocks, because its families are re-derived under **independent** trigger
conditions (`inputs_moved`, `salary_inputs_moved`, `country_inputs_moved`, and remote's own
`sweep or rederive` with no fact-drift variant at all) — collapsing them into one unconditional
call would recompute (and risk overwriting) a family whose own guard says leave it alone.

Two families (`experience`, `salary`) also keep a second, separate low-level function
(`experience_fields`, `salary_fields`) that turns an already-computed span into its meta keys
without calling the extractor. `refresh_row`'s no-held-description fallback
(`_rederive_without_text`/`_rederive_salary_without_text`) produces a span without running the
full cascade — passing `description=None` through `experience_meta`/`salary_meta` would read as
"nothing stated" and null a floor the row was originally derived from text it no longer has
re-readable — so that fallback still calls the module's own tier-1/tier-3 functions directly and
only shares the span→dict assembly, not the cascade call itself. `country`/`remote` need no such
split: neither has a text-dependent fallback (`classify` doesn't read text at all;
`remote`'s overlay degrades gracefully to the raw field on `description=None`, which is exactly
what `refresh_row` already relied on before this change), so their one function each covers both
callers outright.

**What stays in `refresh_row`, unmoved:** every guard condition, the `_KEEP` sentinel, and
`_FACT_WITH_OVERLAY` — the policy of *whether* and *from what* to re-derive. This ADR moves only
the "extractor(s) in, meta keys out" computation; `update_meta`'s module docstring already frames
that split (facts vs. derivations, ADR-0061) and this keeps it.

### Alternatives considered

- **One `derive(job)` call site inside `refresh_row` too**, replacing all four per-family blocks.
  Rejected: proven safe only in the specific case where `sweep` or a row's own `rederive` flag is
  set (then all four families' outer conditions are true together, by construction); an ordinary
  run where only `country_inputs_moved` fires would otherwise force the other three families
  through the cascade against a `descriptions` dict that legitimately holds nothing for that row.
  Keeping four call sites, sharing only the per-family functions, matches the existing (and
  correct) independent-trigger policy instead of fighting it.
- **A typed record instead of a dict.** `derive`'s return value lines up with real, named
  columns (`doc_prep.META_FIELDS`, `index._schema()`), which a dataclass would state more
  strongly than a dict does. Deferred: every caller on both sides already speaks dict (`row.update(...)`,
  `meta.update(...)`, JSON Lines on disk), so a typed record would need to be unpacked back into a
  dict at every call site for no behavior change — a schema-duplication cost with no reader
  benefit yet. Revisit if `derived_meta`'s output needs validating independent of `_schema()`.

## Consequences

- `doc_prep.py` no longer imports `headstart.experience`/`headstart.geo`/`headstart.remote`/
  `headstart.salary` directly; `update_meta.py` keeps direct imports only for the tiers its
  no-held-text fallback calls itself (`from_field`, `from_seniority`, salary's `from_field`).
- `tests/test_derived_meta.py` is new: it asserts `to_meta(job)` and `refresh_row`'s cold-start
  sweep (`sweep=True`, no prior stored values) agree on all nine derived keys, across a
  field-stated salary, a description-stated experience floor, a seniority-fallback floor, and a
  Job with nothing extractable. It cannot yet fail on real drift (both callers now share the same
  function), so its value is forward-looking: it pins the agreement this ADR establishes so a
  future edit to one call site's inputs cannot silently diverge from the other's.
- **No `DERIVATIONS_VERSION` bump.** The composition's output is unchanged for every input — this
  is a pure relocation, confirmed by the line-for-line comparison above and by the new test.
  Flagged here rather than decided unilaterally, per this repo's version-bump discipline
  (CLAUDE.md): if a reviewer disagrees, that discipline says the version comment must cite the
  exact commit range and the measured effect, neither of which a "no functional change" refactor
  has to offer.
- `headstart.geo.classify`'s docstring, which named `doc_prep.to_meta` as the direct caller of
  `classify`, now names `derived_meta.country_meta` — the caller is one hop further away.
- CONTEXT.md gains a **Derivation cascade** glossary entry (§Pipeline scheduling and sharding),
  distinguishing this four-family composition from the _tiered_ fallback inside one extractor
  (field → description → seniority, ADR-0018), which the term does not name.
