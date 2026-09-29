# ADR-0350: Experience widenings run last, and a ceiling, an education, an age or a window is not a floor

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0066](0066-a-recall-widening-that-cannot-change-an-existing-answer.md) (the rule holds for the
widenings; the guards below are corrections and change answers, enumerated in *Consequences*) ·
**Relates to:** [ADR-0009](0009-experience-extraction.md),
[ADR-0072](0072-a-three-digit-number-condemns-the-whole-span.md) (an "up to N" is a 0..N span),
[ADR-0079](0079-smallest-stated-experience-requirement-wins.md) (**unchanged**: the smallest stated floor
still wins), [ADR-0061](0061-refreshable-metadata.md) (`DERIVATIONS_VERSION` 25 carries this to stored rows),
[ADR-0337](0337-a-derived-field-reads-no-company-history-and-says-what-it-annualised.md)

## Context

The 2026-09-29 audit of the served table (findings DF-04 and DF-05) found `experience.from_description` wrong in
two directions at once. It **missed** a requirement written as "Two (2) years of experience", or with a filler
of 46-80 characters between the years and "experience", or as "5+ years of expertise" / "Exp: 6-12 Yrs". And it
**read as a floor** a number that is not one: the tail of "$160,000 yearly", the fraction of "2.5 years", a
ceiling ("less than 2 years", "maximum two years"), the length of a degree ("4 year degree"), an age ("18 years
of age"), a contract ("1 year contract"), a residency window ("resident in the UK for the past 5 years") and the
years that stand in for a degree ("an additional 4 years of experience may be substituted for a bachelor's").
A deep-dive built and measured a patch for both (local, uncommitted); the owner decided on 2026-09-29 to ship it
and to keep ADR-0079's smallest-floor rule exactly as it is, so the DF-02 selection change (which floor to
prefer among several) is **not** part of this decision.

I re-measured it rather than trusting that run. The table is the served `jobs` table at **version 326, 499,841
rows** (497,733 with a description), streamed off HF on 2026-09-29 at 16:57 UTC, the freshest there was: seven
hours newer than the v18 copy the audit used. Main's `extract()` reproduces the served `min_years`, `max_years`
and `experience_source` on **all 497,733** rows, so every difference below is this change's and none is a
sweep still owed from an earlier bump.

## Decision

