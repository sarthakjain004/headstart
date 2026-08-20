# ADR-0076: The smallest stated experience requirement wins

- Status: Accepted
- Date: 2026-08-20
- Relates to: [ADR-0009](0009-experience-extraction.md) (the cascade this gives a policy),
  [ADR-0013](0013-experience-plausibility-guards.md) and
  [ADR-0060](0060-narrative-guards-for-the-work-word-patterns.md) (the guards that still run first,
  unchanged), [ADR-0061](0061-refreshable-metadata.md) (the `DERIVATIONS_VERSION` bump that carries
  this to rows already stored), [ADR-0072](0072-a-three-digit-number-condemns-the-whole-span.md)
  (the span rules a candidate must survive). **Suspends [ADR-0066](0066-a-recall-widening-that-cannot-change-an-existing-answer.md)'s
  rule** that no existing Tier-2 answer may change — that rule governed a *recall widening*, where a
  changed answer is collateral damage; here changing answers is the entire point. Resolves #163;
  implements items 1 and 2 of #189.

## Context

A description that states several experience requirements had no policy. `_scan` returned the
leftmost match of the first matching pattern — an accident of `re.search`, never a decision. #163
found it by trying to widen `_GAP` (how much text may sit between the number and the word
"experience") from 30 to 45 characters, which is a plain recall win: the missed class is
`N+ years <noun phrase> experience` — "3+ years of production-grade C++ and/or Rust experience" is
37 characters and answers nothing at 30, and #163 counted 1,672 occurrences of one shape of it
alone. The widening could not ship, because a wider
gap also lets a pattern anchor *earlier*, and so silently changed **which** requirement wins:
2,690 jobs moved to a higher floor, mean +5.7 years. Recall and semantics were entangled in one
constant.

The three readings, from #163: **first stated** (the status quo, by accident), **largest stated**
(defensible — a candidate must satisfy every requirement, so the binding constraint is the
maximum), **smallest stated** (the most permissive). This is not a badge question. `search.py`
filters `min_years <= your_years`, so the reading decides which jobs a candidate is shown at all.

The repo owner answered it on #163:

> the smallest one, i dont want any user to miss a job they could be qualified for.

Reading the corpus says the same thing for a reason the ask does not mention: postings state
**alternative paths** as often as they stack demands, and for those, "largest" is not strict — it is
wrong. Real rows from the description store:

```
eightfold   "10 years (Master's degree with 6 years) related experience"
eightfold   "2 years (or master's degree + 0 years) of relevant experience"
ashby       "4+ years of work/academic experience as a Machine-Learning/Deep-Learning
             researcher (2+ years, post-PhD work experience with those having a PhD degree)"
```

A PhD candidate with 10 years qualifies for the first by its own sentence; a floor of 12 hides the
job from exactly the person it invites.

The commoner shape is AND-stacked — "10+ years of software engineering experience, including 3+
years working directly on ML/AI systems" — and there the smallest reading is knowingly
over-inclusive: the true floor is 10. That asymmetry is the whole argument. Over-inclusion shows a
candidate a job they may be under-qualified for, which they can see and dismiss in one line of the
posting. Under-inclusion hides a job they *are* qualified for, which they cannot see, cannot
dismiss, and cannot fix with a filter. `CONTEXT.md` already resolves the same asymmetry the same
way for unknown experience: "unknown is deliberately not treated as too senior."

Item 1 of #189 is the same question wearing a different hat. `_CEILING_BEFORE` recognises "up to N
years" and **withdrew** the number, because reading N as `min_years` inverts a posting written for
juniors ("candidates with up to 3 years of experience" served as requiring 3). Withdrawing serves it
as stating no requirement at all, and leaves the scan hunting for a later occurrence — on one row,
the company boilerplate "more than 50 years of experience".

Measured over the ADR-0050 description store pulled fresh from HF (**339,192 descriptions, all 18
ATSes**, 2026-08-20) — this change against the merge-base, Tier 2 only:

| bucket | rows |
| --- | --- |
| answer unchanged | 174,375 |
| no answer either way | 124,563 |
| floor moves **down** | 39,208 (median −3, mean −3.7) |
| floor moves **up** | 1 |
| same floor, different ceiling | 68 |
| gains an answer where there was none | 977 (305 of them an "up to N" ceiling) |
| **loses** its answer | 0 |
| *of the movers*: a floor replaced by an "up to N" ceiling | 286 |

## Decision

**`_scan` collects every surviving match and answers with the smallest `min_years`.** It no longer
returns the first one it finds. The guards are untouched and still run per match — narrative span,
the 20-year requirement ceiling, `_is_narrative`, the ADR-0072 span rules — so this changes only
which of the *survivors* is served, never what survives. Ties keep pattern order (`min` returns the
first minimum), so ranges still beat single values at the same floor.

**The winning span keeps its own `max_years`.** A floor from one sentence carrying a ceiling from
another describes no posting anyone wrote.

**Each pattern resumes from just past the matched number, not from the end of the match.**
`finditer` walks non-overlapping matches, so at a 45-character gap a longer match swallows a
smaller requirement standing inside it — "10 years (Master's degree with 6 years) related
experience" offers only the 10, and "Age Range: 28-35 years 5-8 years' experience" offers nothing
at all, its real requirement hidden inside an age the guards then reject. That is the same
positional accident this ADR exists to remove, so the walk overlaps. Measured over the store, it
recovers a smaller floor on 117 descriptions and costs nothing: 179.8s against 180.3s, a 1.00x
ratio. Resuming past the *number* rather than one character into it is load-bearing — from one
character in, `\d{1,3}` matches "05" out of "105" and re-opens the truncation ADR-0013 closed.

