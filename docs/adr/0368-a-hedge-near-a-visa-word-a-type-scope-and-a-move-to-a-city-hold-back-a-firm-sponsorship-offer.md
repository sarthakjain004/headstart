# ADR-0368: A hedge near a visa word, a type scope and a move to a city hold back a firm sponsorship offer

**Status:** accepted · **Date:** 2026-09-30 · **Amends:**
[ADR-0359](0359-sponsorship-precision-is-quoted-from-a-fresh-strict-draw-and-a-hedge-holds-back-a-firm-offer.md)
(its deferred list, its quoted figure and its rule that nothing is fixed after its second draw) ·
no agent-contract change, no served-table schema change, no stored-data change

## Context

ADR-0359 froze the sponsorship rules after two draws and quoted 0.92 strict precision for
`offers_sponsorship`. The round-5 MCP critique (R5-P2-1) drew 50 fresh `offers_sponsorship` rows
and read 46 of them as strict, the same 0.92. Its four misses were the classes ADR-0359 had
named or deferred:

- **Thinking Machines.** "We sponsor visas." is followed by "While we can't guarantee success for
  every candidate or role, … we're committed to working through the visa process together."
  ADR-0359 reads a hedge in the offer's own window or sentence. This hedge sits in a sentence of
  its own that offers nothing, so it went unread.
- **Jump Trading, on an internship.** "We sponsor work visas for full-time positions."
- **coera.** "We support visa sponsorship … where it makes the difference between hiring the
  right person and not."
- **Trade Republic.** "Visa support is provided if required (only if already based in the United
  Kingdom or Europe)."

ADR-0359 also deferred "if possible", "shall be considered", a type scope and a city scope. The
owner's brief for round 5 asked for the rules to be widened once more, then frozen, and measured
on **one** fresh draw whose figure is published whatever it is.

## Decision

**1. A hedge within 160 characters of a visa word holds back a firm offer, in any sentence.** A
sentence naming a visa, immigration or work permit or authorisation that holds a hedge (the
`_HEDGE` list) marks the description hedged, even when the sentence offers nothing itself. A
"future" in the same reach still exempts it, as it does in the offer window. The reach is what
Thinking Machines' 110-character gap needs. It is short enough that "Relocation is not always
needed" in a sentence with no visa word stays out.

**2. More hedges.** "where it makes the difference" is added to `_HEDGE`. "if possible", "where
possible", "when possible", "where we can", "shall be considered" and "already based, located,
living or residing in" are added to `_MAY`. An offer to candidates already in a place is
conditional on where the candidate is, not only on the job's place.

**3. An offer for full-time positions is judged against the job's type.** The offer is "for
full-time", "for permanent", or "for our full-time" followed by positions, roles, jobs,
employees, hires, employment, opportunities or staff. It offers nothing to an internship, or to
a part-time or contract job that is not also full-time. The job's type is read as the `etype`
filter reads it (`employment_type_filter.RULES`), so the title "Intern" counts even where
Workday says "Full time" (ADR-0340). A job stating no type is full-time (ADR-0341). With
"only", such an offer refuses the job, as a level scope does. `stances()` takes
`employment_type`, and every serving reader passes it: the `/search` filter, `/job` and
`/requirements`.

**4. An offer for a move to a city is judged against the job's country.** A case-sensitive
capitalised run after "relocate/relocation/move/moving to" names a city. The gazetteer places
it in its countries, and they are compared as a country named in prose already is (ADR-0353).
Raydar's "Visa sponsorship is available for relocation to San Francisco", on a London job,
offers that job nothing. Only a move counts. "Upon arrival in Montreal" or "based in Paris or
London" names the job's own place, which the location field sometimes gets wrong. A Fastwater
posting in Montreal lists "Toulouse, FR; United States; United Kingdom", and a Mistral
internship in Paris or London lists Seoul. Both still offer.

**5. The eval's sponsorship verifier reads these hedges.** `_not_offering` now calls a quoted
sentence about sponsorship "hedged" when it holds any of these anywhere: ADR-0359's hedges
("for every role", "can't guarantee", "open to considering", "subject to … approval", a
transfer with no new visa) or this ADR's. It no longer needs the hedge within 8 words of the
sponsorship word. ADR-0359 recorded that it did not read them. The type and city scopes are not
read there: `/job`'s quoted sentences do not carry the job's type.

**6. One fresh draw, published as found.** The rules were committed and frozen in this
branch's first commit, and `work_authorization.py` is unchanged from it to this ADR's merge.
Then 50 jobs were drawn once. The figure below
is the one the `work_authorization` argument's description quotes. Its three misses are not
fixed.

## Measurement

The served table was read off HF on 2026-09-30: version 95, 497,094 rows, of which 133,792
descriptions match `PREFILTER`.

**The change set, before the draw.** 78 served rows change stance between `origin/main` and the
frozen rules. Every one was read:

| Change | Rows | What they are |
| --- | --- | --- |
| offers → may_offer | 62 | Thinking Machines ×25, World Labs ×8, River AI ×4 and Elorian: the "can't guarantee … every candidate or role" template. Krea ×8 ("where we can"). Magic ×6 ("if possible"). coera ×5 ("where it makes the difference"). Integrated Graphene ("where possible"), SoftLabs ("shall be considered"), Online Filings ("only for candidates that are already based in the UK"), Trade Republic ("only if already based in"), and fal (a remote-anywhere job; the visa is for a move to San Francisco) |
| offers → none | 10 | Jump Trading internships ×9 ("for full-time positions") and Raydar (a London job; the visa is for a move to San Francisco) |
| may_offer → none | 6 | DualEntry ×4 (remote EU/UK/Canada jobs; the visa is for a move to NYC after 2 years) and Isidor ×2 (London jobs; the visa is for a possible later move to San Francisco) |

