# ADR-0338: A sorted query orders only close matches, and the tools agree on age and employer

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0074](0074-browse-and-paginate-the-search-index.md) (the sorted window),
[ADR-0331](0331-a-copy-needs-one-companys-words-and-a-missing-id-gets-one-account.md) (the copy
rule) · **Relates to:** [ADR-0275](0275-an-agent-looks-a-company-up-and-reads-its-hiring-profile.md),
[ADR-0322](0322-a-category-spans-the-index-and-an-agent-filters-by-age-experience-and-employer.md),
[ADR-0332](0332-a-requirements-sample-counts-each-requisition-once-under-its-directory-name.md)

## Context

The round-3 critique of the MCP server (7.4 of 10) found six gaps in how searches, profiles and
their numbers fit together:

- **P1-9.** With a `query`, `sort: posted` or `sort: salary` led with rows unrelated to the query.
  `cs03`, "junior data analyst", remote, at most a year of experience, sorted by posted date,
  opened with "Senior AdTech Engineer", "Database Administrator" and "DBA Sênior", at similarity
  0.54 to 0.57. The Space re-orders the 2,000 closest matches. That window is always full, so
  under narrow filters it reaches far below the role.
- **P2-1.** Adyen's Software Engineering read 20 in `company_profile`, 15 in `search_jobs` and 20
  with `max_age_days: 0`. `search_jobs` leaves out postings over a year old by default
  (ADR-0322); the profile counts every age and never said so. `role_requirements` took no
  `max_age_days`, so its sample could hold postings search hides.
- **P2-2.** `salary_min` 200,000 with `salary_max` 50,000 answered 49 rows. A query holding every
  constraint ("3+ years senior python developer remote in Berlin paying 100k") matched 460,383
  jobs, and nothing said the query narrows nothing.
- **P2-3.** TSMC's "Software Engineer (New Graduate) - North America Software Center" was listed
  twice: SuccessFactors "TSMC" in "Vancouver, WA, US" and Avature "TSMC - Taiwan Semiconductor
  Manufacturing Company Limited" in "USA-Washington". ADR-0331 groups two spellings only when they
  are the same words, and only in the same first city.
- **P2-6.** A salary with no currency read "85,000–155,000 a year"; a company read "1 tech
  openings"; `find_company` "Adyen" offered "Adventist Health" as "one typo away" beside the exact
  match; `max_age_days: 0` left the scope line silent.
- **P2-7.** Deloitte is five directory entries. `company_profile` read one (Deloitte Canada, 85)
  and listed the others, but gave no single number.

## Decision

**A sort under a query or `like=` re-orders only the rows of its 2,000-row window that score at
least 0.67** (`job_search.SORT_FLOOR`). Relevance order is unchanged: it shows every match, most
similar first. The answer states the floor and the lowest score it shows. A page past the rows
above the floor says why it is empty.

