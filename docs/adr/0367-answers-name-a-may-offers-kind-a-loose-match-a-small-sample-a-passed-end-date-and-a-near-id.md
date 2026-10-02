# ADR-0367: Answers name a may-offer's kind, a loose match, a small sample, a passed end date and a near id

**Status:** accepted · **Date:** 2026-09-30 · **Under:**
[ADR-0353](0353-a-sponsorship-offer-is-read-against-the-jobs-place-and-title-and-a-hedged-one-may-offer.md),
[ADR-0355](0355-a-search-says-where-every-matching-job-is-and-a-requirements-sample-takes-type-stance-and-pay.md),
[ADR-0344](0344-the-location-filter-reads-accents-and-a-citys-other-spellings-as-one-place.md) ·
agent contract 23 · no served-table schema change, no stored-data change

## Context

The round-5 critique of the MCP server (2026-09-30, score 8.1) left six polish gaps:

- **R5-P2-2.** A `may_offer_sponsorship` search lists firm offers beside hedged, transfer-only and
  out-of-scope ones, and its rows did not say which is which (p1f, 8 rows), so the model needed a
  `get_job` call per row.
- **R5-P2-4.** `search_jobs`' `detail: full` Where-snapshot said "spellings merged" yet listed
  "Bangalore" 302 and "Bengaluru" 297 under India, "Zurich" and "Zürich", "Krakow" and "Kraków",
  and listed "SG" and "Fab 10A" as Singapore's places (p5c).
- **R5-P2-5.** Prompt-injection text as a query led with "0.64 System Engineer · SLB", presented
  like any match (a01).
- **R5-P2-6.** `role_requirements` over 4 distinct postings said "Microservices 50% (1 employer)"
  (p3b).
- **R5-P2-8.** PwC's "Data Architect-Senior Manager" states "Job Posting End Date December 17,
  2025" and was served on 2026-09-30 (s13, s14).
- **R5-P2-10.** `get_job` printed "End of description." after a cut description, answered 5 ids
  with one empty as "Read 0 of 4", and, when eval t35's model typed one character too many into
  a 36-character UUID, said only that the id is not in the index.

## Decision

**1. A may-offer row carries its kind, read with the stances at boot.** `work_authorization.reading`
returns the stances and, for `may_offer_sponsorship`, why it is not firm: `hedged`,
`transfer_only`, `scope_unread` (an offer limited to a country or level the job's place or title
does not show), in that order. The Space's one read of every description
(`WorkAuthorizationRows`) keeps each may-offer id's reasons, so `/search` under
`work_authorization=may_offer_sponsorship` tags every row's `sponsorship` by a dictionary lookup:
no extra read and no extra latency. A batched `/job` read of the page from the MCP server was the
alternative; it would have cost one to four more round trips (each about 0.8 s) and up to
60,000 characters of descriptions a page. `/job` carries the same reasons as `may_offer_because`,
so `get_job` names the kind too. Which stance a job holds is unchanged.

**2. A country's top places group the spellings the `location` filter reads alike, and list no
code or site.** `location_spelling.place_key` folds accents and maps every spelling of a
`GROUPS` or `ONE_WAY` place to one key; `location_counts._by_country` groups a country's cities
by it and shows the spelling most of their jobs carry. A city that is only a two- or
three-letter code ("SG") or holds a digit ("Fab 10A", "HK-TKO 5/F") is not listed, and its jobs
still count in the country. Measured on the served table (v95, 497,094 rows, 2026-09-30): every
one of the 128 countries' totals is identical before and after, so each still equals `/facets`
per country; 40 countries' top three places changed. India now reads Bengaluru 22,024
(was Bengaluru 11,745 and Bangalore 10,279), Switzerland Zürich 557 (282 + 275), Portugal Lisbon
1,510 (995 + Lisboa 515). The four places holding a digit among the 384 top places were all sites
(Fab 10N/X 254 and Fab 10A 170 under Singapore, HK-TKO 5/F 24, "4503 - Karo Platinum Mine" 1).

**3. A first page whose closest row scores under 0.72 says nothing matches closely.** Calibrated
on 37 live `/search` queries (2026-09-30): the closest row of 22 queries naming a real tech role,
5 of them in one country, scored 0.733 to 0.871; of 15 naming no tech role 0.611 to 0.793
("barista" 0.793, "litigation lawyer" 0.746, "truck driver" 0.735, "dentist" 0.741). 0.72 is the
highest cut with a margin below every real query, and it flags 9 of the 15. The line says the rows
are loose, most likely other roles, and to say so. It speaks on page 1 only.

**4. A `role_requirements` sample of fewer than 30 distinct postings gives counts, not shares,**
and a line calls them anecdotes: "Terraform 3 of 4 (2 employers)", "Remote: 1 of 4."

**5. `get_job` says when the posting's own text states an end date that has passed.**
`jobs.stated_end_date.latest` reads a closing label ("Job Posting End Date", "Closing date",
"Applications close", "Deadline", "Apply by", "Last date to apply", "posting expires on") followed
at once by a date with its year, and answers the latest such day. A numeric date is read only when
it cannot be two days ("25/09/2026" yes, "5/10/2026" no). The Space reads the whole description
(`/job`'s `stated_end_date`), and the MCP server compares it with today: "The posting states it
ended on 2025-12-17 (…), yet its Board still lists it: it may have closed, or the date may be
stale; check the link before applying." A future date is not said.

Measured on the served table (v95, 497,094 rows, 2026-09-30), over the 39,351 descriptions naming
a closing word: **3,209 state an end day and 582 of those days have passed** (0.12% of served
rows). PwC holds 260 of the 582; the rest spread thin (Civic Recruitment 16, Anglian Water 10,
SunSoft 9, Gulfstream 9, Amazon 8). 510 more descriptions state their end only as an ambiguous
numeric date and are left unread. PwC's are often reposted with the old date kept: of 40 passed
samples, several were first seen by HeadStart weeks after the stated end, so the date is not
proof of closure, which is why the line says "may".

**Owner option, not built:** hide from search (or evict) a served row whose own text states an
end date more than N days past. It would take out at most the 582 rows above (0.12%), 260 of them
PwC's. Hiding or evicting served rows is the owner's decision; this ADR only discloses.

**6. `get_job` counts what it did not read, and offers a mistyped id's nearest posting.** The
first line adds "Of the 5 ids sent, 1 empty, skipped" (and repeats, read once). `/job` answers
`closest`: for each missing id, the served id on the Board the id names whose posting part
`difflib` rates at least 0.9 alike, with its title. One added character in a 36-character UUID
rates 0.986; unrelated UUIDs about 0.5. On the largest Boards the scan and match took 13 to 25 ms
(Amazon, Apple, HCLTech, on a local table of the v95 ids). A cut description ends "End of the
quoted part."

## Consequences

- The agent contract moves to 23: `/search`'s `sponsorship` under `may_offer_sponsorship`, and
  `/job`'s `may_offer_because`, `stated_end_date` and `closest`.
- A near id on a Board with sequential numeric ids can be a different real posting; the line
  quotes its title so the reader can tell.
- The weak-match cut is one number on one model; a new embedding model needs a new calibration.
- Left as it was: a place naming another country first ("Remote - Lithuania, …") is still listed
  whole under each country it names (ADR-0331's rule).
