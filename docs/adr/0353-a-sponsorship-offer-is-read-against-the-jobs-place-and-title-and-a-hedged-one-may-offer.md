# ADR-0353: A sponsorship offer is read against the job's place and title, and a hedged one only may offer

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0333](0333-visa-sponsorship-and-relocation-are-read-from-descriptions-by-rules-at-query-time.md)
(the work-authorisation rules and filter) and
[ADR-0342](0342-the-sponsorship-eval-judges-apart-from-the-spaces-rules.md) (the
`sponsorship_polarity` verifier) · agent contract 20 · no served-table schema change, no
stored-data change

## Context

The round-4 critique of the hosted MCP server (2026-09-29, 7.9/10, gap P1-3) read 20
`offers_sponsorship` results and 10 `refuses_sponsorship` results through `get_job`'s Mentions
line. Offers were right in 17 of 20 strictly, refusals in 9 of 10. The errors were the ones
ADR-0333 had deferred:

- **An offer scoped to another country.** n8n's "Senior Developer Advocate, US" says "We can
  sponsor visas to Germany; for any other country, you need to have existing right to work". It
  offers nothing to the US job.
- **An offer limited to levels.** Medtronic's "Software Engineering Manager" says sponsorship "is
  offered exclusively for Principal-level roles and above".
- **A hedge.** Amgen's "Sponsorship for this role is not guaranteed" was read as an offer. An eval
  answer dropped the job for exactly that reason, and the eval's t36 verifier then failed the
  answer for naming it.
- **A conditional citizenship clause.** AeroVironment's "Some positions will require current U.S.
  Citizenship" was read as refusing this job.

## Decision

**1. A hedged offer is its own, weaker stance: `may_offer_sponsorship`.** `stances()` now takes the
job's `title` and `location` beside its description, and returns at most one of three sponsorship
stances: `refuses_sponsorship`, else `offers_sponsorship`, else `may_offer_sponsorship`. A hedge is
read two ways. Words that promise nothing are read before the negation, since they hold a "not":
"not guaranteed", "case by case", "should not be assumed", "not all positions", "not always". Words
that weaken an offer are read after it: "may be available", "may sponsor", "might", "select" or
"certain positions", "depending on", "discretion". The name says what the stance claims: the
posting may offer sponsorship, and whether it does for this job is not stated.

**2. An offer is read against the job it is posted on.**

- **A country.** When the offer's clause names a country as its scope (English names from the
  `country` filter's gazetteer, plus "US", "U.S.", "UK", "America", "Britain", "Holland" and a few
  more; not a country named as where the candidate comes from, as in "outside Canada" or
  "citizens of"), the job's `location` is read by `country_gazetteer.classify`, the same reading
  as the `country` filter. The job is **in** scope when they share a country. It is **out** of
  scope when its place is known and not named. It is **unknown** when no country can be read from
  the place ("Remote", none).
- **A level.** When the clause limits the offer to a level ("Principal-level roles and above",
  "senior positions"), the title's rank is read on one ladder (intern 0, junior or associate 1,
  none stated 2, senior 3, staff or lead 4, principal 5, director and above 6). A manager's title
  without a principal-or-higher word is **unknown**: ladders place managers beside, not on, the
  individual-contributor rungs.

What an out-of-scope offer then is: **nothing, not an offer**, and **a refusal** when the text
says the scope is the only one ("only", "exclusively", "solely", "limited to"), because then it
says this job does not get it. A level limit always refuses a job below it: a posting names a
level only to limit the offer. The n8n sentence is nothing for its US job: "for any other
country, you need … right to work" is a separate sentence, with no sponsor word, which the rules
do not join to the offer. Its Mentions line still quotes both sentences. An **unknown** scope is
`may_offer_sponsorship`: the offer may reach the job, and the answer does not claim it does.

**3. A citizenship requirement of some positions is not a refusal of this one.** "Some", "certain",
"select", "specific", "many" or "most" positions, roles, programs, contracts or customers, and "may
require", skip the citizenship clause. "U.S. Citizenship required" still refuses.

