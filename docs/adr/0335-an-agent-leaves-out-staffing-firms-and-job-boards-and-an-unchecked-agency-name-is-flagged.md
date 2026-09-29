# ADR-0335: An agent leaves out staffing firms and job boards, and an unchecked agency name is flagged

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0171](0171-the-hot-tab-curates-what-it-shows-not-the-whole-index.md) (the Operator labels),
[ADR-0238](0238-a-mostly-re-counted-line-gives-no-percentage-and-hot-hides-only-staffing-and-job-boards.md)
(the Operators the Hiring now tab hides),
[ADR-0322](0322-a-category-spans-the-index-and-an-agent-filters-by-age-experience-and-employer.md)
(the agent-only filters and family tables),
[ADR-0321](0321-an-agent-reads-hiring-now-by-opened-less-closed-and-every-trend-says-its-turnover-span.md)
(flagged `hiring_now` rows go last), [ADR-0253](0253-an-agent-reads-the-spaces-read-routes-through-a-read-scoped-token.md)
(the agent contract)

## Context

The round-3 critique of the MCP server (2026-09-29, 7.4/10) found two gaps:

- **P1-1.** `search_jobs` and `role_requirements` could not leave out staffing firms and job
  boards. A sorted "junior data analyst, remote" search listed Jobgether, a job board the Hiring
  now tab itself hides, in 6 of 8 rows (`cs03`). The DevOps requirements sample was led by
  DigitalXNode, FindMyJob.lk, ACME HR Consulting, Angel and Genie and "Chimney Sweep Masters"
  (`cs02`); Jobgether was the second company of "senior backend, DE" (`se04`).
- **P1-2.** `hiring_now` ranked Vrinda International third as an "employer" (`jr05`). Its postings
  are clinical psychologists in Oman, a paediatric neuropsychologist in Abu Dhabi with an INR
  salary, and Salesforce developers: a recruitment agency. "Employer" is only the default for a
  company no curated list names (ADR-0171), and the answer did not say so.

## Decision

### `operators=` on `/search`, `/facets` and `/requirements`

- **A keep-list of Operators** (`employer`, `services`, `staffing`, `aggregator`), comma-separated.
  Absent, or naming all four, keeps every row, so the page is unchanged. An unknown Operator is a
  400 whether or not the request is strict: a misspelt keep-list would keep less than asked.
- **It reads the same Operators the tab does.** The app hands `JobSearch.operator_boards` each
  non-employer Operator's Boards from the Company directory `/hot` ranks, whose Operators are
  decided again as it loads (ADR-0238). An employer is every Board no other Operator names, so a
  list keeping employers names the Boards it leaves out, and any other list names the Boards it
  keeps. Without a directory every Board is an employer's; without `operator_boards` at all, a
  strict request is refused with a 503, as a category without its tables is.
- **One `regexp_like` over `lower(id)`**, `compiler.board_pattern_clause`: one pass for every
  Board named, where `board_clause`'s LIKE a Board is one pass each. Only the Boards that hold a
  served row are named, found by one scan when `operator_boards` is set: the directory named 101
  staffing and aggregator Boards on 2026-09-29, of which 33 held a row.
- **Kept out of `SearchFilters`**, as `board=` and the follow and hide lists are: it is a
  directory hand-off, and a Saved Set must not freeze the curated list as it stood.
- **What it left out is counted.** `/facets` and `/requirements` answer `operators_left_out` when
  `operators=` is sent: the total without it, less the total with it. The count without it is
  one the scalar indexes answer, where counting the left-out Boards' rows would match every id
  again. A category's requirements find its Jobs once without `operators=` and read it off their
  ids.

The agent contract is now 15 (`_AGENT_API_VERSION`, `space_client.AGENT_API`): an older Space
would ignore `operators=` and answer with every Operator's rows.

### The MCP tools default to what the tab shows

`search_jobs` and `role_requirements` take `operators`, a list, default `["employer",
"services"]`: every Operator but `hot_ranking.HIDDEN_BY_DEFAULT`, so a search and the tab leave
out the same companies. The scope line says so, with the count: "staffing firms and job boards
left out, as the site's Hiring now tab hides them: 1,803 jobs (name them in operators to
include them)". A search that finds nothing but what the list left out says that instead of
blaming a filter. **A named `company` overrides the default**: Jobgether's jobs, asked for by
name, are not left out as a job board's. Only a list other than the default is then sent.

### `hiring_now` flags an unverified Operator

A `/hot` row carries `operator_unverified` (`board_operator.unverified`): the company is an
employer only by default, and its name or a Board's tenant holds, at the start of a word, one of
consult…, staffing, recruit…, hr, manpower, placement(s), talent(s) or international. Every Lens
flags such a row "operator unverified", the default Lens included, and lists flagged rows after
unflagged ones, as ADR-0321 does for artifacts. The line under the rows says the label
"employer" is only a default and not to report them as employers hiring.