The floor was measured, not chosen. I read the whole 2,000-row relevance window of 20 live
queries (2026-09-29): 14 role names, 3 descriptive queries ("backend engineer at a climate
startup"), and 3 under narrow filters (`cs03`'s own, "rust developer" in Germany, "machine
learning intern" in India). I labelled each row by whether its title names the role, with one
pattern per query.

- **16 windows never fall below 0.67.** Their lowest scores run 0.671 to 0.780, and the floor
  leaves them whole. In 13 of them 71–99% of rows are on the role; the other three are
  descriptive or broad ("product manager", "payments infrastructure", "dashboards for a
  fintech"), where a title pattern undercounts.
- **4 windows reach far below it.** Their share of rows on the role, before and after the floor:

  | Query | Window's lowest score | Rows kept | On the role, all 2,000 | On the role, kept |
  |---|---|---|---|---|
  | `cs03` junior data analyst, remote, ≤1 yr | 0.551 | 83 | 26% | 76% |
  | technical writer | 0.631 | 863 | 46% | 93% |
  | rust developer, DE | 0.567 | 21 | 1% | 86% |
  | machine learning intern, India | 0.600 | 216 | 6% | 36% |

- **0.65 left the tails of the narrow windows off the role.** In the least similar fifth of the
  rows it kept, 61%, 19%, 17% and 10% were on the role; at 0.67 the first three reach 76%, 71%
  and 60%.
- **0.68 cuts a broad window.** "junior data analyst" with no filters bottoms out at 0.671 with
  86% on the role, and 0.68 drops 564 of its rows.

The alternatives measured worse:

- **The top 200** keeps only 8–15% of the on-role rows of the 16 windows the floor leaves
  whole.
- **A margin under the top score** (top − 0.10) keeps 9 rows of `cs03` and 76 of "junior data
  analyst", because the top score varies more between queries than the relevant band does.

The website takes the same floor. Its note says a sorted query shows "your best matches", which
the old window did not honour either. A query nothing scores 0.67 against now shows "Nothing
matched" when sorted, and its matches when ordered by relevance. Every one of the 20 queries had
matches well above the floor: its closest scored 0.772 to 0.884.

**`company_profile` says how many of its jobs are over a year old, overall and per category.** It
asks `/facets` for the total within `search_jobs`' default 365 days beside the total of every age.
When some are older, it asks both totals for each listed category, and marks the category
"N over a year old". So Adyen's "Software Engineering 20 (… 5 over a year old)" matches search's
15. `role_requirements` takes `max_age_days`, with the same schema and 365 default as
`search_jobs`, through `search_arguments`, which now sends it for both tools.

**Impossible bounds are refused; constraints in a query get a line naming their filters.**

- `salary_min` above `salary_max`, and `required_years_at_least` above `max_years`, are refused
  before any read.
- A query holding years ("3+ years"), pay (a currency sign or code, "100k", "30 lpa", or a bare
  number of four or more digits that is not a year), a place the `country` gazetteer reads, or
  "remote" gets a line saying the query only ranks and naming each filter to use.
- It is a note, not a refusal, so a false reading costs one sentence. Of about 40 role-only
  queries I tried, one read as a place: "phoenix elixir" (Phoenix, US). "web3", "c++17", "ec2"
  and "2027" read as nothing.
- `role_requirements` gives the same line.

**A short and a long name of one employer group when the stated pay matches too**
(`requisition_copies`). One name's words, legal forms and ADR-0331's three generic words dropped,
must begin the other's. Both rows must also have the same title stem, the same countries by the
gazetteer, and the same stated annual range in the same currency. The first city is not compared:
"Vancouver, WA, US" and "USA-Washington" are one place written two ways.

This was measured over the whole served table (500,134 rows, 2026-09-29):

- **Countries alone were too loose.** A word-prefix name, the title stem and the countries
  grouped 189 spelling pairs, and I read all of them. Many were other companies: "GE" and
  "GE Vernova", "Applied" and "Applied Materials" and "Applied Medical", "Hotel" and "Hotel
  Bethlehem", "Tesla" and "Tesla Laboratories", "Toyota" and "Toyota Tsusho".
- **Adding the same stated pay left 46 spelling pairs.** I read every one. All 46 are one
  employer's posting under two names: "Booz Allen" and "Booz Allen Hamilton", "Veolia" and
  "Veolia Environnement SA", "TSMC" and its long name, "Capital One" and "Capital One - CA",
  "Takeda" and "Takeda Pharmaceutical".
- **On 20 live search pages of 40 rows** the new rule added 6 groups: TSMC twice, Booz Allen
  three times and OneMain once. All six were true copies. It removed none.

A job that states no pay still needs the same words. So "GE" and "GE HealthCare" with no stated
pay stay apart, as ADR-0331 wants.

**`company_profile` takes `companies`, several keys or exact names read as one employer.**

- Each name or key is resolved as `company` is.
- Every count is over their Boards together, so each served job counts once. No two directory
  entries share a Board.
- Each entry is listed with its key, Boards and openings.
- `/trends` takes every key at once and answers their combined total.
- A single profile's "other companies" line now points at `companies`.
- The limits are 10 companies and 200 Boards, what `/companies/locations` reads.
- A posting listed on the Boards of two entries is counted on each, and the answer says so.

**Small fixes.**

- A salary with no currency reads "currency not stated: 85,000–155,000 a year".
- One opening is singular.
- A typo match reads "one typo from a word of the name, or from its start". That is what
  `company_suggestions._near` matches: "adyen" is one letter from "adven". `find_company` drops
  typo matches when the name matched exactly or by alias.
- `max_age_days: 0` reads "any age (max_age_days 0)" in the scope line.

The agent contract goes to 12. A sorted `/search` answers fewer rows, and `/requirements` groups
by the new copy rule.

## Consequences

- A sorted search over a small pool can list fewer rows than before, or none: 21 rows for "rust
  developer" in Germany. The rows it drops are the ones not on the role, and the answer names the
  floor and how to see every match.
- The floor is one number for every query, while the score scale differs a little between
  queries. "machine learning intern" in India still keeps 36% on the role. If the embedding model
  changes, this floor has to be measured again. The labelling scripts are local, under
  `experiment/search-sort-floor/`.
- `company_profile` makes one to two dozen more `/facets` counts when a company has postings
  over a year old. Each is a total over its own Boards.
- The copy rule's pay test also groups a company's two names when one posting really does
  appear on two Boards with one range. It cannot group copies that state no pay.