**4. The filter keeps or drops the weaker stance by value, and `offers_sponsorship` drops it by
default.** `work_authorization=offers_sponsorship` keeps only firm offers to this job.
`work_authorization=may_offer_sponsorship` keeps those **and** the hedged or unknown-scope ones, the
set of jobs that at least may offer it. `/requirements` counts `may_offer_sponsorship` the same
way, so a count and a filter agree. `/job` names the one stance the job holds, and `get_job` glosses
`may_offer_sponsorship` as "not a firm offer". `offers_sponsorship` drops the hedged by default
because a visa holder who applies on a false offer loses the most. The critique's own sample and
the eval answer both treated "not guaranteed" as not an offer. The looser reading is one argument
value away, and the answer says which kind each job is.

**5. What the draws after each freeze found** (see Measurement). Each is now a rule and a
labelled row:

- A demand for work authorisation **already held** refuses, sponsor word or not: "for this role,
  applicants must be currently authorized to work", "You must currently possess valid and
  unrestricted U.S. work authorization", "You must have a valid NZ work visa". Without
  "currently", "already" or "valid" the demand is the usual one an offer sits beside ("must be
  legally authorized to work in the United States. Visa sponsorship is available").
- A curly apostrophe negates as a straight one does ("doesn’t support immigration sponsorship"
  had read as an offer).
- Work authorisation "required" is not helped with, whatever "support" is near it; immigration
  as a product's domain ("support immigration and border security operations") is no mention;
  "provide proof of work authorization" offers nothing.
- An offer for a move the job does not need ("If you wish to relocate, we are happy to help you
  obtain a visa") or made later ("after 2 years' tenure") only may offer.
- "EU" and "EEA" name their member countries as a scope; "UK/EU" reaches a job in France.
- Citizenship named but not required is no refusal: the agency "U.S. Citizenship and Immigration
  Services", RTX's field label "U.S. Citizen, U.S. Person, or Immigration Status Requirements:",
  a definition ("A U.S. person according to their definition is a U.S. citizen").
- `mentions` quotes a "U.S. person" sentence. AeroVironment's job does refuse sponsorship: it
  verifies U.S. person status under the ITAR, which no visa holder has. The critic judged it by
  the one sentence the Mentions line showed.

**6. The eval judges hedges and dropped jobs itself** (amends ADR-0342). `sponsorship_polarity`
reads a hedge ("not guaranteed", "case by case", "may be available", "might", "select positions",
"certain roles") within eight words of a sponsorship word, before its negation check. A hedged job,
or one a person labelled `may_offer`, passes only on an answer line that says it is hedged ("may",
"not guaranteed", "possible", "case by case"). With `said_ok`, a job named on a line saying it was
dropped, left out or excluded passes, and that line may name it by its company alone ("I dropped an
Amgen listing"). t36 now sets `said_ok`: round 4's t36 r1 named Amgen only to drop it. The eval
still reads no country or level scope of its own; a scoped offer it cannot see is caught only by a
person's label.

## Measurement

HF's resolver quota (5,000 requests in 5 minutes) refused every read of the served table on
2026-09-29 (other sessions were reading it too), so rows came from the live Space's `/job` and
`/search`. That is the served table as the Space held it that evening.

**The labelled fixture, 660 → 893 rows** (`tests/fixtures/work_authorization_labelled.jsonl`):

- Every row now carries its job's `title` and `location`, read by id from `/job` (649 of 660; 11
  had left the index and read as unknown).
- 35 of ADR-0333's labels were revised after reading each row with its job's place. 32 hedged
  offers became `may_offer`, among them Amgen's "not guaranteed", Deloitte's "Limited immigration
  sponsorship may be available" and Archer's "Certain positions may be eligible". Two jobgether
  offers to "a UK/EU country" became `may_offer`. inworld's Germany job, whose US visa help is for
  a later move to the Bay Area, became `none`. Two AECOM labels I first gave from
  a cut mention were corrected before measuring.
- Added: the critic's 30 rows (`critic-round4`); 53 neighbours found by description keyword on
  the live Space ("not guaranteed", "case-by-case", "exclusively for", "certain positions",
  "some positions" citizenship, "principal level" and others; `neighbours-v3`); and the three
  draws below. All tuned on except the last draw.

Over all 893, the final rules:

| Stance | Precision | Recall |
| --- | --- | --- |
| offers_sponsorship | 0.99 (329 of 332) | 0.98 (329 of 336) |
| may_offer_sponsorship (the hedged tier alone) | 1.00 (46 of 46) | 1.00 (46 of 46) |
| offers or may offer (the filter's `may_offer_sponsorship`) | 0.99 (375 of 378) | 0.98 (375 of 382) |
| refuses_sponsorship | 0.98 (307 of 312) | 0.98 (307 of 314) |
| offers_relocation | 0.99 (228 of 230) | 0.99 (228 of 231) |

On ADR-0333's untuned random half (100 rows), unchanged: offers 4 of 4, refusals 41 of 42.

**Fresh offers, drawn after the rules froze.** The live Space listed 3,896 of its 4,033
`offers_sponsorship` jobs (ADR-0333's rules; `/search` stops at 1,000 rows, so it was listed per
ATS). Each draw shuffled them with a new seed, left out every labelled id, read each job by `/job`
and kept the first 50 the frozen rules read as `offers_sponsorship`. I read each through its
title, place and quoted sentences, strictly: an offer of sponsorship to this job, now.

| Draw | Rules at | Right, strict | Wrong |
| --- | --- | --- | --- |
| 1 | commit 4966efef | 45 of 50 (0.90) | PHINIA ×2 ("for this role, applicants must be currently authorized"), UCI (a heading, then "provide proof of work authorization"), DualEntry (sponsorship "after 2 years' tenure"), BoostDraft (visa help only for an optional move to Japan) |
| 2 | commit fedba721 | 47 of 50 (0.94) | Carrier ("doesn’t support immigration sponsorship", a curly apostrophe), MSR-FSR ("support … EU work authorisation and eligibility required"), Jump Trading (an internship; it sponsors full-time roles) |
| 3 | commit 3bee7a81 | **48 of 50 (0.96)** | Ministry of Social Development ("You must have a valid NZ work visa"), Securiport (immigration is its product's domain) |

Each wrong answer became a rule (Decision 5), and each draw joined the fixture. The third draw is
the one measured on frozen rules, and it holds 0.96. Its two misses were fixed after it, so the
shipped rules have not been measured on a fresh draw. Four of the 48 were counted right on a
lenient reading: Fusion Consulting ×2 ("Get help with visas, permits"), Scale AI's Qatar role
(visa processing is part of hiring) and Wise ("support transfer of visa sponsorship" for local
candidates). With all four counted wrong the draw is 44 of 50.

Of the 63, 65 and 61 live offers read to fill the three draws, the rules moved 11, 13 and 10 to
`may_offer_sponsorship` and 2, 2 and 1 to `refuses_sponsorship`; about one ADR-0333 offer in five
is hedged, scoped or refused. The draws come from ADR-0333's offers, so they cannot show an offer
the new rules find that the old ones did not (a firm offer beside "not for every role" had been a
refusal).

## Considered

- **Keep a hedged offer as `offers_sponsorship`** (ADR-0333's reading of "not guaranteed"): the
  most recall, but it is the error the critique and an eval answer both caught.
- **Make `offers_sponsorship` keep the hedged by default, with a flag to drop them**: a new Space
  parameter for one distinction the enum already carries.
- **Disjoint filter values** (`may_offer_sponsorship` keeping only the hedged): an agent wanting
  every job that may sponsor would need two searches and would have to merge their totals.
- **An out-of-scope offer as `refuses_sponsorship` always**: "We sponsor visas to Germany" on a US
  job says nothing about the US, and a refusal filter should hold what a text refuses.
- **Reading a city as the offer's scope** ("our Barcelona office"): the gazetteer's city words
  are built for location strings, and prose names cities for other reasons ("moving to Barcelona
  from"); left out.

## Deferred

- The complement sentence ("for any other country, you need … right to work") is not joined to the
  offer before it, so such a job reads as no stance, not as a refusal.
- A region other than the EU and EEA ("European locations", "EMEA") is not read as countries,
  and a city named as scope ("our Barcelona office") is not read; Surt AI's remote Tunisia role
  still reads as an offer.
- "H-1B" does not imply a US scope.
- The eval reads no scope of its own.

## Consequences

- An agent asking which jobs sponsor visas gets firm offers by default. It can widen to
  `may_offer_sponsorship` and say which jobs only may sponsor.
- `offers_sponsorship` counts fall; the jobs they lose move to `may_offer_sponsorship`, to no
  stance, or to `refuses_sponsorship`.
- Every boot reads each description's title and location with it; `classify` runs only for an
  offer that names a country, cached by place.
