# ADR-0352: A relevance page and a requirements sample take a few postings of each company

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0074](0074-browse-and-paginate-the-search-index.md) (the 2,000-row window a page can reach),
[ADR-0338](0338-a-sorted-query-orders-only-close-matches-and-the-tools-agree-on-age-and-employer.md)
(a sort orders only close matches),
[ADR-0332](0332-a-requirements-sample-counts-each-requisition-once-under-its-directory-name.md)
(a sample counts each requisition once),
[ADR-0335](0335-an-agent-leaves-out-staffing-firms-and-job-boards-and-an-unchecked-agency-name-is-flagged.md)
(the Operators and the "operator unverified" flag),
[ADR-0253](0253-an-agent-reads-the-spaces-read-routes-through-a-read-scoped-token.md) (the agent
contract)

## Context

The round-4 critique of the MCP server (2026-09-29, 7.9/10) found two gaps:

- **P1-5. One employer fills a page.** A London staff-platform search listed Reflection in 9 of 10
  rows (`p2_07`); a US new-grad search listed Capital One in 15 of 20, partly its Radancy and
  Workday twins (`p1_01`). A relevance page is the closest matches in order, and one employer
  with many near-identical postings is all of them.
- **P1-2. One company skews a requirements sample.** DigitalXNode was 15 of the 279 postings the
  DevOps sample counted (`p5_12`), and 9 of 265 of an India "AI engineer" sample (`p5_13`): a
  sample says what a role asks for, and one company's boilerplate then speaks for it. Separately,
  `hiring_now`'s "operator unverified" flag (ADR-0335) reached neither search rows nor the
  sample's company list.

## Decision

### `per_company=` on `/search`: a company's first N, then every other company's

- **A relevance-ranked search only** (`q` or `like`, no `sort`). A sort orders by what the user
  asked for (ADR-0338's window and floor are unchanged), and a browse lists the newest; under
  `strict=1` `per_company` beside either is refused, since it would be dropped. Below 1 is a 400.
- **Re-ordered, never dropped.** `per_company_cap.spread` walks the ranked window: each
  company's first N postings keep their places, and its others follow every company's kept
  rows, still in similarity order. The row set, and so `/facets`' total beside it, is the
  search's own.
- **The whole window, fixed.** The spread is over the `max_k * max_page` (2,000) rows a sort
  already reads, so every page cuts one list: page 2 continues page 1 with no row twice or
  missed. Spreading a page, or a window sized by the page asked for, would not: a company's moved
  rows land after the end of whatever window was spread, so page 1's list (moved rows after row
  30, say) and page 3's (after row 90) are different lists, and a row can show on both or
  neither.
- **A company** is its name case- and spacing-blind, or its Board when it names none, as a
  requirements sample counts employers. A row copying a kept row of its company
  (`requisition_copies.copies`: one posting on two Boards, or per country) is kept with it
  without taking a place, since the agent lists it under that row as "also #N": Capital One's
  Radancy twin does not cost it one of its three.
- **Each row says what happened.** A kept row of a company with rows moved later carries
  `more_from_company` (how many), and a moved row `past_company_cap`, so the MCP can say "N more
  from X: send company X" and mark the moved rows when paging reaches them.
- **It lives in the Space, not the MCP.** Only the Space holds the window: an MCP-side cap could
  only spread the page it was given, so page 2 would repeat rows page 1 moved away and lose the
  ones it pulled forward. The web page never sends it, so its search is unchanged.

The MCP's `search_jobs` sends `per_company` (default 3) on a relevance-ranked search, and not
when `company` is set: a search scoped to one employer asks for its postings.

### `per_company=` on `/requirements`: at most N of one company's counted

`requirement_counts.summarize` keeps, after copies are grouped, each company's first N postings in
sample order (the closest, or the newest) and counts every figure over those. The answer says
`per_company` and `over_company_cap`, how many it left out, and each listed company keeps its
sampled count beside `counted`, so "DigitalXNode 15 sampled, 8 counted" is still said. The sample
is not refilled past its 300: a smaller honest sample, said so, is simpler than a second read.
`role_requirements` sends **8** unless a `company` is named.

