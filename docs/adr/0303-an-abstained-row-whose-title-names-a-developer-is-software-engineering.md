# ADR-0303: An abstained row whose title names a developer is software engineering

**Status:** accepted · **Date:** 2026-09-29 · **Extends:**
[ADR-0220](0220-a-trained-title-classifier-decides-a-role-family.md) (the head abstains below its
cutoff) · **Relates to:**
[ADR-0224](0224-a-rows-description-vector-joins-its-title-in-deciding-its-role-family.md) (the head reads the row's
description vector too)

## Context

The Trends tab carried a line, Unclassified tech, of 26,971 open Jobs (7.2% of 376,963) on
2026-09-29: the rows on which the head's top family probability fell under its 0.4 cutoff. It was
not warm-up backlog. All 26,971 titles were in the title cache. The cutoff was chosen on 81
uniform dev rows by a "coverage ≥ 0.90" rule, so 7% was a setting, not a measured optimum.

A random 250 of those rows, labelled from title, department and company before the head's answer
was read (one labeller, so about ±6 points on the shares): 57% had a family a reader could name,
10% were non-tech, 32% could not be told without the description. 21% of all 250 were plain
software engineering: "Python Developer", "Developer L3", "Senior Software Engineer
(Python/Flask+React)".

## Measurements

| Way of placing an abstained row | Right on |
| --- | --- |
| Force the head's top pick (drop the cutoff), rows with a nameable family | 43% (64% within its top two) |
| The head saying non-tech on an abstained row | 15 of 64 (23%) |
| The title rules that label the head's silver set, where they fire on those rows | 40 of 69 (58%) |
| **A title naming a developer, programmer or software engineer → software-engineering** | **25 of 30 (83%)** |

The rule fires on 3,926 of the 26,971 rows (14.6%). Its five misses in the sample were a partner
manager, a faculty post, "IT Specialist (Programmer/Operator)", a verification role and "Anguler
Developer" (frontend). Two repeat offenders in the whole bucket were named and excluded: developer
relations ("Developer Advocate", "Developer Relations", "Developer Evangelist", about 65 rows) and
consumer "Product Developer" (about 20).

## Decision

1. **Where the head abstained, a title naming a developer, programmer or software engineer is
   `software-engineering`.** `role_family_classifier.names_a_software_developer`, applied in
   `decide_rows` to a row whose title the cache holds and whose verdict is `unclassified-tech`.
   It never overrules a family the head decided, and never touches a row whose title is not yet
   encoded, so the warm-up gate is unchanged.
2. **Dropping the cutoff was rejected.** Forcing the head's answer is wrong on more than half the
   rows a reader can place, and its non-tech verdict there is wrong three times in four.
3. **The tick's Methodology carries the rule.** `family_classifier_version` is now
   `"{head.version}+developer-title-rule-{n}"` (`classifier_version`), so the rows the rule moves
   are one declared counting change ("sorted jobs into categories more accurately"), not hiring.
   The title cache and the assignment snapshot stay keyed by `head.version` alone: the rule never
   invalidates what the head decided, and the moves land in the ADR-0057 reassignment ledger as
   `unclassified-tech → software-engineering`. Changing a word of the rule bumps
   `DEVELOPER_TITLE_RULE_VERSION`.

## Consequences

- About 3,900 rows move out of Unclassified tech into Software engineering on the first tick after
  this ships, and the Trends epoch ledger records why.
- The rest of the bucket (about 23,000 rows) is untouched. The measured next steps are a cached
  LLM resolver that reads the description, and retraining the head on its labels.
- Roughly one row in six the rule fires on is not software engineering. That is the accepted
  price of a rule with no model behind it.
