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
— which are **large**, 1–4% of the tech corpus each, three to twenty times the size ADR-0051's
rationale contemplated. Adding them contradicts the stated premise, so the premise, not the
addition, is what needs revisiting.

## Decision

**Watch roles are for any role the centroid fit cannot express — small or large.**

The 2,000-word version of ADR-0051's rationale conflated two things: *a role too rare to earn a
cluster* and *a role a cluster cannot represent*. Only the second is essential. Backend is not
rare; it is unclusterable **here**, because `role_families.json`'s own note records that k-means
split the `software-engineering` catch-all "by seniority and phrasing rather than by domain".
Twenty clusters map to that one family and not one of them means "backend". Size was never the
operative property; **inexpressibility in the current fit** is.

Fifteen roles ship: eight under `software-engineering` (Full Stack, Backend, DevOps / SRE,
QA & Test, Frontend, Security, Platform, and the existing Forward Deployed Engineer) and seven
under `ai-ml` (AI Engineer, ML Engineer, Research Engineer, LLM / GenAI, Research Scientist,
Computer Vision, MLOps).

Two properties of ADR-0051 are unchanged and load-bearing: a watch role is an **overlay, never a
partition** (it re-counts Jobs already counted in their family), and watch rows stay **out of the
share denominator**, so adding fifteen of them cannot move any family's share.

## Consequences

**Overlaps are now routine, not exceptional.** At one entry, overlap was theoretical. At fifteen it
is measured: on a July-4 snapshot, 114 titles match both Backend and Full Stack, 59 both AI Engineer
and ML Engineer, 38 both DevOps/SRE and Platform. A drill's series therefore **do not sum to the
family**, and must not be read as a breakdown. This is inherent to matching titles a human wrote —
"Senior Backend QA Engineer" genuinely is both — not a defect to be normalised away.

**Precision is now the maintenance burden.** Patterns were validated by reading matched titles, not
by eyeballing counts, and two were tightened as a result: a bare `test engineer` swept in *Hardware
Test Engineer* (QA dropped 2,012 → 1,479 once anchored on QA/SDET/quality-assurance/software-test),
and a `systems engineer` candidate was dropped entirely after its top match came back *Power Systems
Engineer* — electrical, not software. One known residual: `research scientist` still admits a
non-AI *Research Scientist (Hardware)*. Kept, because the series is small and the alternative is a
bidirectional co-occurrence regex that fails on *Research Scientist — Computer Vision*.

**Eight per family is a real ceiling.** `app.js` sets `CHART_MAX = 8` distinct colours and
`LEGEND_MAX = 12` rows; a ninth role greys out rather than plotting. `software-engineering` is at
exactly eight, so adding one there now means removing one.

**The counting cost is linear in watchlist size.** `count_groups` tests every role's patterns
against every served row — fifteen roles × ~282k rows per run instead of one. Regex matching on a
short title is cheap and the step already pulls the whole vector column, which dominates; if that
stops holding, the fix is to compile one alternation per parent, not to shrink the list.

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