`board_operator.VERIFIED_EMPLOYERS` holds employers adjudicated from their own postings whose
names still read like an agency's, so the flag stops once someone has looked. It is not a
label: `classify` never reads it.

The flag is a lead for the curation pass, not a classifier, because no signal measured good
enough to label by (below). A company flagged by its name is listed last until adjudicated;
the curated lists remain the only thing that labels.

## Measured

**Rows.** On the served table read off HF on 2026-09-29 (500,134 rows), the directory's
staffing Boards held 4,021 rows on 33 Boards and its aggregators 2,427 on 3. `operators=employer,
services` left out 5,177 of the 460,383 rows posted in the last year (local copy of the same
day's compact, 500,167 rows).

**Latency**, on that local copy with lancedb 0.36, `JobSearch` as the Space runs it, caches
cleared, median of 5 on a laptop (a second run moved every figure by up to 40%):

| request | before | with `operators=employer,services` |
| --- | --- | --- |
| `/facets` total, age 365 | 9 ms | 82 ms |
| `/facets` full strip, age 365 | 142 ms | 630 ms |
| newest-first browse page | 167 ms | 280 ms |
| ranked, sorted by posted date, remote, `max_years` 1 | 180 ms | 208 ms |
| ranked, `country=DE` | 3,797 ms | 3,980 ms |
| category devops total | 0 ms | 3 ms |
| requirements, category devops | 694 ms | 750 ms |
| requirements, query and `country=DE` | 4,876 ms | 5,954 ms |

The clause shapes, as the count and the browse page: a LIKE a Board cost 218 and 303 ms for
109 Boards; one alternation over all 101 directory Boards 48 and 253 ms; over the 33 with a
row 28 and 109 ms. Guarding it with `ats NOT IN (…)` gained nothing (32 and 109 ms), and `(?i)`
in place of `lower()` was slower (69 and 409 ms).

**Signals for the flag.** 278 companies of the four Lenses' top 100s (live `/hot`,
2026-09-29) were labelled: 24 agencies (the 12 `/hot` already hid, and 12 adjudicated in the
curation pass that followed), 254 employers or IT services firms, and 12 that their postings
could not settle, left out.

| signal | flagged | agencies | precision | recall |
| --- | --- | --- | --- | --- |
| name vocabulary (the rule above) | 10 | 5 | 50% | 21% |
| 20 or more job categories | 110 | 8 | 7% | 33% |
| categories per served posting ≥ 0.15 | 107 | 10 | 9% | 42% |
| countries per served posting ≥ 0.5 | 3 | 2 | 67% | 8% |
| "our client(s)" in ≥ 20% of the newest 30 descriptions | 42 | 3 | 7% | 12% |
| any of 7 description phrases (W2/C2C, contract to hire, a rate an hour, …) in ≥ 20% | 57 | 8 | 14% | 33% |

Categories and countries do not separate an agency from a large employer: Amazon spans 26
categories and 53 countries, Vrinda International 25 and 4. Government contractors and
consultancies say "our client" as often as agencies do. The name vocabulary's five false
positives were a defence test contractor, a government contractor, two consultancies and an
automotive services firm, which is why a verified-employer list comes with it.

## Alternatives

- **An `operators` field in `SearchFilters`.** It would reach the Blocking filter and the facet
  strip, and a Saved Set would freeze the curated list; a directory hand-off, like `board=`,
  is not a user's control.
- **A LIKE a Board** (`board_clause`). Exact and 4.5 times slower at 109 Boards.
- **Every directory Board in the clause.** Twice the cost of the Boards with a row, and the
  directory only grows.
- **An `operator` column in the served table.** One equality, the fastest; a served-table schema
  change, which needs the owner.
- **A classifier to label, not flag.** Measured above: nothing is precise enough to move a
  company out of "employer", which is also what `board_operator` found in 2026-09-21's
  measurement.
- **Hiding unverified rows.** At 50% precision half would be employers hidden; a flag says what
  is known, and the curation pass turns flags into labels or verified employers.

## Consequences

- An agent's search and requirements sample leave out the tab's hidden Operators by default and
  say how many jobs that was; its rows follow the curated list, so a new agency appears until it
  is labelled.
- A `hiring_now` row named like an agency and on no list is flagged and listed last; the
  curation pass clears it either way.
- Follow-ups: the page could offer the same keep-list; the ranked prefilter pays the id match on
  every row (+1.1 s on a requirements query with a country, above).
- Tests: `test_search_filters_compiler.py` (the alternation keeps what the LIKEs keep, on a real
  table), `test_serving_job_search.py` (browse, ranked, category, description-keyword and
  requirements paths, the counts, the pruning, the refusals), `test_space_app.py`,
  `test_board_operator.py`, `test_trends_hot_ranking.py` and `test_space_mcp_server.py`.
