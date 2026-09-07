# ADR-0118: A fact can wear a derivation's column — but then it isn't a fact anymore

**Status:** accepted · **Date:** 2026-09-07 · **Amends:**
[ADR-0061](0061-refreshable-metadata.md) (its fact/derivation table, for `remote` only — the
rest of the table is unchanged) · **Relates to:**
[ADR-0009](0009-experience-extraction.md), [ADR-0082](0082-salary-extraction-a-two-tier-cascade-no-estimate.md)
(the two existing cascades this borrows its shape from)

## Context

Every scraper already sets `Job.remote` from an ATS-native field (`workplaceType`, `remoteType`,
...) or, failing that, a location-string guess — never from the description. Reading real job
descriptions across all 19 ATSes found hundreds of postings where the description states the
work arrangement outright ("This is a remote position", `#LI-Remote`) while the field itself is
`None` or wrong — concentrated on Ashby (recruiters leaving `workplaceType` unset even at
remote-first companies) and on greenhouse/zoho (the field says `False` outright). Measured
against the live served table: at least 7,439 already-indexed rows qualify.

The decision to let the JD override the field, in the positive direction only (never the
reverse — an onsite/hybrid reading must never touch an existing field value, since a bad onsite
call would permanently and silently suppress a job with nothing to recover it), was made
directly with the user and is not revisited here. What ADR-0061 didn't anticipate is *where* the
result lives.

ADR-0061 classifies every stored column as exactly one of two things: a **fact**, re-observed
from the fresh scrape every run, or a **derivation**, `f(code, facts)`, re-derived only on a
version sweep. `remote` was a plain fact. The straightforward first cut kept it there and simply
overwrote `meta["remote"]` with the cascade's output in `doc_prep.to_meta()` — no new column, at
the user's explicit choice over the alternative (a separate raw-field column mirroring
`experience`/`min_years`, which was presented with its tradeoff and declined).

That first cut was wrong in a way review caught before merge, not in production. `update_meta`'s
Facts pass resyncs every `FACT_FIELDS` column, unconditionally, every run — and `remote` was
still in that list. So the very next time a Board carrying an already-JD-derived `True` got
re-scraped, the Facts pass silently overwrote it back to the raw field's current value, and the
overlay had no held description on that ordinary run (`descriptions` is `{}` off a sweep) to
reinstate it with. The override would vanish until the next version-bump sweep re-applied it —
directly contradicting the "always safe, idempotent, never loses information" claim the
one-directional design was supposed to guarantee.

## Decision

**`remote` is a third shape ADR-0061's table didn't have a row for: a fact whose *served* value
is a derivation.** Concretely:

- `remote` is **excluded** from `update_meta.FACT_FIELDS` (`_FACT_WITH_OVERLAY`), so the blind
  per-run resync that every other fact gets never touches it.
- The raw ATS-native value is still captured every run — `corpus_facts()` carries it in each
  Job's fact dict alongside `FACT_FIELDS` — but only `remote`'s own derivation block reads it,
  and only `on sweep or rederive`, the exact cadence `min_years`/`salary` already use.
- `doc_prep.to_meta()` is unchanged in shape: it still overwrites `meta["remote"]` with the
  cascade's output at scrape time, same as before, since a brand-new Job has no stored value to
  protect.

This makes `remote` safe under the same reasoning experience/salary already rely on: a
derivation only needs to run on a sweep because that's when a code change needs to reach
already-stored rows, and running it more often than that buys nothing while risking exactly the
silent-reversion bug above. The difference from experience/salary is narrower than it looks —
`remote`'s cascade input isn't a separate raw column, it's the fact dict `corpus_facts()` already
builds for every field, just not synced into the row automatically.

## Why not the separate-column alternative, again

Presented and declined by the user before this bug surfaced, and the bug doesn't change that
call — it only proves the *chosen* design (overwrite-in-place, sweep-gated) needs the
`FACT_FIELDS` exclusion to actually be idempotent, not that a separate column was necessary all
along. A separate `remote_field` column would sidestep this specific bug (the raw value would
have its own home, safe from being confused with the derived one), but at the cost of a real
LanceDB schema migration this fix does not need.

## Consequences

- **`remote`'s raw ATS field now lags to the next sweep**, not every run — a real behavior
  change from before this feature existed, when `remote` was a plain fact and synced
  immediately. A Board flipping its own `workplaceType` takes a version bump or an explicit
  re-derive queue entry to reach the served column, same lag every other derived field already
  has. Judged acceptable: the alternative (immediate sync) is what caused the bug this ADR fixes.
- ADR-0061's table (line for `company`/`location`/`remote`/... as bare "facts") is stale for
  `remote` specifically as of this ADR; it should read **"remote: fact re-observed AND
  derivation-overlaid — see ADR-0118"** rather than a bare fact, the same way its `has_description`
  row already carries an "Amended by ADR-0062" note for a different column.
- `corpus_facts()`'s fact dict is no longer exactly `{f: job.get(f) for f in FACT_FIELDS}` — it
  carries one extra field (`remote`) that the blind sync loop deliberately ignores. The
  docstring says so; a future fourth family in this shape should extend `_FACT_WITH_OVERLAY`
  rather than growing a third bespoke carve-out.
