# ADR-0359: Sponsorship precision is quoted from a fresh strict draw, and a hedge holds back a firm offer

**Status:** accepted · **Date:** 2026-09-30 · **Amends:**
[ADR-0353](0353-a-sponsorship-offer-is-read-against-the-jobs-place-and-title-and-a-hedged-one-may-offer.md)
(its measurement table, and its rule that a firm offer beside a hedge stands) · **Under:**
[ADR-0079](0079-smallest-stated-experience-requirement-wins.md) as the owner re-affirmed it in
[ADR-0357](0357-the-owner-keeps-the-smallest-stated-experience-and-get-job-names-the-others.md) ·
no agent-contract change, no served-table schema change, no stored-data change

## Context

The round-4 code review of the MCP fixes (fixed point `5e57051d`) raised three spec findings this
ADR settles.

- **SP1.** ADR-0353's table and the `work_authorization` argument's description quoted 0.99
  precision for `offers_sponsorship` over 893 hand-read descriptions. Those 893 rows are the set
  the rules were tuned on, so the figure measures fit, not precision. Its one fresh draw (48 of
  50) was taken before two more fixes, so the shipped rules had not been measured on a fresh draw
  at all.
- **SP3.** A job's served minimum is the smallest floor its description states (ADR-0079). A
  "Senior Software Engineer" whose description says "1+ years of Kubernetes" beside "7+ years of
  software engineering" is served at 1 year, and a `max_years` 1 search lists it. The owner kept
  ADR-0079 on 2026-09-29 and asked for disclosure, never a change of which clause wins.
- **SP5.** `hiring_now` named O'Reilly Auto Parts "o-reilly-auto-parts": `board_naming.board_names`
  took the first row's name, and that Board carried "O'Reilly Auto" on 37 rows and its slug on 1.

## Decision

**1. The figure an answer quotes is a strict reading of a fresh draw.** A draw is 50 live
`offers_sponsorship` jobs, 25 placed in the US and 25 elsewhere, taken after the rules are
frozen in a commit, with every labelled id left out. Each is read by hand through its title,
place and every sponsorship sentence. **Strict** counts a job right only when its description
offers *this* job sponsorship now, without a hedge. **Lenient** also counts a hedged, conditional
or limited offer. Capital One's "will consider sponsoring a new qualified applicant for this
position" is strict: its postings say that or "will not sponsor", so it is the employer's yes.

