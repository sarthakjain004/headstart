# ADR-0052: Watch the large domain roles too, not only the small ones

**Status:** accepted · **Date:** 2026-08-13 · **Amends:** [ADR-0051](0051-trends-as-share-flow-and-watched-roles.md)

## Context

ADR-0051 added watch roles: a curated role tracked by **title pattern** rather than by centroid,
appearing as a series inside its parent family's drill. It justified them narrowly. The watchlist
was for roles *"too small to earn a centroid"* — the seed entry, Forward Deployed Engineer, sits
well under 0.2% of the clustered corpus, and `roles.WatchRole`'s docstring said as much.

That premise made the feature nearly invisible in practice. Read against the shipped UI:

- The by-role drill un-hides only for families named in `watch_parents`, which is derived from the
  watchlist. With one entry, **exactly one family of twenty-four** offered the drill at all.
- **AI / Machine Learning offered nothing**, because no watched role named `ai-ml` as its parent.
- Inside Software Engineering the drill drew **a single line** — a chart of one series.

So the mechanism worked and answered almost nothing. The obvious repair is more entries, and the
roles a reader actually wants under Software Engineering are Backend, Frontend, Full Stack, DevOps
— which are **large**: whole percents of the tech corpus each, an order of magnitude past the
fraction of a percent ADR-0051's rationale contemplated. Adding them contradicts the stated
premise, so the premise, not the addition, is what needs revisiting.

## Decision

**Watch roles are for any role the centroid fit cannot express — small or large.**

The 2,000-word version of ADR-0051's rationale conflated two things: *a role too rare to earn a
cluster* and *a role a cluster cannot represent*. Only the second is essential. Backend is not
rare; it is unclusterable **here**, because `role_families.json`'s own note records that k-means
split the `software-engineering` catch-all "by seniority and phrasing rather than by domain".
Twenty clusters map to that one family and not one of them means "backend". Size was never the
operative property; **inexpressibility in the current fit** is.

Fifteen roles ship: eight under `software-engineering` (Full Stack, Backend, Frontend,
DevOps / SRE (by title), QA (by title), Security (by title), Platform (by title), and the
existing Forward Deployed Engineer) and seven under `ai-ml` (AI Engineer, ML Engineer, Research
Engineer, LLM / GenAI, Research Scientist, Computer Vision, MLOps).

Two properties of ADR-0051 are unchanged and load-bearing: a watch role is an **overlay, never a
partition** (it re-counts Jobs already counted in their family), and watch rows stay **out of the
share denominator**, so adding fifteen of them cannot move any family's share.

**Four of the eight SWE roles name a concept that is *also* a top-level family** — the families
`qa-test`, `security-engineering`, `devops` and `sre-platform` all exist in `role_families.json`.
They are kept, because a reader drilling into Software Engineering wants to see QA and Security
among the SWE titles, but they are **renamed with a `-by-title` suffix** (`qa-by-title`, "QA (by
title)") rather than left to share a name. The overlap is not one-to-one in either direction:
`devops-by-title` matches SRE titles that the family map splits across `devops` *and*
`sre-platform`, and `platform-by-title` covers part of `sre-platform` too. That is the point — the
watch role is a title pattern, the family is a centroid, and they were never going to partition the
same way.

Sharing one would be the exact failure CLAUDE.md Rule 3 warns about — a near-synonym for a
different thing — because the two count **different populations**: `count_groups` matches watch
roles corpus-wide by title *before* centroid assignment, so the drill's "QA (by title)" line
includes QA jobs that the family map files under `qa-test`, `data-engineering`, or anywhere else.
The two numbers will legitimately disagree, and the name has to admit that. `WATCH_PREFIX` keeps
the ledger keys apart; it does nothing for the vocabulary a reader sees. The suffix is applied
only where a family of the same concept exists — disambiguation where there is ambiguity, not a
uniform tax on every label.

## Consequences

