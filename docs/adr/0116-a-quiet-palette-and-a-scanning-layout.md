# ADR-0116: A quiet palette, and a layout built for scanning

**Status:** accepted · **Date:** 2026-09-07 · **Replaces the visual language ADR-0042 shipped; keeps every decision ADR-0112 and ADR-0113 made about what the product says**

## Context

The UI ADR-0042 built had a consistent register: near-black navy ground, a neon aqua→violet
gradient on the wordmark and the Search button, glow shadows, and a slowly drifting decorative
aura behind the header. Four hues competed on every result card (amber "new", lime "pays", violet
"remote", aqua match). Navigation was a 184px sidebar; filters were a 260px rail beside the
results.

Two things made that worth revisiting.

**The register contradicts what the product now says about itself.** ADR-0112 put a door in front
of the app that refuses to state a number it cannot count, and ADR-0113 added a tab that publishes
the index's own coverage gaps and its worst eviction case. Neon-on-near-black is the visual
language of AI and crypto launch pages — it promises hype while the copy promises honesty.

**The user is not in a launch-page state of mind.** Job searching is a long, repetitive,
high-rejection scanning task, usually done tired, often in stolen moments, sometimes while
employed and anxious about being seen looking. The loop is: read title, company, location, pay,
decide in about two seconds, fifty times. High-chroma surfaces and glowing accents raise arousal
and eye-fatigue across a session of that length, and a badge on every row is noise rather than
signal.

## Decision

### The palette is "quiet dossier": warm paper, warm charcoal, one accent

Light `--ground:#EAE6DC` on `--raise:#FFFFFF`; dark `--ground:#131211` on `--raise:#2A2724`.
One accent (`#1F5F5B` light, `#5FBFB0` dark) replacing the aqua/violet pair. No gradients, no glow
shadows, no animated aura. `--aqua` is renamed `--accent`, because it no longer names its own
colour and a stale name is a defect.

Hue is spent on exceptions only. The card's four hues become two — amber for "arrived in the last
48 hours", violet for "remote" — and provenance drops its accent dot for plain text. Pay is not a
hue at all: it gets its own column and its own weight, because it is the field the decision turns
on and it had been rendered the same size as "3d ago".

**Rejected: a professional-blue institutional palette.** The generated design system recommended it
and it is the safe answer. It also looks like every B2B SaaS product and, specifically, like
LinkedIn — the thing this product defines itself against.

**Rejected: keeping the dark identity and only de-neoning it.** The smallest change, and it would
have preserved brand recognition. It was not chosen because it answers the register problem
without answering the fatigue one.

### The layout puts navigation on top and filters above the results

The 184px sidebar becomes a horizontal tab strip; the filter rail becomes a full-width panel above
the results, collapsed by default, with the applied-filter count on its trigger. The rail was
**moved, not rebuilt** — every control keeps its id, so the filter, facet and saved-set JavaScript
is untouched.

**This was the user's call over a recommendation to leave the desktop grid alone**, and the
measurements behind that recommendation are recorded here because they turned out to matter. Before
the change, results already had 60% of the width (786px of 1320) with five cards above the fold on
a laptop; the genuinely broken case was a phone, where 625px of an 844px viewport was chrome before
the first job. The restructure was chosen anyway, and the width it freed then had to be *earned*
rather than merely occupied — see below.

### Full width is spent on alignment, not on stretching

The first attempt simply let the card fill 1276px, which bought whitespace rather than density:
content crammed left, the match ring stranded at the far right, ~840px of dead air per row, fifty
rows deep.

The card is now a grid whose text column is bounded and whose data columns are fixed, so **pay and
match land at the same x on every row**. That is the only thing full width is actually worth for a
scanning task: the eye runs down one column instead of re-reading each row left to right. The
bound follows the skill database's own rule ("limit to 65-75 characters per line"), and the column
was sized against measured content — the widest row used 344px of a 694px column, leaving a
389-474px gap.

## Consequences

- **The trends series palette was re-measured against the new surfaces, not assumed.** Worst
  series-vs-surface contrast moves 3.46 → 3.36 (dark) and 2.17 → 2.13 (light), so the ground change
  is not what decides it. **The light figure is under the 3:1 floor and was before this change** —
  a pre-existing gap the CSS comment's "six-checks PASS" did not cover. Recorded here rather than
  inherited silently; fixing it is a separate change to the series colours themselves.
- **Every surface change must be re-checked in both themes.** Lifting a surface silently pushed
  `--ink-3` under 4.5:1 twice during this work — once in dark, once in light after dark had been
  "fixed". Both were caught by measurement and neither by looking.
- Renaming `--aqua` touched 28 declarations plus the door's inline copy. The door carries its own
  token block because the sign-in wall gates `/static`, so the palette exists in two places by
  necessity; they must move together.
- The design was reviewed by an adversarial critique loop that screenshots the running UI and rates
  it, with a separate agent applying the fixes. It scored 5 → 6.5 → 6 → 5 across four rounds: the
  dips are rounds catching that a previous fix had not worked or had cost something (a click
  overlay that killed every tooltip in the card; a width cap that only constrained one tab and
  moved the void to another). Recorded because the pattern — a fix that reads correct and fails on
  contact — is the same one CLAUDE.md's measure-don't-reason rule exists for.
