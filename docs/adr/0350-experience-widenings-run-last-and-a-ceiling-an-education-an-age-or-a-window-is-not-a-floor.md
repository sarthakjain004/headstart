# ADR-0350: Experience widenings run last, and a ceiling, an education, an age or a window is not a floor

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0066](0066-a-recall-widening-that-cannot-change-an-existing-answer.md) (the rule holds for the
widenings it names; the wrong-read guards that shipped with them are corrections and move answers,
enumerated in *Consequences*) · **Relates to:** [ADR-0009](0009-experience-extraction.md),
[ADR-0072](0072-a-three-digit-number-condemns-the-whole-span.md) (an "up to N" is a 0..N span),
[ADR-0079](0079-smallest-stated-experience-requirement-wins.md) (**unchanged**: the smallest stated floor
still wins; ADR-0357 re-affirmed it), [ADR-0061](0061-refreshable-metadata.md) (`DERIVATIONS_VERSION` 27
carries this to stored rows), [ADR-0337](0337-a-derived-field-reads-no-company-history-and-says-what-it-annualised.md),
[ADR-0340](0340-the-employment-type-flags-read-the-title-and-more-raw-values.md) (the 5.9 KB a row),
[ADR-0357](0357-the-owner-keeps-the-smallest-stated-experience-and-get-job-names-the-others.md) (`stated_floors`
and Netflix's ladder, which this change was merged against)

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

The combined review of the first version of this change found the rule it then applied too loose in both
directions, and the principle it judged by is ADR-0079's own: **a false floor hides a job from someone who
qualifies, and an unknown stays visible.** Five guards dropped a real requirement ("Experience with Python for
the last 3 years is required", "3+ years of study design and data analysis experience", "2 years of
college-level Java programming"); the long gap read benefits and company prose as a requirement ("5 years of paid
parental leave benefits, flexible schedules, and an amazing employee experience"); a ceiling clause in another
sentence dropped a stated floor to 0; and the substitution guard, which runs in every pass, had never been
measured on its own. So the rule for each class became: **ship it only where 40 fresh hand-read changed rows are
at least 90% right; otherwise leave main's answer.** Every class met it after the fixes below, and two parts of
the widenings were cut to meet it (reversed "expertise" and a company's own 15+ years of expertise).

The table is the served `jobs` table at **version 44, 496,739 rows** (494,572 with a description), streamed off
HF on 2026-09-30 at 11:14 UTC, the freshest there was. Main's `extract()` (unchanged since ADR-0357) reproduces
the served `min_years`, `max_years` and `experience_source` on **all 494,572**, so every difference below is this
change's and none is a sweep still owed from an earlier bump.

## Decision

**The recall widenings are a third pass, tried only where the first two passes found nothing (ADR-0066).**
Digits only, on text where "five (5)", "5 (five)" and "Six (6)+" are first collapsed to their digits, padded
with spaces to the original length so every offset and guard window is unchanged (a pair that disagrees, "ten
(5)", is left as written). Five parts, each held to what a hand read showed it reads right:

- *The collapse itself*, and every third-pass pattern is guarded like the work-word patterns.
- *A forward-only gap of 46-80 characters* with no full stop in it, **screened** for first-person words, pay,
  leave, benefits, tenure, culture, "employee experience", a company's "For over 15 years, ArcTouch has created",
  and years of education, schooling or training ("1-2 years of education or training ... or equivalent work
  experience", "4 years of total combined higher education and related work experience").
- *"expertise" and "exp" for "experience"*, forward only ("With expertise of 20 years we serve customers" is
  how a company describes itself, so the reversed pattern takes "experience" and "exp" alone) and below 15 years
  when the anchor is "expertise" ("Exp: 15+ Years" is a requirement; "more than 15 years of expertise" is not).
- *A number the posting calls preferred, desired, preferable or "even better" is left unread* in this pass
  ("Preferred Qualifications - 2+ years of ...", "5+ years heavy industrial experience preferred"). The first two
  passes keep reading such a number as they always did, which is ADR-0066's rule, and is a residue below.
- A degree-substitution guard read **before** the number ("a master's degree can be substituted for two (2)
  years", "is equal to", within 60 characters) runs in this pass only, since there is no other answer to protect.

**The wrong reads are guards in `_stated`, so they apply to every pass.**

- *A number is a whole token.* A match directly after a digit, a digit and a point or comma, or a digit, a space,
  a digit and a slash is skipped ("160,000 yearly", "7.000 year", the 2 of "3 1/2 years"). A fraction is consumed,
  so "2.5 years" is 2 and a range's ceiling rounds up ("1.5 - 3.5 years" is 1-4), the way `from_field` treats
  months. "5/7 years" (a range with a slash) is left as main reads it.
- *A ceiling word is a ceiling only where no floor is stated.* "less than", "fewer than", "under", "below",
  "maximum (of)", "no more than", "at most", "upto" and "not exceeding" join "up to" as a 0..N span (ADR-0072)
  **only when the pass states no floor at all**. Where it does, the clause keeps the reading main gave its
  number, a plain floor N, so the answer never rises above main's ("Minimum of 5 years' experience. Exceptions can
  be made for less than 5 years" is 5; "8+ years of experience. Below 5 years of experience need not apply" is
  5, as on main; "BS and 3 to 5 years ... or MS and less than 2 years" is 2). A clause that closes a range a floor
  opened in the same clause ("minimum of 6 years ... with a maximum of 10") is no 0..N span, and a comma ends that
  reach. "no less than" and "not less than" are floors; "candidates with less than 5 years are not eligible" is
  the floor 5; a range keeps its label ("Maximum 8-12 years").
- *An education, an age and a contract length are not experience, read as shapes and not as words.*
  `_NARRATIVE_SPAN` holds "4 year degree" / diploma (singular "year" only), "2 years of (related) college
  education / coursework / study / program" (an education noun that **ends** the phrase: "2 years of college-level
  Java programming", "3+ years of study design" and "5 years of university-level teaching experience" are
  requirements), "18 years of age / old / or older" and "1 year contract / fixed term" (a hyphen makes a compound:
  "3 year contract-management system experience" is a requirement).
- *Years that stand in for a degree* ("may be substituted for a bachelor's degree") are skipped only where the
  clause belongs to the number: read from the number to the verb with no comma, bracket, "or" or "and", no degree
  word and at most one "experience" between them. A description flattened to one line has no full stops, and "Master's
  Degree with 3 years of related experience Equivalent experience can be substituted for the degree" states the 3.
  This guard changes which numbers are read, never which floor wins: ADR-0079 still takes the smallest.
- *A window of time* widens to "3 out of the past 5", "within last 3 years" and a range ("in the last 1-2
  years"). "for the past 5 years" and "lived in the UK for 10 years" are windows only after a residency or
  record cue (resident, lived, citizen, violation, conviction); elsewhere "for the past 4 years in backend
  development with Go" is a requirement. "paid over 2.5 years" and "spread over" join the narrative words.

The three-pass chain is written once (`_passes`), and `from_description` and `stated_floors` (ADR-0357) both walk
it, so the floors `get_job` names come from the pass that served the number. `_NARRATIVE_SPAN` keeps its name:
ADR-0066 and ADR-0337 cite it by that name, its own comment states the wider idioms, and a rename would rewrite
two records of why it exists.

## Consequences

**Measured on v44, over the 494,572 rows with a description: 10,582 change (2.14%).**

| old tier | field | regex | seniority | none |
| --- | --- | --- | --- | --- |
| field (33,545) | 33,545 | 0 | 0 | 0 |
| regex (302,256) | 0 | 301,893 (1,194 of them with another value) | 207 | 156 |
| seniority (86,805) | 0 | 4,827 | 81,978 | 0 |
| none (71,966) | 0 | 4,198 | 0 | 67,768 |

- **Gained 9,025:** 4,198 from unknown, 4,827 from a title estimate, of which 2,054 move the estimate by three
  years or more. By widening: "five (5) years" 7,880, the 46-80 gap 826, "expertise"/"exp" 319.
- **Lost 363:** a wrong read is withdrawn and nothing replaces it (156 to unknown, 207 back to the title estimate).
- **Same tier 1,194** (regex to regex): 597 floors fall, 575 rise, 22 change only the ceiling, 600 move by three
  years or more. The 1,557 rows that lose or change a value (these 1,194 and the 363 lost), bucketed by the text
  around the old match: 353 degree substitution, 287 ceiling word, 252 decimal or thousands, 222 education, 57
  window, 54 age, 12 contract, and 320 that no single rule names (a second number that now sets the floor, or a
  guard that withdrew one of several reads).

**ADR-0066's rule holds for the widenings, and the ablation proves it.** With the same module and the third pass
switched off, the pass changes 9,109 rows, **all** none/seniority to regex, and **0** where the first two passes
still answer. Of the 9,109, 86 had a main answer that a guard above had first withdrawn (they are among the 1,194
same-tier rows, and a test pins the seam: a substitution clause withdrawn from pass one, "Two (2) years" read by
the third); the guards alone change 1,570 rows and give two rows an answer. So the answers that move are moved by
the corrections, on purpose, which is why this ADR amends ADR-0066 instead of hiding the change in it. The
before-number substitution guard runs in the third pass only, and a test pins that too: "a master's degree can be
substituted for 2 years of experience" keeps its 2 in pass one.

**Against ADR-0357.** The third pass still fires only where passes one and two find nothing, and `stated_floors`
reads it (a test pins it). Netflix's ladder is Tier 3 and answers only a posting that states no number, so a
third-pass number now beats it where the description holds one.

**Precision, read by hand with the text in front of me: 40 fresh random rows per class** (a new seed for every
read, drawn from the changed rows of the table above; the guards were tightened between reads, and each class
below was read on the code or a tighter version of it, so the later guards remove rows and add none):

| class | rows | right | misses |
| --- | --- | --- | --- |
| "five (5) years" | 7,880 | 39 of 40 | a degree's credit-equivalence clause read as a floor |
| 46-80 gap | 826 | 39 of 40 | "Even better, you may have 3-4 years" (now guarded as preferred) |
| expertise / exp | 319 | 39 of 40 | "From 5 up to 10 Years of expertise" read 0-10 |
| degree substitution | 353 | 39 of 40 | "2 years of industry experience may be substituted for 1 year of DoD experience" |
| ceiling word | 287 | 40 of 40 | |
| decimal / thousands | 252 | 38 of 40 | a numbered list's "2.6+ years"; a preferred "1+ years" |
| education | 222 | 38 of 40 | "one year of university or industrial research experience" dropped; a preferred six years exposed |
| window | 57 | 40 of 40 | |
| age | 54 | 40 of 40 | |
| contract | 12 | 11 of 12 | a "desirable" 3-5 years exposed |
| no single rule | 320 | 38 of 40 | a "Desired: 8+ years"; a benefits sentence "paid over 2.5 years" (now guarded) |

The third pass's high floors, the slice the review feared most: 242 of its gains are 15 years or more (220 spelled,
19 gap, 3 expertise), and 40 of 40 read were real requirements ("Twenty (20) years experience as a SWE in
programs and contracts of similar scope"), so no cap beyond the expertise one is needed. The first round's read
of the first version (25 rows a class, v326) found 23 of 25 for "five (5)", 25 of 25 for the gap and 22 of 25 for
expertise; the misses it found are the guards above.

**Why the gap recovers 826 rows and not the audit's 1,581.** The audit's probe widened main's `_GAP` from 45 to
80 characters in *every* first-pass and second-pass pattern (forward and reversed, digits and number words) and
that gap's character class holds "." so a match can cross a sentence. Reproduced on v44 with main's own module it
answers 1,518 of the 158,771 rows main leaves without a stated number. This change also answers 894 of them
(every class together, not the gap alone). Of the other 624: 351 are answered by a reversed or spelled-number
pattern the gap rule does not use (it is forward only, the reversed one reads "experience ... we've spent 10
years"), 103 cross a full stop, 162 are forward gaps of 46-80 clean characters the guards or the prose screen
refuse, and 8 are under 46 characters, which main's own gap reads and a guard then refuses. The 826 is the gap
rule's own gains.

**Residues, measured and left.** Each is a class this change did not create or could not fix to 90%; none is hidden
in a number above.

1. *A company's own "20 years" read as a requirement, at the minimum of 20.* 693 rows of the served table carry
   `min_years` 20 on main's code and 826 after this change (the extra 133 are the third pass's spelled "Twenty
   (20) years experience" requirements, which read right: 122 of the 133 are the third pass's gains). Twenty of them read: 10 are company history ("LocalEyes
   has more than 20 years of experience", "our professionals have an average of 20+ years' experience"). Not
   fixed: a cue rule for company history at 15-20 years was tried in the deep-dive, changed 539 rows and about
   half of what it removed were real requirements.
2. *A preferred number in the first two passes* is read as a requirement, as it always was ("Desired: 8+ years
   relevant experience", "3-5 years Master Data management experience is desirable"); a guard that clears an old wrong
   read can expose one (2 of 40 education rows, 1 of 12 contract rows). A preferred-only guard in every pass is a
   policy change for its own measurement.
3. *A ceiling clause beside a stated floor keeps main's reading of its number*, a floor no higher than main's, so
   the job stays visible to the smaller cohort ("BS and 3 to 5 years or MS and less than 2 years" is 2) and
   ADR-0079's smallest floor still chooses. It is main's answer, not a right one, and is left.
4. *Two-level postings beyond the gap*: "5+ years (for Associate) or 8+ years (for Vice President) of professional
   software engineering experience" reads 8, the 5 sitting 86 characters from "experience".
5. *"From 5 up to 10 years"* reads 0-10; a numbered list's "2.6+ years" reads 2; a degree's equivalence clause
   with two numbers ("equal to one(1) year of specialized and two (2) years of general experience") reads 2;
   "5 year updates are no longer required" reads 5 once a clearance sentence has gone. All read lower than the truth
   or no higher than main, so none hides a job.

**Entry level** (`min_years` unknown or at most 0) falls from 102,234 rows to 98,286: stated 7,844 to 8,179, title
estimate 21,465 to 21,224, unknown 72,925 to 68,883. At most 2 years 176,545 to 174,757; at most 5 386,189 to
384,834; at most 10 486,681 to 486,166. Every drop is a Job that now states a floor, or a ceiling that used to read
as a floor and now reads as one.

**A one-time rewrite.** `DERIVATIONS_VERSION` moves to 27 (ADR-0357 took 26 while this change was in review, so 25
is never bumped), and the sweep rewrites the 10,582 rows whose served value differs: about 61 MB at the 5.9 KB a row
that ADR-0340 measured (5.76 KB on v44's compacted data files: 2,931 MB over 496,739 rows). A run in which nothing
else changed writes nothing after it. It stacks with any other derivation change that ships in the same merge, and
each of those is its own rewrite.

**Runtime.** `extract()` costs 1.29x on 3,000 served descriptions (1.51 s to 1.94 s against main, best of five
alternating runs). The pipeline derives only new and changed rows, so the cost is the sweep above and a few percent
of a run.

## Alternatives

Measured in the deep-dive that preceded this change and in the review rounds (local, not committed):

**Read the widenings as first-pass patterns.** "five (5)" first moved 1,005 existing answers (772 downwards,
mostly wrong: a template's "Four (4) years of additional experience ... may be substituted" beat the requirement
under the smallest-floor rule), and the long gap first moved 557. As a last resort they move none, which is the
ADR-0066 rule kept.

**Let a ceiling yield to any floor in the description, or drop it outright.** It breaks a multi-level posting
("Junior: up to 10 years / Senior: min 15" served 0-10, would become 15), and a clause dropped outright lifts the
answer past main's: "BS and 3 to 5 years or MS and less than 2 years" would answer 3 and hide the job from a
master's holder. The clause keeps main's reading of its number where a floor is stated.

**Guard a mixed fraction and a slash alike.** Tried, and read: "5/7 years" is a range, and the broader guard turned
68 of them from main's 7 into unknown. Narrowed to a digit, a space, a digit and a slash ("3 1/2").

**A cue rule for company history at 15-20 years.** It changed 539 rows and about half of what it removed were real
requirements.

**Drop the whole gap class.** The audit's probe was wrong about what it would read, not the class: with the
screens the class is 39 of 40 right, so it ships.

**The DF-02 selection change** (which floor to prefer among several: first, largest, or a ladder-aware hybrid).
Not built into this decision: ADR-0079 quotes the owner's "the smallest one, I don't want any user to miss a job
they could be qualified for", and the owner kept it on 2026-09-29.
