# ADR-0365: A copy is one posting on two Boards, and every row counts toward per_company

**Status:** accepted · **Date:** 2026-09-30 · **Amends:**
[ADR-0352](0352-a-relevance-page-and-a-requirements-sample-take-a-few-postings-of-each-company.md)
(what `per_company` counts),
[ADR-0274](0274-an-agent-asks-facets-for-the-total-alone-and-names-a-category-in-its-own-words.md),
[ADR-0323](0323-an-agent-sees-one-posting-once-under-a-company-name.md),
[ADR-0331](0331-a-copy-needs-one-companys-words-and-a-missing-id-gets-one-account.md) and
[ADR-0338](0338-a-sorted-query-orders-only-close-matches-and-the-tools-agree-on-age-and-employer.md)
(what a copy is) · **Relates to:**
[ADR-0332](0332-a-requirements-sample-counts-each-requisition-once-under-its-directory-name.md)
(a sample counts each posting once),
[ADR-0366](0366-an-agency-name-is-read-off-a-boards-own-label-and-a-watched-roles-total-counts-one-basis.md)
(an eval task's `requires`),
[ADR-0246](0246-a-radancy-career-front-is-a-board-keyed-by-its-host-scraped-in-full.md) (Radancy fronts are kept
with their duplicates)

## Context

The round-5 critique of the MCP server (2026-09-30, 8.1/10) found that one company still fills a
page (R5-P1-2). `search_jobs {"query": "machine learning engineer", "country": "US",
"work_authorization": "offers_sponsorship", "limit": 10}` (s04) listed Capital One in row 1 and
nine "also #N" rows under it; page 2 (s05) was Capital One again, rows 11 to 20. Two rules
combined:

- **The copy rule was too wide.** `requisition_copies.copies` took the same company and title,
  brackets aside, as one requisition wherever it was placed. So Capital One's four Workday
  requisitions titled "Machine Learning Engineer 5" (R1001855, R1001268, R1001338, R1001829), their
  four Radancy twins, and "Machine Learning Engineer 5 (Senior Manager, IC)" in Chicago were one
  group of nine.
- **The cap counted groups, not rows.** ADR-0352's `per_company_cap.spread` kept a copy of a kept
  row "without taking a place", so a group of nine cost Capital One one of its three places, and the
  page's `limit` of 10 was spent on one employer.

The critic's p2a (a Netherlands relocation search) showed the same on a smaller scale: payabl. held
4 of the top 8 rows, three kept and one folded as a copy of a bracketed title.

## Decision

### A copy is one posting on two Boards

`requisition_copies.copies(head, row)` is now true only for **the same title, brackets included, on
another Board of the same employer**:

- under its name or another spelling of it (ADR-0331's same-words rule), with **the same first place
  and countries**; the first place now also matches when one spelling's words are all among the
  other's ("Hyderabad, India" and "India - Hyderabad", "Pune, Maharashtra, India" and "Pune
  Maharashtra India");
- or under a short and a long name of it with the same countries and the same stated pay
  (ADR-0338, unchanged).

Four kinds of pair the old rule grouped are now separate rows:

- **Two rows of one Board.** A Board lists two requisitions as two postings, and index prune
  already folds a Board's own duplicates by id (ADR-0023).
- **A title that differs in brackets.** "Machine Learning Engineer 5 (Senior Manager, IC)" is another
  requisition.
- **One requisition per country** (ADR-0274's "Backend Developer (Peru)" and "(Chile)").
- **Rows naming no company.** Two unnamed Boards are not one company, and two rows on one unnamed
  Board are two postings.

A group holds at most one row of each Board (`requisition_copies.joins`). So Capital One's four
same-titled McLean requisitions, each on Workday and on its Radancy front, are four groups of two,
not one of eight. Radancy fronts stay served with their duplicates (ADR-0246, the owner's
decision); listing the twin under its posting is still the mitigation.

### Every row counts toward `per_company`

`spread` counts each company's rows. A company takes a new posting only while it holds fewer than
`per_company` rows. A row that is a kept posting's copy on another of its Boards stays with that
posting even past the cap, so a posting is never split from its copy across pages. With
`per_company` 3 and a company whose postings each have a Radancy twin, the page holds two postings
and their twins (4 rows), then every other company's rows.

I weighed three options against what keeps `limit` meaning rows on the page and paging
consistent:

1. **Count every row strictly.** A twin arriving after the third row would be held, and would show
   pages later as a standalone row whose posting is on page 1. It keeps "at most 3 rows" exact.
2. **Count every row, and keep a posting's copies with it (chosen).** `limit` is still rows, the
   spread is still one fixed list over the 2,000-row window, so page 2 continues page 1. A company
   can exceed `per_company` by the copies of the postings it took, which the answer's order line
   says: "at most 3 rows of one company, besides a listed posting's copy on another of its Boards".
3. **Count postings, and cap "also" rows per group (say 2, then "and N more copies").** It gives a
   company 3 postings times their copies, 6 rows of 10 for Capital One. It also needs the MCP to
   hide rows the Space served, which breaks "every id and link stays" and makes `limit` stop
   meaning rows.

With the narrowed copy rule a group rarely exceeds two rows (a Board and one front), so option 2
costs at most one row per posting and keeps each copy honest and visible.

A requirements sample (`requirement_counts._capped`) counts at most `per_company` of each
company's postings strictly, after copies are grouped. Grouping pairs each Radancy row with a
Workday row in page order, so a Workday-led group and a Radancy-led group can be each other's
copies. `spread`'s copy-keeping would then count a company past its cap (9 of 8 counted for Capital
One in a "full stack engineer" US sample), so the sample does not use it.

## Measurements (2026-09-30)

**The whole served table** (HF `jobs.lance` v95, 497,094 rows, read with column projection):

- The old rule grouped 1,209,095 row pairs. 1,146,394 of them were two rows on one Board, such as
  Nagarro's "Associate Staff Engineer" posted once per country. 37,652 were two Boards with
  different first places.
- The new rule groups 11,355 postings holding 22,945 rows, so it folds 11,590 rows, across 580
  companies. The largest are Booz Allen Hamilton (1,318 groups), Applied Materials (991), Citi
  (934), L3Harris (772) and Capital One (670).
  - In 1,903 of those groups every row has the same native id (Jibe and its LinkedIn twin,
    Teamtailor group and country accounts, ADP clients): certain copies.
  - The rest are mostly a front over a Board: Radancy with Workday (5,127), Avature with Workday
    (1,532), and Radancy with SuccessFactors (1,132).
- **False merges.** I read 40 randomly drawn groups whose native ids differ, and all 40 were one
  posting on two Boards. Examples: Booz Allen's Workday Board and Avature site; NetApp on
  SuccessFactors and two Radancy fronts; Qonto on Lever and Ashby; Mayo Clinic on Oracle and
  Eightfold. Keeping brackets in the title removed 7,533 pairs that differed only in brackets. I
  read 30 of those: 27 were distinct requisitions ("Full-stack Engineer 4 (Cyber)" and "Full-stack
  Engineer 4"), and 3 may be one posting written two ways ("(Manager, IC)" and "(Manager IC)").
- **Lost merges, recovered.** An exact first place missed 2,925 pairs that the word-containment
  test recovers. I read 40 of them, and all 40 were Workday/Radancy twins (Citi, Amgen, Boeing, USAA,
  Comcast, Palo Alto Networks).
  - "Pune Maharashtra India" and "Pune, Maharashtra, India" are the same place.
  - "India - Hyderabad" and "Hyderabad, India" are the same place.

**20 live `/search` pages of 40 rows**, uncapped, 2026-09-30:

- The old rule folded 128 rows, and the new rule folds 27.
- Every new group is one posting on two Boards:
  - 17 Capital One Workday/Radancy pairs;
  - Amgen's Hyderabad "Data Engineer" (Radancy and Workday);
  - Citi's Tokyo director role;
  - TSMC on SuccessFactors and Avature;
  - Kaluza on Greenhouse and Ashby;
  - Preferred Travel Group on two ADP clients.
- The rows the old rule folded and the new one lists apart were same-Board requisitions per city
  or country, such as L3Harris "Intern, Software Engineer" in four cities, Zwayam "Java Developer"
  ×5 and SmartRecruiters "Site Reliability Engineer" in two cities. There were also bracket variants, such as TikTok "Software
  Engineer Graduate (Transaction Platform)" and "(Foundation Platform)".

**The critic's pages, spread with `per_company` 3 over the Space's own window:**

| Page | Old: first page | New: first page |
| --- | --- | --- |
| s04, `limit` 10 | 1 company (Capital One ×10) | 6 companies |
| s04, `limit` 8 | 1 company | 4 companies |
| p2a, `limit` 8 | 5 companies (payabl. 4) | 6 companies (payabl. 3) |

On s04 with `limit` 10, the new page holds Capital One ×4 (R1001855 and R1001268, each with its
Radancy twin), then Institute of Foundation Models, Preference Model ×2, EvolutionIQ, Poesis and
Periodic Labs.

**Requirements samples.** I took the 300 closest rows of 11 role queries, grouped them and capped
each company at 8:

- The postings counted rose in every sample. Devops went from 217 to 265, backend from 237 to 277,
  Java India from 216 to 257, and full stack US from 69 to 75.
- Per-country postings on one Board now count apart, up to the cap of 8. AgileEngine leads the
  devops sample at 8, and Capgemini leads the data engineer sample at 8.

## Consequences

- **A company with a career front shows fewer postings on page 1.** At `per_company` 3 it shows two
  postings and their twins (4 rows), where before it showed up to three groups of any size.
- **Per-country and per-city postings of one requisition are separate rows.** On a page each costs a
  place under the cap, and in a requirements sample each counts, up to `per_company`. Their
  descriptions are often the same boilerplate, so a sample can count one company's text up to 8
  times, where it used to count it once. ADR-0352's cap bounds this.
- **A copy on two Boards that states its place in unrelated words is not grouped.** For example,
  "Remote" on one Board and "Austin, TX" on the other. Both rows stay listed and count toward the
  cap.
- **The one-per-Board pairing can pair the wrong twins.** When one company has several postings of
  one title in one city, a Radancy row may be listed under the wrong Workday requisition. The count
  of postings is still right, and every id and link is shown.
- **Agent contract 24.** `/search?per_company` answers a different page, and `/requirements` counts
  more postings.
- **Eval task t42** needs at least 4 companies on the first page of the s04 query, named in the
  answer (`page_companies`). Its `requires` (ADR-0366's mechanism) retires it while the Space's
  uncapped ranking of that query already names 4 companies on its first page.
  - Its recorded `/search` reply was made by this change's `spread` from the deployed Space's own
    uncapped 2,000-row window, because the Space answered by the old rule until this change
    deployed. The next re-record reads the deployed rule.
- **Eval task t32** had the Eversource pair behind it close. It now reads Schwan's: one posting in
  Hopkins, Minnesota, on its ADP Recruiting site ("Schwan's Company") and its Radancy front
  ("Schwan’s Company"). It carries a `requires` on those two ids.