By a strict reading, each of the 72 moves out of `offers_sponsorship` is right. The fal row and
the six `may_offer → none` rows are lenient calls: the offer is real, but not for this job's
place. `offers_sponsorship` goes from 3,200 to 3,128 served rows, and `may_offer_sponsorship`
(its own rows) from 1,145 to 1,201.

**The draw.** Seed 6303 drew from the served rows the frozen rules read as `offers_sponsorship`,
leaving out every labelled id: a pool of 2,729. Rows were taken in shuffled order until there
were 25 placed in the US and 25 elsewhere, with at most 5 from any one company. That gave 36
companies, Capital One at its cap of 5. Each row was read through its title, place, type and
every sentence about sponsorship, citizenship or relocation.

| Draw | Rules | Pool | Strict | Lenient | US strict | Elsewhere strict |
| --- | --- | --- | --- | --- | --- | --- |
| ADR-0359 draw 2 (seed 5202) | ADR-0359 | 3,122 | 46 of 50 (0.92) | 49 of 50 | 24 of 25 | 22 of 25 |
| Round-5 critique | ADR-0359 | live search | 46 of 50 (0.92) | — | — | — |
| **This draw (seed 6303)** | **ADR-0368, frozen** | **2,729** | **47 of 50 (0.94)** | **50 of 50** | **25 of 25** | **22 of 25** |

Strict is read as ADR-0359 reads it:

- Capital One's and GEICO's "will consider sponsoring a new qualified applicant for this
  position" count as the employer's yes (7 rows).
- So do "for exceptional talent" (Multiply Labs) and "If you are exceptional, we will sponsor"
  (Haize Labs), as "for the right candidate" did in ADR-0359. Read as hedges, these two would
  make strict 45 of 50 (0.90).

The three misses, left standing:

- **Fusion Consulting**: "Get help with visas, permits, and international assignments". This is
  a global-mobility benefit, the same posting type ADR-0359's draw missed.
- **Titanium Birch**: "If you need an Employment Pass, we can sponsor it, but you must already
  be in Singapore". The candidate-place condition is written as "must already be in", which
  Decision 2's "already based/located/living/residing in" does not reach.
- **Superhuman**, a job listed at its Porto hub: "Superhuman provides relocation support to make
  your move to Berlin seamless. Our package includes visa assistance". The move to a city sits
  in the sentence before the visa. Decision 4 reads it only inside the offer's own window.

All three at least may offer, so lenient precision is 1.00.

**The shipped figure is 0.94 strict and 1.00 lenient, on 50 fresh jobs.**

**The fixture grows from 989 to 1,062 rows** (`tests/fixtures/work_authorization_labelled.jsonl`).

- `critic-round5`: the critic's 4 misses.
- `neighbours-v5`: 19 rows. There are 12 from the change set, one or two per phrase. There are
  also 5 the new scopes touch but leave offering: Jump Trading's two full-time roles, and fal,
  Resolution and Wintermute, whose move is to the job's own city. The last 2 are the Fastwater
  and Mistral rows whose place field is wrong.
- `predicted-offers-v5-frozen`: this draw's 50, labelled for sponsorship only.
- Two older labels change, each with a note: Jump Trading 8052338, an internship labelled
  `offers` in `predicted-v1-frozen`, becomes `none`; River AI 4423515009, the Thinking Machines
  template, becomes `may_offer`.

Over all 1,062 rows the rules read:

- `offers_sponsorship` at 0.985 precision and 0.985 recall;
- the hedged tier at 1.00 and 0.90 (0.93 before, since this draw added three hedges the rules
  do not read);
- refusals at 0.98 and 0.98;
- relocation at 0.99 and 0.99.

This is still the rules' own tuning set plus one fresh draw, so it measures fit, not the figure
to quote.

## Considered

- **Keep ADR-0359's freeze and ship nothing** (its Decision 3). That would leave the exact
  classes the critique found. The owner's brief asked for this one more round, measured by a
  fresh draw and published as found. That is ADR-0359's own discipline, applied once more.
- **Read a hedge anywhere in the description, with no reach.** Thinking Machines needs only its
  own sentence. A description-wide hedge would let "not always" in a travel sentence hold back
  real offers.
- **Read any capitalised place after "to", "in" or "at" as a scope.** An earlier cut did this. It
  read "upon arrival in Montreal" and "based in Paris or London" as scopes, and turned two
  right offers whose location field is wrong into nothing (Fastwater, Mistral). Only a move is
  read.
- **Read a city against the job's own city, not its country.** A visa is granted for a country.
  Matching cities would also need every city alias ("Bangalore"/"Bengaluru") to agree.
- **Read the job's country from its title too.** Of 3,000 sampled titles, 151 name a country,
  and 22 of those disagree with the location. "IT" reads as Italy, "On-Site" as Canada and "AR"
  as the US.

## Deferred

- A candidate-place condition written as "must already be in" (Titanium Birch), and a move to a
  city named in the sentence before the offer (Superhuman).
- A global-mobility benefit read as this job's visa (Fusion Consulting, in both draws).
- Clera's visa-class condition ("where an O-1 visa is a realistic path"), as in ADR-0359.
- The eval verifier does not read the type or city scopes.

## Consequences

- `offers_sponsorship` loses 72 served rows. `may_offer_sponsorship` still keeps the 62 that are
  hedged. No stance value, parameter or route shape changes, so the agent contract stays at 21.
  The Space computes stances at boot, so the change reaches every served row on its next
  restart, and nothing stored is re-derived.
- `/requirements` reads one more column, `employment_type`, for its sample.
- The `work_authorization` argument's description quotes 0.94 strict and 50 of 50 lenient, and
  names the type and move scopes.