**`_GAP` widens to 45.** The constant is now free to be set by recall alone, because position no
longer decides the answer. This is #189 item 2, and it is why the two issues ship together: the
widening was never a separate change, it was blocked on this policy.

**"up to N years" is read as `0`–`N`, not withdrawn.** The check moves *after* the narrative and
floor-plausibility guards rather than sitting with them, so "up to 25 years" is still refused as
narrative by the 20-year requirement ceiling; and the branch applies that same ceiling to the top
it records, because it returns before the ADR-0072 span rules further down and would otherwise be
the one path that can write an absurd `max_years` ("up to 8 to 150 years" — no description in the
store takes that shape today, so the guard is structural, not a repair). Below the ceiling, the
posting's floor is 0 and N is its top — what the sentence actually says, and an answer rather than
a fall-through to whatever "N years" the description mentions next.

Be clear about what this does **not** buy today: nothing reads `max_years`. `search.py` filters on
`min_years` only (its `max_years` parameter is the *user's* years), `/search` does not project the
column, and the UI badge renders `min_years` alone — so a junior sees the same result set either
way, because an unknown floor already passes every filter (ADR-0009). What changes now is that the
row states the truth instead of nothing, and that the scan stops hunting past it; the filtering
win #189 imagined needs a consumer for the ceiling, which is a separate change.

**`DERIVATIONS_VERSION` 1 → 2.** Without the bump this reaches newly seen jobs only, because
`embed_plan` skips ids already embedded (ADR-0061). The ~39k moved answers are the point; they have
to reach the rows already in the table.

## Rejected alternatives

- **Largest stated minimum.** The strict reading of an AND-stacked posting, and genuinely more
  faithful there. Rejected: the owner's call, and the OR-alternative rows above make it *wrong*
  rather than merely strict — it hides a job from a candidate the posting explicitly invites. It
  also aims the error in the direction the user can neither see nor undo.
- **First stated (the status quo).** Not a policy at all — it is whichever pattern in
  `_tier2_patterns` order happens to anchor leftmost, which is why widening one regex constant
  moved 2,690 answers. Keeping it means `_GAP` can never be tuned for recall again.
- **Keep withdrawing "up to N years".** Correct as far as it goes (it stops the inversion), but it
  discards a requirement the posting states plainly, and #189 measured the fall-through it leaves
  behind landing on company boilerplate.
- **Keeping the plain `finditer` walk.** Simpler by five lines, and it was what this change shipped
  with until the measurement was read: it leaves **43 rows moving up**, every one a longer match
  swallowing the smaller requirement inside it, and most of them the OR-alternative shape the
  decision is built on. Widening `_GAP` without overlapping the walk hands back, on the exact rows
  that matter most, the positional dependence the policy removes everywhere else.
- **An LLM tier that reads AND vs OR from the sentence.** The only thing that could tell an
  alternative path from a stacked demand reliably. ADR-0009 deferred that tier and #189 confirms it
  is still unbuilt; a regex cannot make the distinction, so the policy has to pick a side.

## Consequences

**39,208 answers move down, and that is a filter change, not a display change.** Every one of them
admits candidates who were previously filtered out of a job they may qualify for. Nothing is
hidden that was visible before: no row loses its answer, and a lower floor can only widen who
passes `min_years <= your_years`.

**The trends ledger's seniority bands shift junior.** `role_trends` bands rows by `min_years`
(`roles.band`), so the run after this lands will show a level shift across bands that is an
extraction change, not a market change. Read it as a step in the series, not a trend.

**Narrative false-positives are promoted.** The guards' known residue — the sentences ADR-0066
measured as costing more to guard than they save, "for 3 years running", "(4+ years at YC startup)"
in a founder bio — used to lose to an earlier, larger, real requirement. Small numbers now win by
construction, so where the residue survives the guards it becomes the answer. Read in a sample of
25 changed rows, 2 were of this kind. Widening the idiom list is still the wrong fix (ADR-0066
measured it costing real requirements); this is the LLM tier's work.

**A ceiling outvotes a stated floor, and 286 rows take that path.** A ceiling span's floor is 0,
which is the global minimum, so wherever a description carries both an "up to N" sentence and a
real requirement, the ceiling wins. Measured against the merge-base, 286 descriptions that answered
a floor above 0 now answer 0–N — mostly privacy boilerplate against a real requirement ("Your data
is kept for up to 2 years in our candidate pool" beating a stated 3), and occasionally a genuine
pair ("4-6 years … up to 5-10 years for candidates with the right posture" → 0–10). This is the
policy applied literally, and it errs in the safe direction on the column anything reads: the row
is admitted to more searches, never fewer. What it damages is the `max_years` no consumer has yet,
and a UI badge that now reads "0+ yrs" where it read "3+ yrs" — the tier is right, the number is
the most permissive reading of a description that says both things. If `max_years` ever gains a
consumer, revisit this: the fix would be to let a ceiling span answer only when no stated floor was
found, which is a different policy for a different pair of costs.

**Tier 2 is ~1.24x slower** (144.4s → 179.1s over 339,192 descriptions, single-threaded), because a
scan can no longer stop at its first hit. It runs inside `update_meta`/`embed_plan`, both far from
the pipeline's binding cost, so this buys the policy cheaply. The overlapping walk is not what
costs it — measured on its own, that was free.

**One answer moves up, and it is the pass ordering, not the policy.** A greenhouse row reads 1
today, from the spelled-out-numbers pass; the wider gap now lets the digits pass answer ("Over the
last 20+ years, you have gained demonstrated experience in planning and leading Systems Engineering
efforts"), and ADR-0066 runs the words pass only when the digits pass finds nothing. The two passes
are not pooled before the minimum is taken, by that ADR's design. One row in 339,192 does not
justify reopening it.