**Why 8, measured.** Across the 11 live `role_requirements` samples of the round-2 to round-4
critiques (each 202 to 287 distinct postings, company-scoped samples left out), the largest company
held 3 to 21 postings (median 11) and the fifth largest about 5. A cap left out, per sample:

| cap | postings left out, median (range) | companies capped per sample |
| --- | --- | --- |
| 3 | 10.6% (0–16.3%) | 0–10 |
| 5 | 5.9% (0–10.0%) | 0–6 |
| 8 | 1.4% (0–4.8%) | 0–3 |
| 10 | 0.5% (0–3.8%) | 0–2 |

At 5 the cap reshapes the ordinary head of every sample: Northrop Grumman, Tesla, American
Express and TikTok in a US new-grad sample. At 8 it binds on the outliers, DigitalXNode (15, 17),
STAFIDE (21), ABOUT YOU (13), Jobgether (12), Cognizant (11, 12), and bounds any one company's
effect on a skill's share to about 3 points of a 270-posting sample.

### "operator unverified" reaches search rows and the sample's companies

`search_jobs` tags a row, and `role_requirements` a listed company, "operator unverified" by
ADR-0335's `board_operator.unverified` over the row's Board and company name: an employer only by
default whose name reads like an agency's. A tag, never a filter, as on `hiring_now`; the curated
lists remain the only thing that labels, and the round-4 curation pass adds the named firms to them.

The agent contract is now 17: an older Space refuses `per_company` under `strict=1`.

## Measured

**The spread, on the live ranking.** 13 realistic searches were read off the live `/search` on
2026-09-29 (`operators=employer,services`, the first 200 to 400 rows of each, a 14th failed on the
network), and each first page spread with `per_company_cap.spread` at caps 2, 3 and 5:

| search | first page as served | at 3 |
| --- | --- | --- |
| staff platform engineer, London, sponsorship, 7+ years (`p2_07`) | Reflection 9 of 10, 2 companies | Reflection 3, 8 companies; 23 more from Reflection |
| new grad software engineer, US, sponsorship, ≤ 1 year (`p1_01`) | Capital One 15 of 20, 4 companies | Capital One 5 (3 postings and 2 twins), 11 companies |
| the other 11 (DevOps, AI engineer India, junior data analyst remote, frontend US, ML DE, SRE GB, backend Bengaluru, PM US, iOS, senior backend DE and NL) | at most 2 of one company | unchanged |

So 3 moves rows only where one company fills the page; at 2 the other 11 still did not move,
and at 5 `p1_01` kept Capital One at 9 of 20 and 7 companies. The lowest score shown on the
spread pages was 0.60 (`p2_07`) and 0.66 (`p1_01`), against 0.66 and 0.69 as served: the rows
pulled forward are still the query's matches.

**Cost.** A spread page reads the window a sort reads: ADR-0084's amendment measured that
window at 256.5 ms against 105.0 ms for a single page on a 318,003-row table. The hosted cost is
measured after deploy and recorded in the PR.

## Alternatives

- **Drop a company's rows past the cap.** The total beside the page would count rows no page
  can reach; moving them keeps the result set whole.
- **A cap on the MCP page.** Inconsistent paging (above).
- **Spread only the page's own window** (`offset + k` rows, or a multiple). Cheaper, and pages
  would spread different lists (above).
- **A cap on sorted pages too.** A salary sort led by Netflix L5 roles is what the user asked;
  moving rows would break the order the answer states.
- **Refill the requirements sample to 300 after capping.** A second read of rows and
  descriptions for a few percent of the sample.
- **Cap 5 in requirements**, as the critique suggested: measured above to reshape normal samples.

## Consequences

- A relevance page shows at most 3 postings of one company before every other company's, says
  how many more each has and how to list them; paging walks one list.
- A relevance page with `per_company` reads the 2,000-row window a sort reads.
- A requirements sample counts at most 8 postings of one company and says what it left out.
- Rows and sampled companies whose names read like an agency's say "operator unverified".
- Tests: `test_per_company_cap.py`, `test_serving_job_search.py` (the spread, paging, the
  refusals, a real table), `test_serving_requirement_counts.py`, `test_space_app.py`,
  `test_space_mcp_tools.py`.