**The recall widenings are a third pass, tried only where the first two passes found nothing (ADR-0066).**
Digits only, on text where "five (5)", "5 (five)" and "Six (6)+" are first collapsed to their digits, padded
with spaces to the original length so every offset and guard window is unchanged (a pair that disagrees, "ten
(5)", is left as written). Three widenings: the collapse itself; a forward-only pattern with a gap of 46-80
characters and no full stop in it, guarded like the other work-word patterns; and `expertise` / `exp` for
`experience`. A degree-substitution guard read **before** the number ("a master's degree can be substituted
for two (2) years") runs in this pass only, since there is no other answer to protect and an "N years"
beside a substitution is at least as often the requirement in the first two.

**The wrong reads are guards in `_scan`, so they apply to every pass.**
- *A number is a whole token.* A match directly after a digit, or a digit and a point or comma, is skipped
  ("160,000 yearly", "7.000 year"). A fraction is consumed, so "2.5 years" is 2 and a range's ceiling rounds
  up ("1.5 - 3.5 years" is 1-4), the way `from_field` treats months.
- *A ceiling word is a ceiling.* "less than", "fewer than", "under", "below", "maximum (of)", "no more than",
  "at most", "upto" and "not exceeding" join "up to" and read as a 0..N span (ADR-0072), unless a floor opens
  the same sentence ("minimum of 6 years ... a maximum of 10") or the sentence turns the ceiling away
  ("candidates with less than 5 years are not eligible": the 5 is the floor). "no less than" and "not less
  than" are floors. A range keeps its label ("Maximum 8-12 years" is 8-12).
- *An education, an age and a contract length are not experience.* `_NARRATIVE_SPAN` gains "4 year degree" /
  diploma (singular "year" only, so "3 Years Diploma" as the next list item stays a requirement), "2 years of
  post-secondary study", "18 years of age / old / or older" and "1 year contract / fixed term". "or above" is
  not among them: "3 years or above" is a floor.
- *Years that stand in for a degree* ("may be substituted for a bachelor's degree") are skipped, read from the
  number to the verb with no comma, bracket, "or" or "and" between.
- *A window of time* widens to "for the past 5 years", "3 out of the past 5", "within last 3 years" and a range
  ("in the last 1-2 years").

## Consequences

**Measured on v326, over the 497,733 rows with a description: 11,212 change (2.25%).**

| old tier | field | regex | seniority | none |
| --- | --- | --- | --- | --- |
| field (33,069) | 33,069 | 0 | 0 | 0 |
| regex (304,405) | 0 | 304,009 (1,385 of them with another value) | 229 | 167 |
| seniority (87,671) | 0 | 5,005 | 82,666 | 0 |
| none (72,588) | 0 | 4,426 | 0 | 68,162 |

- **Gained 9,431:** 4,426 from unknown, 5,005 from a title estimate, of which 2,123 move the estimate by three
  years or more. By widening: "five (5) years" 8,083, the 46-80 gap 952, "expertise"/"exp" 396.
- **Lost 396:** a wrong read is withdrawn and nothing replaces it (167 to unknown, 229 back to the title
  estimate).
- **Same tier 1,385** (regex to regex): 752 floors fall, 609 rise, 24 change only the ceiling, 651 move by
  three years or more. Bucketed by the text around the old match: 392 degree substitution, 328 ceiling word, 253
  decimal or thousands, 240 education, 72 window, 54 age, 16 contract, and 426 that no single rule names
  (mostly a second number that now wins under ADR-0079's smallest floor).

**ADR-0066's rule holds for the widenings, and the ablation proves it.** With the same module and the third pass
switched off, the pass changes 9,518 rows, **all** none/seniority to regex, and **0** where the first two passes
still answer. Of the 9,518, 88 had a main answer that a guard above had first withdrawn (they are among the 1,385
same-tier rows); the guards alone change 1,793 rows and give one row an answer. So the answers that move are
moved by the corrections, on purpose, which is why this ADR amends ADR-0066 instead of hiding the change in it.

**Precision, read by hand with the text in front of me** (25 random rows per class, drawn from the 11,212):

| class | right | notes |
| --- | --- | --- |
| "five (5) years" (8,083) | 23 | 1 wrong ("Experience may substitute for education up to four (4) years" gave 0-4), 1 partial |
| 46-80 gap (952) | 25 | |
| expertise / exp (396) | 22 | 2 company history ("more than 15 years of expertise"), 1 partial ("From 5 up to 10" read 0-10) |
| degree substitution (392) | 22 of 24 | the old junk floor replaced by the stated one; 2 debatable |
| ceiling word (328) | 20 | 1 wrong ("not for those with less than 2+ years" read 0-2); 4 follow ADR-0079 (a ceiling clause beside a floor) |
| decimal / thousands (253) | 24 | the 25th ("3.5 year training contract") was wrong before and still is |
| education (240) | 24 | "university-level teaching experience" lost (the value is the same, from the title) |
| window (72), age (54), contract (16) | 12 of 12 each | first 12 of the 25 drawn read |
| no single rule (426) | 17 | 7 wrong, 5 of them a ceiling clause beside a real floor (2 of the 7 were wrong before too); 1 debatable |

**Two residues, measured and left.** (1) A ceiling clause can beat a floor stated elsewhere, because a 0..N span
has floor 0 and ADR-0079 takes the smallest: 439 rows fall to 0 from above, 385 of them on a ceiling read, and in
97 the old floor sat somewhere else in the text ("Minimum of 5 years' experience. Exceptions can be made for less
than 5 years", "We will not consider profiles with less than 3 years", Intel's sponsorship boilerplate "less than
three years' experience"). About 12 of the 97 carry a negation or an exception within 160 characters before the
number; only the negation after the number is guarded. (2) "expertise" reads company history on the same
terms as the digit patterns: 39 of its 396 gains are 15 years or more (measured 2 of 25 wrong on the read). Both
are small (97 rows is 0.02% of the served table, 39 is 0.008%); a guard that turns a ceiling away on a negation *before* it, and one for a
company's own years of expertise, are the follow-ups, each to be measured before it lands.

**Entry level** (`min_years` unknown or at most 0) falls from 102,871 to 98,845 rows: stated 7,837 to 8,323,
title estimate 21,484 to 21,231, unknown 73,550 to 69,291. At most 2 years 177,674 to 175,858; at most 5
388,516 to 387,144; at most 10 489,694 to 489,134. Every drop is a Job that now states a floor, or a ceiling
that used to read as a floor now reading as one.

**A one-time rewrite.** `DERIVATIONS_VERSION` moves to 25, and the sweep rewrites the 11,212 rows whose served
value differs: about 65 MB at the 5.9 KB a row that ADR-0340 measured (a whole row, vector included), and at most 87 MB
at the 7.9 KB a row the served table's data files average (4,058 MB over 499,841 rows, dead space included). A run in which
nothing else changed writes nothing after it. It stacks with any other derivation change that ships in the same
merge, and each of those is its own rewrite.

**Runtime.** `extract()` costs 1.25x on 3,000 served descriptions (2.47 s to 3.09 s, best of five alternating
runs). The pipeline derives only new and changed rows, so the cost is the sweep above and a few percent of a run.

## Alternatives

Measured in the deep-dive that preceded this change (local, not committed):

**Read the widenings as first-pass patterns.** "five (5)" first moved 1,005 existing answers (772 downwards,
mostly wrong: a template's "Four (4) years of additional experience ... may be substituted" beat the requirement
under the smallest-floor rule), and the long gap first moved 557. As a last resort they move none, which is the
ADR-0066 rule kept.

**Let a ceiling yield to any floor in the description.** It breaks a multi-level posting ("Junior: up to 10
years / Senior: min 15" served 0-10, would become 15). Rejected for the guard that a ceiling word closing a range
the sentence opened is not a span of its own.

**A cue rule for company history at 15-20 years.** It changed 539 rows and about half of what it removed were real
requirements.

**The DF-02 selection change** (which floor to prefer among several: first, largest, or a ladder-aware hybrid).
Not built into this decision: ADR-0079 quotes the owner's "the smallest one, I don't want any user to miss a job
they could be qualified for", and the owner kept it on 2026-09-29.
