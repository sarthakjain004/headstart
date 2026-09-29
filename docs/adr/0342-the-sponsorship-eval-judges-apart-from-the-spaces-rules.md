# ADR-0342: The sponsorship eval judges apart from the Space's rules

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0334](0334-connecting-is-counted-apart-and-the-eval-judges-truth-not-one-path.md) (the
`sponsorship_polarity` verifier) · **Relates to:**
[ADR-0333](0333-visa-sponsorship-and-relocation-are-read-from-descriptions-by-rules-at-query-time.md)

## Context

The round-3 code review found two gaps in how the eval judges visa-sponsorship answers:

- **SP8.** `sponsorship_polarity` (task t36) failed an answer that named a job the Space's own rules
  read as `refuses_sponsorship`. It judged the Space by the Space's rules, so an error in those
  rules could never fail a run. It could miss an offer read as a refusal, or a refusal the rules
  did not see.
- **SP7.** Task t13 ("software engineering jobs in Germany that mention visa sponsorship") still
  required the description-keyword path's "Descriptions are stored for" line. An agent following
  the new guidance to use `work_authorization` failed it. An agent that reported keyword matches as
  jobs offering sponsorship passed.

## Decision

`sponsorship_polarity` no longer reads the Space's `stances`. For each job the answer names, it
decides whether the job offers sponsorship in one of two ways:

1. **A person's label, where the job has one.** It uses the 660 descriptions labelled for
   ADR-0333 (`tests/fixtures/work_authorization_labelled.jsonl`). A job labelled `offers` or
   `mixed` passes. A job labelled `refuses` or `none` fails.
2. **Its own negation check, otherwise.** The job fails when a negating word ("no", "not",
   "without", "unable", "refuse", "n't", or "citizenship required") appears within five words of a
   sponsorship word in the sentences `/job` quotes. The check is deliberately simpler than the
   Space's rules, so it does not share their errors. "Within five words" is there because coera's
   "We support visa sponsorship … the right person and not" offers sponsorship.

With `said_ok`, a job named on an answer line that says it does not offer sponsorship passes,
because the answer reported it truly. A new `all_of` verifier combines checks, as `any_of` already
did.

t13 now accepts either of two paths:

- **Path 1:** the answer names at least one job, and the check above finds no named job that fails
  to offer sponsorship.
- **Path 2 (the keyword path):** the answer passes on the description-coverage caveat (ADR-0274).
  It also says that a mention may refuse sponsorship rather than offer it. Any job it names that
  does not offer sponsorship must be named on a line saying so.

An answer that reports keyword matches as jobs offering sponsorship fails both paths.

## Consequences

The eval can now fail an answer that the Space's rules would have passed. What the check catches
and misses is written into t36's `why`:

- **It catches** a labelled job whose label is `refuses` or `none`, and an unlabelled job whose
  quoted sentence negates near a sponsorship word.
- **It misses** an unlabelled job whose refusal no quoted sentence states. It still reads which
  sentences to quote from `/job`, which uses the Space's prefilter.
- **It fails a right answer** whose job offers sponsorship in a sentence that also negates nearby
  ("no matter your visa status, we sponsor").

The recorded replay fixture now holds a `work_authorization` run for t13 and a run for t36, both
recorded live on 2026-09-29.

## Alternatives

- **Keep reading the Space's `stances`.** Rejected: an eval that grades the Space by the Space's
  own reading cannot see that reading's errors.
- **Judge only labelled jobs.** Rejected: a live answer rarely names one of the 660 labelled jobs,
  so the verdict would almost always be empty.
