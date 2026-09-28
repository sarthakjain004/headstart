# ADR-0256: Recruitee reads a real English description and keeps the primary title

**Status:** accepted · **Date:** 2026-09-28 · **Relates to:** [ADR-0017](0017-tech-role-filter.md) (the tech gate that reads the title)

## Context

A Recruitee offer's top-level `title`, `description` and `requirements` are in its primary
language. `translations` holds every language the tenant wrote, keyed by code. The search index
holds non-English text out (CLAUDE.md, English-only corpus), so a Dutch offer with an English
version never reached search: voortman's "Lead Software Developer XR" is Dutch at the top level
and English under `translations.en`.

Reading `translations.en` wholesale is wrong in two measured ways (voortman and dnata, 27 offers
with an `en` version besides another language, 2026-09-28):

- An English version can be a template of headings alone: voortman's "BBL: Logistiek" and
  "BBL: Mechatronica" are 1-2% of their Dutch text ("WHAT ARE YOU GOING TO DO? WE ASK WE OFFER").
- An English title can be a stale copy of another offer's: dnata's "Cargo Agent" carries
  "Ramp Coordinator – Schiphol"; voortman's "Service Engineer" carries "Service Monteur".

Where the top-level text is already English, `translations.en` equals it (119 of 119 offers on
40 random Boards).

## Decision

**The description (with requirements) comes from `translations.en` when it is at least half the
primary text's length; the title always stays the primary one.**

## Alternatives

- **Read the whole English version, title included.** Serves wrong titles (the two above) and
  empty templates; a wrong title misleads a user more than a Dutch one.
- **Leave the primary text.** The fix's whole point is lost: those Jobs stay out of search.
- **Pick by language detection of the primary text.** Adds a detector call per offer and still
  needs the template guard; the equality measured above makes it unnecessary.

## Consequences

- 12 of 79 voortman and 13 of 38 dnata Jobs change description to English (2026-09-28). On dnata
  3 gain a stated-years experience figure the Dutch text hid from the English patterns.
- 1 of the 27 (dnata "Cargo Agent") takes another offer's English description. Accepted: no field
  says which translation belongs to which offer, and it is 1 in 27.
- The feed shows the English description too, not only the index.
