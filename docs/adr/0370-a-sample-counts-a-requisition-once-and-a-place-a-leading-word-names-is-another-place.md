# ADR-0370: A sample counts a requisition once, and a place a leading word names is another place

**Status:** accepted · **Date:** 2026-09-30 · **Amends:**
[ADR-0365](0365-a-copy-is-one-posting-on-two-boards-and-every-row-counts-toward-per-company.md)
(what a requirements sample counts, and one first place),
[ADR-0325](0325-the-model-retries-an-edge-failure-a-scan-runs-alone-and-the-eval-waits-for-its-server.md)
(how the eval waits for its server) · **Relates to:**
[ADR-0332](0332-a-requirements-sample-counts-each-requisition-once-under-its-directory-name.md)
(each requisition counts once),
[ADR-0366](0366-an-agency-name-is-read-off-a-boards-own-label-and-a-watched-roles-total-counts-one-basis.md)
(agency names off a Board's own label),
[ADR-0367](0367-answers-name-a-may-offers-kind-a-loose-match-a-small-sample-a-passed-end-date-and-a-near-id.md)
(a may-offer's kind),
[ADR-0369](0369-every-company-line-says-when-its-opened-was-mostly-found-late.md) (found late)

## Context

The round-5 fixes of the MCP critique (#1004 to #1010, fixed point 5d94c2ff) had one code review
on two axes. This ADR records the calls made in applying it.

## Decision

### Two predicates: one posting, and one requisition (SP4)

ADR-0365 narrowed `requisition_copies.copies` to "one posting on two Boards", so that a search page
stops folding distinct requisitions into one group. The requirements sample used the same
grouping, so each country's posting of one requisition started to count on its own. DevOps went
from 217 to 265 postings counted. That reversed ADR-0332's "each requisition counts once", and
nobody had asked for it.

`jobs/requisition_copies.py` now holds two predicates:

- **`one_posting`**, with `joins` and `posting_groups`, is ADR-0365's rule. A search page lists
  copies by it (`search_jobs`), and `per_company_cap.spread` keeps a copy beside its posting by it.
- **`one_requisition`**, with `requisition_groups`, is ADR-0332's rule as it stood before ADR-0365.
  It covers one posting on two Boards, or the same company spelled alike and the same title with
  brackets dropped, placed anywhere. Unnamed rows count only on one Board. `/requirements` counts
  by it.

The module name still fits. A posting on two Boards and a posting per country are both copies of
one requisition, the glossary's word, and the docstring names both predicates.

`requirement_counts._capped` stays as it is (S8). With requisition grouping, no two counted Jobs
are copies of one another, because `one_requisition` is symmetric. So `spread`'s keeping of a
posting's copies would have nothing to keep. Sharing it would only add the markers `spread` writes
on rows. Six lines of strict counting are simpler.

### A word that begins a place's name makes another place (SP6)

ADR-0365's containment test took one first place's words, when all were among the other's, as one
place. So "York, PA" and "New York, NY" were one place. The test now requires one of two things:

- the same words in any order; or
- the shorter's words as a run within the longer's, not led by a word that begins a place's name:
  new, old, east, west, north, south, upper, lower, great, little, port, fort, mount, lake, saint,
  st, san, santa.

"Hyderabad" still runs within "India - Hyderabad", and "Pune" within "Pune Maharashtra India". A
repeated word counts once: "Tokyo - Tokyo" runs within "Ariake - Tokyo".

I weighed the review's other suggestions against the whole table, and both cost far more:

- **Token equality after normalising order and punctuation** drops every pair where one Board adds
  a site to the city, such as "MapleLabs Bengaluru", "Sanchong District" or "Glasgow Campus".
- **A whole segment, or extra words that name a country or region** (the gazetteer city plus
  country) still drops 361 of the 3,007 pairs. All 40 of those I read were one posting on two
  Boards.

### The eval judges hedges by its own words (SP7)

`space_mcp_eval._HEDGED_IN_SENTENCE`, `_TRANSFER` and `_NEW_VISA` were copies of
`work_authorization`'s patterns. The verifier is meant to be simpler than those rules so that it
does not share their errors. The three are replaced by `_HEDGE_PHRASES`: plain lower-case phrases,
each written beside the may_offer-labelled wording it came from.

Measured against the 1,062 labelled descriptions, no description labelled a firm offer has a
sponsorship sentence carrying one of the phrases. A self-test keeps that true, and checks that the
module imports no `work_authorization`. Two phrases were narrowed on that measurement:

- A bare "consider" was in 119 firm offers ("Capital One will consider sponsoring a new qualified
  applicant"), so only "open to consider", "shall be considered" and "may be considered" remain.
- A bare "visa transfer" appeared beside a new visa ("visa transfers and new visa sponsorship are
  listed as available"), so only "open to visa transfer" remains.

The Space reads "We sponsor visas, pending company approval" as a firm offer, and the verifier
reads it as hedged. A self-test pins both readings. t36's `why` says what the verifier can and
cannot catch.

### A denial is of agency status only (SP8)

t40's `_DENIED` stripped from any "not" to the end of the clause. So "HeadStart does not verify it,
so it may be a staffing agency" read as a denial. A denial is now a negation followed only by words
that say how HeadStart tags a company ("flag it as", "an", "any", "sign that it is"), and then an
agency kind or a list of them ("not an agency or recruiter", "no staffing flag").

### Found late is said in one set of words (S4, S5, S6, SP1)

- hiring_now's flag now reads in `found_late.clause`'s words, as read_trends and company_profile
  do, and calls `found_late.mostly_found_late` directly.
- `found_late.attach` now fills the payload in place and returns None, which is what its one
  caller already assumed. The rule and the wording stay in one module, because the wording is two
  functions over the rule's own constants.
- **Kept as is (SP1).** A company line shows its fresh and found-late split only when the rule
  calls its opened mostly found late. That is when hiring_now flags a row. Showing the split on
  every line would add a clause to lines whose opened is hiring, and a reader would weigh numbers
  that change nothing.

### Wording homes (S2, S3, S9, S10)

- **`noun_counts`** gains `verb` ("is"/"are") and `listed` ("a, b and c").
- **`may_offer_words.not_firm`** says a may-offer the same way on a search row and a read by id:
  "not a firm offer: hedged; a visa transfer only". It replaces `search_arguments.MAY_OFFER_WORDS`.
- **`answer_date.today`** is the one "today" get_job and search_jobs count to. Tests and replays
  pin both tools through it.

### The eval waits for its server (HARNESS)

Claude Code 2.1.212 waits for an MCP server before it starts a run only for
`MCP_CONNECT_TIMEOUT_MS`, which defaults to 5,000 ms. `MCP_CONNECTION_NONBLOCKING=false` and
`MCP_TIMEOUT` do not extend that wait. The installed binary proves it: it logs "servers: 1/1 not
ready after 5000ms — proceeding". The hosted server connects and lists its tools in about 3 to
10 s.

`run_env` now also sets `MCP_CONNECT_TIMEOUT_MS=60000` for `--http` runs. This amends ADR-0325's
§4, which said `MCP_CONNECTION_NONBLOCKING=false` alone made the run wait.

Re-recording the replay fixture (SP2) added runs for t40, t41 and t43. It showed that t43's
verifier counted its window back from the real clock, so its replay read a URL nobody had
recorded. `tools_clock_at` now holds the verifiers' clock with the tools' clock.

### Labels (SP3, SP5)

- **SP3.** Each of Truelogic, Breakmark, BizFirst and Algoleap was re-read on at least 5 live
  postings, and all four keep STAFFING. Algoleap's comment now rests on the end clients'
  requisitions it passes through, and it is the least settled of the four.
- **SP5.** Of the 104 Boards the registrable-label rule cleared, 24 are not on SAP. Their postings
  show 22 employers, 1 agency (`hr.nippon24.jp`, Japanese-only and in no directory entry) and 1
  unclear Board (`bigwater.consulting`). No host rule separates the agency from the employers, so
  the rule stays as it is. `hr.nippon24.jp` is listed for curation. It rests on one posting, below
  the 5 a label needs.

## Measurements (2026-09-30)

- **SP6, whole served table** (HF `jobs.lance` v123, 501,709 rows). Pairs ADR-0365's rule groups:

  | First places | Pairs |
  | --- | --- |
  | Equal | 12,887 |
  | Short and long name, place not compared | 5,071 |
  | Containment | 3,007 |

  - The new test drops 10 of the 3,007 containment pairs, and I read all 10. Each pair names a
    neighbouring place by a leading word: East Springfield ×4 (Eversource), New Delhi ×2, East
    Naples ×2, North Amityville and South Lansing.
  - They look like one posting written with two neighbouring places. So they are 10 lost merges
    in exchange for a rule that can no longer merge "York" with "New York". That shape did not
    occur in the table.
- **SP6, 20 live `/search` pages of 40 rows**, uncapped. ADR-0365's rule and this one each fold 26
  rows, and no group changes.
- **SP4, 5 live `/requirements`-shaped samples** (the 300 closest rows, then a cap of 8):

  | Sample | Pre-ADR-0365 | ADR-0365 | This change |
  | --- | --- | --- | --- |
  | devops engineer | 216 | 265 | 216 |
  | backend engineer | 237 | 277 | 237 |
  | data engineer | 224 | 264 | 224 |
  | java developer, IN | 213 | 254 | 213 |
  | full stack engineer, US | 70 | 75 | 70 |

- **HARNESS**, a hosted sample of 10 runs (t12 and t28, 5 each), cost $0.96:
  - 10 of 10 connected at init and were judged. Before the fix, 3 of 3 probe starts were pending.
  - Verdicts: t12 passed 5 of 5, and t28 passed 4 of 5. The t28 failure was on wording.

## Consequences

- **Agent contract 26.** `/requirements` counts fewer postings, and `/search?per_company` can split
  the rare pair the new place test separates.
- **Merges lost.** A posting whose two Boards write neighbouring places ("East Springfield" and
  "Springfield") is listed twice on a search page, and it counts twice in a sample only when its
  company is spelled two ways.
