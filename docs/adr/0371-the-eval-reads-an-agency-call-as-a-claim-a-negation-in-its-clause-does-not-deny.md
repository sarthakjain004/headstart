# ADR-0371: The eval reads an agency call as a claim that no negation in its clause denies

**Status:** accepted · **Date:** 2026-09-30 · **Amends:**
[ADR-0370](0370-a-sample-counts-a-requisition-once-and-a-place-a-leading-word-names-is-another-place.md)
(its SP8 section: how t40 reads a denial)

## Context

ADR-0370 narrowed t40's verifier (`employer_unflagged`) to strip only a denial of agency status,
then fail any agency word left on the line. The first hosted runs after the change deployed failed
right answers: 3 of 7 runs of t40 (2026-09-30). Each run answered "No, HeadStart does not suggest
Lockheed Martin is an agency", but it did so in ways the stripping could not follow:

- a restated question: "**Does HeadStart suggest it might be a staffing agency or recruiter?**";
- a list of tags not carried: "not `services` (an IT-services firm), `staffing` (an agency), or
  `aggregator` (a job board)";
- what a tag means: "HeadStart flags a row as `staffing` … when a name reads like an agency's".

Stripping denials cannot tell an explanation from a call. Every new phrasing needed another
exception.

## Decision

The verifier now looks for the call itself. A line calls its company a possible agency in three
cases:

- **It says so as a claim**, in a clause that no negation before the claim denies. The claim may
  say:
  - the company may be one ("may be a staffing agency", "possibly a recruiter");
  - it is one ("is an unverified operator");
  - HeadStart flags, treats, lists or reads it as one ("flagged operator unverified", "treats
    Lockheed Martin as a staffing firm").
- **It says the company is not the employer.**

A clause ends at punctuation or a conjunction. So a "not" in another clause denies nothing:
"HeadStart does not verify it, so it may be a staffing agency" and "is not on any curated list and
may be a recruiter" both still fail. A question calls nothing, and neither does an agency's
possessive.

## Measurements (2026-09-30)

- The self-tests hold these right answers as passing: 5 real hosted answers that the stripping
  failed, and 5 written denials.
- They hold these calls as failing:
  - the review's two SP8 sentences;
  - "possibly a staffing agency, unverified";
  - "listed, not flagged; it may be a staffing agency";
  - "flagged operator unverified";
  - "treats … as a staffing firm";
  - "not the employer".
- After the change, hosted t40 passed 3 of 3 runs.

## Consequences

The verifier misses a call written without any of its claim forms, for example "Lockheed Martin,
an agency". It is a regression check for the tools' own tag, and a tool line that carries
"operator unverified" still fails the task.
