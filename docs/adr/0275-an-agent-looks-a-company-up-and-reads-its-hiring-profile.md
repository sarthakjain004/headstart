# ADR-0275: An agent looks a company up and reads its hiring profile

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0253](0253-an-agent-reads-the-spaces-read-routes-through-a-read-scoped-token.md) (the agent
contract and `/companies/suggest`'s `match`),
[ADR-0258](0258-the-spaces-read-routes-answer-anyone.md) (the public read routes),
[ADR-0267](0267-the-space-hosts-the-mcp-server-at-a-url-anyone-can-add.md) (the hosted server),
[ADR-0185](0185-trends-narrow-to-companies-picked-from-a-directory-of-boards.md) (the Company directory)

## Context

An independent critique of the Space MCP server (2026-09-29, round 1, 4.5/10) found no company
tool (its P1-5). Its recruiter persona could not answer "how many openings does Stripe have, where,
at which levels, in which categories, what share remote". A typo in `search_jobs`' company box
("Strpie") answered 0 jobs with no suggestion, although `read_trends` offered Stripe for the same
typo (calls rc03 and rc06). No route served where a company's jobs are.

Measured on the live Space the same day, the routes a profile needs already answer everything but
the places. `/facets?board=greenhouse:stripe` gave 224 jobs with every dimension counted (11.9 s
on the first call after a deploy, 2.8 s for Amazon's 9,667). `/trends?company=greenhouse:stripe`
gave the category lines, 218 tech openings, 6 jobs the tech filter sets aside, and postings opened
and closed since 2026-09-25, when turnover counting began. `/companies/suggest` offers one entry
per name, the largest: its matcher drops a smaller same-named entry (`company_suggestions.suggest`).

## Decision

**Two tools, one module each, on the existing registry.**

- **`find_company`** takes a `name` (or a Board key, looked up exactly) and lists every Company
  directory entry it may mean, best first, each with its key, Boards, tech openings and how it
  matched, in words (exact name, alias, prefix, every word, one typo, spaces ignored). It picks
  none. It says that anything but an exact name or alias is a guess, and that of entries sharing a
  name only the largest is listed, as in the site's picker.
- **`company_profile`** takes one `company`, resolved exactly as `read_trends` resolves one
  (`company_scope.for_trends`: a key, or an exact name or alias; a looser name is refused with the
  suggestions). It then reads four routes at once: `/facets` with `board=` for each of its Boards
  (the scope `search_jobs` sends for a key), `/trends?company=<key>` over the trailing 30 days,
  `/companies/locations` over its Boards, and, for a name, `/companies/suggest` again, to name the
  other entries the name may mean. "Deloitte" reads as the 86-opening Canadian Board, while
  Deloitte South Asia has 1,038, so the answer names the others.
- **The trend leads with postings opened and closed**, then the change in openings with its
  re-counted part named. The change in openings also moves when HeadStart re-counts, so it is
  not the hiring figure. The answer says from when turnover is counted.
- **The experience figures are the facet's ceilings, said as such:** "open to someone with at
  most 0, 2, 5, 10 years", with the note that a job stating no experience counts at every level.
  The facets cannot separate unstated experience into a band of its own.
- **Each tool's `when_to_use` is one short sentence.** The server's instructions are
  1,111 characters with six tools, under the 2,048 a client reads.

**`company_scope` gains the lookup, not a second copy of it.** `suggest` returns directory
companies with their `match` and ATSes, `find` is what `find_company` offers, and `alternatives` is
what `search_jobs` appends when no company name contains the text: up to five suggestions with their
keys. If the Space cannot suggest, the answer only asks for another spelling, since the offer is
a courtesy and must not fail the search. The existing refusal's wording is unchanged; it now
renders through the same `DirectoryCompany.offered`.

**One new public read route, `GET /companies/locations?board=…&limit=…`.** It lists the places the
served jobs on 1 to 200 Boards name most (default 10, at most 50), with how many jobs were
counted, how many name none and how many distinct places there are. `serving/location_counts.top`
does one filtered scan of the `location` column, bounded at 50,000 rows and saying when it reaches
the bound; `JobSearch.locations` checks the request. A location is the served string with its
whitespace collapsed and nothing else merged. The route is scoped by Boards alone, so no
Account's lists reach it, and it needs a Board, so it can never read the whole table. Measured
through `top` on a local 514,163-row snapshot: Amazon's 9,651 rows in 28 ms, Deloitte South Asia's
884 in 21 ms, Stripe's 221 in 21 ms.

**A new agent contract, 5.** `/companies/locations` is new contract, so the app's
`_AGENT_API_VERSION` and the server's `AGENT_API` rise to 5 together (ADR-0253's rule): a stdio
server meeting an older Space says it needs a deploy rather than failing on a 404.

## Options rejected

- **A location dimension in `/facets`.** `facets.counts` counts a filter's fixed options, each
  with its own lifted where-clause; `location` is free text with no options, so it would be a
  second kind of count in the strip, paid by every browser search that never shows it. A
  count-only mode was being added there in parallel, so a separate function kept the two changes
  apart.
- **Normalising places (city, country).** It needs a gazetteer beyond India's, which the
  critique's P1-3 asks for separately. Merging by text now would claim "Seattle, WA" and "Seattle,
  Washington, USA" are one place on no evidence.
- **Level bands from `/trends?split=bands`.** It splits one category, not a whole company. A call
  per category would multiply the reads for a figure the facets already approximate honestly.
- **Picking the best candidate in `find_company`.** The directory joins Boards only on proof
  (ADR-0185), so one employer can be several entries. A pick would hide exactly the ambiguity the
  user needs to settle.
- **`required` arguments in the schema.** The registry's contract test holds every tool's defaults
  to its own schema, so `name` and `company` are optional in the schema, and a call without one is
  refused in a sentence.

## Risks, stated plainly

- **Places are as each employer wrote them.** A company that writes one place several ways has its
  count split across them, and "N/A" is a place when an employer writes it (21 of Stripe's
  jobs). The answer says the places are as written.
- **Same-named directory entries stay hidden from `find_company`**, as they are from the site's
  picker. A smaller entry is reachable only by a key from another answer (a `search_jobs` result
  id, a `hiring_now` row).
- **A profile makes four to five reads.** In process they share the `/mcp` request's slot
  (ADR-0267). The `/facets` read is the slow one: 2.8 to 11.9 s measured, cached for a minute.

## Consequences

- The tools are documented in `docs/agents/space-mcp-server.md` §"The tools". The evaluation has
  three iteration tasks for them (t19 to t21), and the sealed held-out tasks are untouched.
- Tests: `tests/test_space_mcp_server.py` covers both tools and the search's offer against a fake
  Space. `tests/test_space_mcp_against_space_app.py` runs them against the real app, including over
  `/mcp`. `tests/test_space_app.py` covers the route's scope and refusals, the public-path set and
  the contract version. `tests/test_serving_location_counts.py` covers the counting.