**2. A hedge anywhere holds back a firm offer.** The first draw showed the commonest strict error:
"We do sponsor visas! However, we aren't able to successfully sponsor visas for every role and
every candidate." ADR-0353 let the firm first sentence stand. Read strictly, the second says this
role may not get it, which is what ADR-0353 itself calls `may_offer_sponsorship` ("not
guaranteed", "not all positions"). So:

- a description with a firm offer and a hedge that reaches this job is `may_offer_sponsorship`;
- a hedge anywhere in the offer's own sentence counts, not only in its 70-character window
  ("We provide visa sponsorship support and assess each circumstance on a case-by-case basis");
- "can't always guarantee" is a hedge;
- "open to considering" and "subject to … approval" only may offer;
- an offer of a visa **transfer** only ("H-1B transfer sponsorship available", "Open to visa
  transfers", "support transfer of visa sponsorship") only may offer: a candidate who needs a new
  visa is not offered one. "Visa sponsorship and transfers" and "new H-1B visas and transfers"
  stay offers.

A refusal still outranks both.

**3. Two rounds, then the truth.** The review allowed one round of fixes after a draw under 0.95
and a second fresh draw, then no more. The second draw's four misses are recorded below and are
not fixed after it, so the shipped rules are exactly the ones measured.

**4. `search_jobs` tags a senior-titled row for a new user** (SP3). When `max_years` is 2 or
less, a row whose title reads Senior, Staff, Principal, Lead, Manager or Director, and not
Associate or Junior, carries: "senior title: its stated minimum may be a side clause, not the
role's requirement; read get_job's line on the floors it states before calling it a fit".
Disclosure only: the row is listed where it was, and the served minimum is ADR-0079's. `get_job`
already names every floor a description states (ADR-0357). Eval task t39 ("new grad, no
experience, backend jobs in the US") fails an answer that names such a row without a caveat on
the line naming it; the verifier reads the titles itself, apart from the tool's tag.

**5. A Board is named by the name most of its rows carry** (SP5). `board_names` counts each
Board's served company names and takes the most common; a tie keeps the one read first.

**6. What ADR-0353 already states, kept.** A demand for work authorisation already held refuses
(`_ALREADY_AUTHORIZED`, ADR-0353 Decision 5), and an offer limited to levels above the job's title
refuses it (ADR-0353 Decision 2). Both stand unchanged.

## Measurement

Rules at `origin/main` `90037521` (ADR-0353's, shipped as agent contract 21), then at this
branch once Decision 2 was made, committed before draw 2 and unchanged to this ADR's merge. Draws
read the live Space's
`offers_sponsorship` listing per ATS, then `/job` for each description, on 2026-09-30.

| Draw | Rules | Pool | Strict | Lenient | US strict | Elsewhere strict |
| --- | --- | --- | --- | --- | --- | --- |
| 1 (seed 4101) | shipped, ADR-0353 | 3,193 of 3,507 unlabelled | **40 of 50 (0.80)** | 50 of 50 | 19 of 25 | 21 of 25 |
| 2 (seed 5202) | Decision 2, as merged | 3,122 of 3,476 unlabelled | **46 of 50 (0.92)** | 49 of 50 | 24 of 25 | 22 of 25 |

Draw 1's ten strict misses, every one hedged or limited, none a refusal: Anthropic ×4 ("not for
every role"), Cartesia (case by case, in the same sentence past the window), GPTZero and Lavendo
(transfers only), Aurora Energy Research ("open to considering … subject to company approval"),
Raydar (sponsorship for a later move to San Francisco from a London job) and Clera ("where an O-1
visa is a realistic path"). Decision 2 moves the first eight to `may_offer_sponsorship`; the
last two stand (a city as scope and a visa class as condition are not read).

Draw 2's four misses, left standing: Fusion Consulting's "Get help with visas, permits and
international assignments" (a global-mobility benefit, not this job's visa), Magic's "Visa
sponsorship … if possible", SoftLabs' "visa sponsorship shall be considered for the right skill
sets", and Jump Trading's internship, whose employer sponsors "full-time positions" only (ADR-0353's
second draw missed the same posting type). Only the last is outright wrong; lenient precision is
0.98.

**The shipped figure is 0.92 strict, 0.98 lenient, on 50 fresh jobs.** It is under the 0.95 the
review asked for. An agent that promises sponsorship should read the job's sponsorship sentences
first, and the `work_authorization` description now says so.

**The fixture, 893 → 989 rows** (`tests/fixtures/work_authorization_labelled.jsonl`). Eleven
`offers` labels became `may_offer` under Decision 2's reading (Anthropic, Apollo Research ×2,
Goaly, Cartesia ×3, Lavendo, Wise ×2, Aurora), each noted. Draw 1's 46 still-served rows
(`predicted-offers-v4-first-draw`, tuned on) and draw 2's 50 (`predicted-offers-v4-frozen`) were
added with sponsorship labels only; their relocation is not labelled and not measured. Over all
989 the rules read `offers_sponsorship` at 0.98 precision and 0.98 recall and the hedged tier at
1.00 and 0.93: the rules' own tuning set plus one fresh draw, so a fit, not the figure to quote.

**SP5.** On the served table read off HF on 2026-09-30 (version 380, 499,735 rows), 39 of the
37,371 Boards with a served name change name: O'Reilly Auto, Starbucks (was its Eightfold host),
Larsen & Toubro Limited (was "Larsentoubrocareers"), Kohl's, Advance, and 34 multi-entity Boards
(Personio and Teamtailor groups) that move from one subsidiary to the commonest.

## Considered

- **Keep a firm offer beside a hedge as `offers_sponsorship`** (ADR-0353). The most recall, but
  it is the draw's commonest strict error, and it contradicts ADR-0353's own reading of "not all
  positions".
- **Keep fixing after draw 2 and quote the fixture.** Every fix after a draw leaves the shipped
  rules unmeasured, which is the error SP1 found; the review capped it at two rounds.
- **Filter senior titles out of a new graduate's search.** It would override ADR-0079 by the back
  door and drop real entry-level roles with an odd title; the owner asked for disclosure only.
- **Name a Board from its curated name only.** Most Boards have none; the commonest served name
  is the smallest change.

## Deferred

- A type scope ("we sponsor for full-time positions" on an internship) and a city or visa-class
  scope are not read.
- "if possible" and "shall be considered" are not hedges yet: adding them after draw 2 would ship
  unmeasured rules.
- The eval's own sponsorship verifier (ADR-0342) does not read Decision 2's hedges.

## Consequences

- `offers_sponsorship` loses the firm-plus-hedge and transfer-only jobs to `may_offer_sponsorship`;
  the filter's `may_offer_sponsorship` still keeps them. No stance value or route shape changes,
  so the agent contract stays at 21.
- A new graduate's search tags senior titles; `get_job` names the floors behind each.
- The next directory build renames 39 Boards.
