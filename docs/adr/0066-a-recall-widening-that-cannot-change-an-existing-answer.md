# ADR-0066: A recall widening that cannot change an existing answer

- Status: Accepted
- **Amended by:** [ADR-0072](0072-a-three-digit-number-condemns-the-whole-span.md) — one Tier-2 answer is withdrawn that was *faithful*, not wrong, so it falls outside the exception below; enumerated there
- **Suspended by:** [ADR-0076](0076-smallest-stated-experience-requirement-wins.md) — the rule below governs a *recall widening*, where a changed answer is collateral damage; ADR-0076 is a policy change, so changing answers (39,208 of them, all downward) is its point. The rule still binds any future widening
- Date: 2026-08-19
- Extends [ADR-0009](0009-experience-extraction.md) and [ADR-0018](0018-experience-seniority-fallback.md)
- **Amends [ADR-0060](0060-narrative-guards-for-the-work-word-patterns.md)** on three points: how
  the guard set is selected, its rejected alternative "guard every pattern uniformly", and the
  restriction of the 20-year requirement ceiling to the work-word patterns
- **Amends [ADR-0018](0018-experience-seniority-fallback.md)**: Tier 2 no longer requires a work
  token after the connector

## Context

ADR-0009 deferred recall work with a one-line recipe: *"widening regex recall is appending to
`_DESC_PATTERNS`"*. That held while every pattern was anchored on the literal word `experience`.
It stopped holding here, and the shape of the measurement is why.

Measured against the served LanceDB table joined to the ADR-0050 description store — **281,428
rows, all 18 ATSes**, pulled fresh from HF — the cascade covered **83.26%**. Partitioning the
122,880 Tier-2 misses over the full 328,930-description store showed where the remainder actually
lives:

| miss class | count | share of misses |
| --- | --- | --- |
| a number sits beside "years" — the only addressable class | 14,797 | 12.0% |
| says "experience", states **no number anywhere** | 78,369 | 63.8% |
| carries neither "years" nor "experience" | 25,909 | 21.1% |
| stub, empty, or "years" with no number | 3,805 | 3.1% |

So the Tier-2 hard ceiling is **67.1%** against 62.6% then achieved: even solving every addressable
miss perfectly leaves a third of the corpus with nothing to extract, because the employer never
wrote a number. Recall work here is a small, bounded prize, and the risk it carries — silently
changing an answer that was already right — is unbounded. That asymmetry, not the size of the gain,
is what dictates the design.

Three specific things made "just append a pattern" unsafe:

1. **Typographic punctuation blocks matches character-by-character.** The non-breaking hyphen
   U+2011 alone defeats 209 otherwise-matchable spans ("8+ years of hands‑on … experience", where
   `_WORK` already carries `hands[\s-]?on`); en/em dashes another 46. Threading six code points
   through `_GAP`, `_WORDS`, `_WORK` and `_RANGE_TAIL` means re-deriving the risk in each.
2. **Spelled-out numbers need a wider alternation**, and `re` builds no trie from one, so paying
   for it on every description rather than only the ~38% that miss measured **6.5x slower end to
   end** (0.31s → 2.02s per 3,000 descriptions).
3. **The vocabulary anchor cannot be completed.** `_WORK` enumerates the domains someone thought
   of; the misses are a long tail no list closes — "3+ years in product marketing", "7+ years in
   hardware quality", "5+ years in system and network administration".

## Decision

Widen all three tiers, under one rule: **a change may add a reading where there was none, and may
replace a Tier-3 guess with a stated number, but may not change a value Tier 2 already produced.**

- **Fold before matching, via a 1:1 `str.maketrans`.** Every mapping is one character to one
  character, so offsets survive exactly and the narrative guards — which slice `text` around
  `match.start()` — keep pointing at what they did before. Downstream patterns may therefore
  assume ASCII punctuation.
- **Run spelled-out numbers as a second pass**, tried only when the digits pass finds nothing, so
  the alternation cost is paid only on misses. The *patterns* cannot change a digit answer, because
  they never run when one exists. One shared piece does move a digit answer, deliberately:
  `_RANGE_TAIL` now recognises a spelled-out floor, so `"three to 5 years of experience"` reads
  3–5 where it previously read 5. That is the ceiling-as-floor bug ADR-0009 already calls out,
  fixed for one more spelling — not a new class of change.
- **Carry the narrative-guard flag beside each pattern** rather than recovering it afterwards.
- **Guard on the idiom, not only on the preceding words.** `_NARRATIVE_SPAN` rejects fixed phrases
  where "N years" is never a requirement however the sentence reads, for every pattern.

Result: **83.26% → 84.55%, +3,637 rows.** Eight rows lose a value, and all eight are the `up to`
guard withdrawing a *wrong* one — see below.

## Consequences

**ADR-0060 is amended on how the guard set is selected.** It required the set be *"derived from
the pattern text, never hardcoded"*, because a literal index set would re-bind when a range
phrasing was inserted. The derivation was `_WORK in pattern.pattern` — sound only while the
work-word patterns were the sole ones matching without the literal word "experience". Three of the
patterns added here are built from something else entirely, and the sniff reported `False` for all
of them, silently shipping them unguarded. The flag now travels beside its pattern in
`_desc_patterns`, which keeps ADR-0060's actual requirement — the binding cannot drift when the
list is reordered — while surviving a pattern that does not mention `_WORK`. Its test moved with
it, from asserting the derivation to asserting the behaviour: an unguarded pattern must be unable
to fire on narrative at all.

