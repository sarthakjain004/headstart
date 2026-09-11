# ADR-0132: A date rule may refuse to read a month; it may not guess one

**Status:** accepted · **Date:** 2026-09-11 · **Extends ADR-0127 (hygiene is a shared baseline), whose `dates` rule this changes for all nine Layouts at once.**

## Context

`parseMonth` is the only thing in the résumé builder that reads a date. It feeds the `dates` rule
and `reverse-chronological`, and since ADR-0127 put `dates` in the baseline, **all nine** registered
Layouts run it — so whatever it answers reaches every user of the builder, not only the ones who
picked the Headless Headhunter template.

It read a date by slicing the first three characters of the month word and asking which English
month *started* with them. That has two failure modes, both measured on the live code:

| Cell | Was read as | Actually |
|---|---|---|
| `j 2023` | January | one letter names no month |
| `ju 2023` | June, never July | two months |
| `ma 2023` | March, never May | two months |
| `enero 2023` | **no month at all** | January, in Spanish |
| `mai 2023` | **no month at all** | May, in French and German |

The first three are a rule validating a date the user never wrote. The last two are worse: the
`dates` rule reported `Start date needs a month and a year` — an **error**, the loudest level the
Checks panel has — about a date that was correct. Nothing in this project scopes a résumé's own
language; the English-only line in CLAUDE.md §Project Scope is about the *search corpus*, and a
builder aimed at companies worldwide is not entitled to assume its users write in English.

## Decision

Three verdicts for a date cell, not two.

1. **A prefix resolves a month only when it fits exactly one, and is at least three characters.**
   Ambiguity is counted in *months*, never in spellings — `mai` is May in French, German and (as
   the prefix of `maio`) Portuguese, so it is an answer; `ma` is March and May, so it is not. The
   three-character floor is the length every one of these languages' own abbreviation uses; without
   it `o 2023` resolves to October on the table alone, and a single letter is not a date.
   Refusing costs nothing a guess was paying for: `jun`, `jul`, `sept`, `mar`, `mai`, `mei`, `março`
   and `août` all still read.
2. **Seven Latin-script languages are read** (en, es, fr, de, it, pt, nl), with diacritics folded.
   Verified: no name maps to two different months, every full name resolves to itself, and every
   standard three-letter abbreviation resolves to the month it abbreviates.
3. **A month-shaped cell whose word we cannot read is a `note`, not an `error`** — with one
   exception. `Start date needs a month and a year` is a claim about a document the builder cannot
   read, and it is wrong for every Latin-script language outside the table (Polish, Swedish, Czech,
   Turkish…). The exception is a short list of English words a date cell really does carry and the
   builder *knows* are not months — the four seasons, `ongoing`, `various`, `sometime`. Those stay
   errors, because the note is the quiet level: the editor leaves a note out of the headline problem
   count, and demoting `Summer 2023` would have hidden a real fault to spare a hypothetical one.

The third verdict lives in `ResumeLayouts.dateFinding`, shared, because *what this build can read*
is not a template's opinion. A Layout keeps its own wording for the one verdict it owns — the
missing date — which is why `headless-headhunter` still says `"June 2023"` in its own voice.

## Consequences

- A misspelling (`Junuary 2023`) reads as unreadable and gets the note. Telling a typo from a
  Hungarian month needs a dictionary this does not have, and a note is the honest answer to
  "we don't know".
- A connector is still not read: `junio de 2023`, the ordinary Spanish prose form, matches neither
  the month-and-year shape nor the table, so it stays an error. If that turns out to be what people
  type, the fix is one optional group in the shape — deliberately not added on a guess.
- `reverse-chronological` now orders a non-English résumé's jobs, where before every start date read
  as `null` and the check silently skipped the whole section.