**Overlaps are now routine, not exceptional.** At one entry, overlap was theoretical. At fifteen it
is everywhere: over the snapshot described below, hundreds of titles match both Backend and Full
Stack, and dozens match both AI Engineer and ML Engineer, or both DevOps/SRE and Platform. A
drill's series therefore **do not sum to the family**, and must not be read as a breakdown. This is
inherent to matching titles a human wrote — "Senior Backend QA Engineer" genuinely is both — not a
defect to be normalised away.

**Precision is now the maintenance burden.** Patterns were validated by reading matched titles, not
by eyeballing counts, and two were tightened as a result: a bare `test engineer` swept in *Hardware
Test Engineer*, so QA was re-anchored on QA/SDET/quality-assurance/software-test and shed roughly a
quarter of its matches; and a `systems engineer` candidate was dropped entirely after its top match
came back *Power Systems Engineer* — electrical, not software. One known residual: `research
scientist` still admits a non-AI *Research Scientist (Hardware)*. Kept, because the series is small
and the alternative is a bidirectional co-occurrence regex that fails on *Research Scientist —
Computer Vision*.

Those comparisons were run over the local `data/jobs/tech/` snapshot, dated 2026-07-04. It is used
here only to compare two regexes against the same fixed text — a property of the patterns, not a
measurement of the index. Live figures come from the ledger; per CLAUDE.md the local copy is never
the source of truth for pipeline data, and no number in this ADR should be read as one.

**Eight per family is a real ceiling.** `app.js` sets `CHART_MAX = 8` distinct colours and
`LEGEND_MAX = 12` rows; a ninth role greys out rather than plotting. `software-engineering` is at
exactly eight, so adding one there now means removing one.

**The counting cost is linear in watchlist size.** `count_groups` tests every role's patterns
against every served row — fifteen roles instead of one, against the row count the last run
reported in its log (~282k). Regex matching on a short title is cheap and the step already pulls
the whole vector column, which dominates; if that stops holding, the fix is to compile one
alternation per parent, not to shrink the list.

**The ledger is no longer "tiny forever", and ADR-0040's sizing should be read as superseded.**
`append_ledger` writes one row per non-empty `(metric, family, band)` group. Fifteen watch roles
add up to fifteen × bands × two metrics on top of the family rows, taking a run from the 145 rows
last observed to several hundred. At the current cadence that is hundreds of thousands of rows a
year rather than the handful ADR-0040 envisaged. Two things follow. `_migrate_ledger` rewrites the
file whole, which is fine at this size but is now the thing to watch, not the appends. And the
ledger will eventually want a retention or rollup policy — none exists today, and this ADR does
not add one, because the right trigger is a measured file size rather than a guess.

**Discoverability is data-dependent, and nothing asserts it.** The marker and the drill are gated
on a family being inside `CHART_MAX` **by rank**, not merely on having watched roles. Both
`software-engineering` and `ai-ml` sit comfortably inside the top eight today, so all fifteen roles
are reachable. If either ever slipped past rank eight its roles would become unreachable with no
test going red — the gate is correct (it must not advertise a click that does nothing), but the
dependency is on live data and therefore cannot be pinned by a unit test.

**A refit does not invalidate these series.** That was ADR-0051's argument for patterns over
centroids and it still holds — with more force now, since these fifteen are exactly the roles a
future refit is most likely to reshuffle.

## Alternatives rejected

**Refit the centroids so these become real families.** The honest taxonomy fix, and rejected only
on cost: it needs a `cluster-roles.yml` refit, a new `centroid_version`, a re-curation of every
cluster in `role_families.json`, and — because series identity is `(version, family)` — a re-base
that restarts the trend history. Worth doing eventually; not worth blocking a labelling change on.

**Stay inside ADR-0051's stated scope and add only sub-1% roles.** Self-consistent and useless: it
excludes Backend, Frontend and Full Stack, which is most of what the drill exists to show.

**Derive the drill from clusters instead of a curated list.** Rejected in ADR-0051 and still right —
cluster ids are meaningless across fits, and the drill would silently re-label itself on every refit.