**ADR-0060 is also amended on its rejected alternative.** It declined to *"guard every pattern
uniformly"* on the premise that *"the experience-anchored patterns cannot reach narrative (the
literal word 'experience' has to be nearby)"*. That premise does not hold, and the decisive case is
`_CEILING_BEFORE`: `"Candidates with up to 3 years of experience"` fires an *anchored* pattern and
was served as `min_years=3` — a posting addressed to juniors, hidden from every junior. The anchor
word being present says nothing about whether the number is a requirement.

`_NARRATIVE_SPAN` and `_CEILING_BEFORE` therefore apply to **every** pattern, guarded or not.

**So does the 20-year requirement ceiling, which is the third amendment to ADR-0060.** That ADR
restricted `_MAX_PLAUSIBLE_REQUIREMENT` to the work-word patterns because *"'25 years of
experience' is a real, if rare, requirement"*. Measured, it is not rare — it is nonexistent. 1,066
descriptions in the store receive a Tier-2 answer above 20 years, and hand-reading the top 30 found
no real requirement among them: *"PayPal has been revolutionizing commerce globally for more than
25 years"*, *"a federal contractor with more than 30 years of experience providing environmental,
construction, facility management"*, *"the founding team brings over 30 years of cybersecurity
experience"* — and two that are not tenures at all, *"a 21 year old UMich grad"* and *"Age Limit:
Below 26 Years"*. The anchor word says nothing about genre; the magnitude does. This is the largest
single correction in the change, and it is a pre-existing defect the review surfaced rather than
one this work introduced.

`_NARRATIVE_BEFORE` and `_NARRATIVE_AFTER` do stay guarded-pattern-only, as ADR-0060 decided: they
key on surrounding words rather than magnitude, and an anchored pattern genuinely cannot reach the
founder-tenure phrasings they target.

This is a partial fix, not a complete one. Anchored patterns can reach narrative by routes no fixed
idiom catches — `"we pair eight years of cloud-operating experience with one of the world's largest
infrastructure capital bases"` still reads as 8, and this change introduces it by teaching Tier 2
spelled-out numbers. Recognising that class needs sentence-level judgement about genre, which is
what the deferred LLM tier is for; enumerating more idioms would repeat the `running` mistake below.

**ADR-0018's work-token anchor is dropped for one pattern.** ADR-0018 specified `N years of/in/as
<work word>`, *"anchored to `of/in/as` + a work token so `per year` / `N years ago` don't match"*.
`_WORK` can only enumerate domains someone thought of, and the misses are a long tail no list
closes. One pattern therefore keeps the `in` connector but drops the work token. Measured over the
description store it decides the answer for **1,049 descriptions, 1,028 of which had no answer at
all**; hand-reading a 21-sample put the false-positive rate at 2 — a data-retention notice ("kept
for up to 2 years in our candidate pool") and a fixed-term contract ("a training position of up to
three years in duration"). Both are the same shape, so `up to` joins `_NARRATIVE_BEFORE`: it states
a *ceiling*, and reading a ceiling as `min_years` is wrong regardless of which pattern found it.

That guard turned out to matter more than the pattern that prompted it. It withdraws a value from
eight rows, and every one was inverted: `"Fresh graduates and candidates with up to 3 years of
relevant mobile development experience are welcome to apply"` was serving `min_years=3`, hiding a
junior-friendly posting from every candidate with less than three years — the exact population it
is addressed to. Another read `"up to 15 years of security and maintenance commitments for Long
Term Support releases"` as a fifteen-year requirement. Withdrawing the number is a partial fix:
the faithful reading is `min_years=0, max_years=3`, which needs a ceiling-aware pattern rather than
a guard, and is left as follow-up.

A residue remains. `"For over 20 years in India, we have served customers"` still reads as 20.
Restricting the character after `in` to lowercase would reject it — and was tried — but under
`re.IGNORECASE` that class matches capitals anyway, and making it case-sensitive costs the acronym
domains that are far more common than country names: "8+ years in CPU verification", "7-10 years in
BCP/DR", "3+ years in GRC". The Title-Case/acronym split does not separate them either, because
"3+ years in Salesforce consulting" is Title Case and real. The residue is accepted and recorded
rather than guarded badly; ADR-0060 made the same call at a measured 2.7%.

**An idiom earns a guard only if it is unambiguous.** `N years running` and `N years in business`
were tried and reverted: measured, they cost 4 and 6 real requirements ("4+ years running
distributed systems at scale", "3+ years in business development") to buy roughly two narrative
rejections each. A phrase that is *usually* narrative is a net loss, because the requirement
reading is the one a candidate filters on. Both carry regression tests pinning them as unguarded.

**`_GAP` stays at 30.** Widening it to 45 gains the "N+ years <noun phrase> experience" class, but
`search` returns the leftmost match, so a wider gap also changes *which* requirement wins where a
posting states several — 2,690 jobs, mean +5.7 years. That is a semantic question about
multi-requirement postings (is the floor the first stated, or the largest?), not a recall question,
and is left to its own decision.

**The standing coverage check cannot see fresh data.** `scripts/enrich/experience_coverage.py`
reads `data/jobs/tech/`, which is ephemeral stage output that never reaches HF, so it reports on
whatever the local machine last scraped. It remains the right quick gauge, but a coverage claim
should be measured against the served table joined to `data/descriptions/` — both of which do have
a durable source.

**Coverage is bounded by the postings, not the extractor.** Of the rows still uncovered, 76.1%
carry neither a number in the description nor a seniority word in the title. Further Tier-2 regex
work has little left to reach; the honest remaining levers are a Tier-3 vocabulary judgement about
`manager`-class titles, and removing the ~4,673 non-tech rows that can never carry a requirement
at all ([#186](https://github.com/sarthakjain004/headstart/issues/186)).
