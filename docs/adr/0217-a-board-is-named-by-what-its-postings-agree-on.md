# ADR-0217: A Board is named by what its pages or postings agree on, where no Board page names it

**Status:** accepted · **Date:** 2026-09-25 · **Relates to:** [ADR-0114](0114-a-board-states-its-company-name-in-its-page-title.md)
(board-page titles; this extends its sources and reverses its blanket refusal of per-posting names
for the ATSes measured below), ADR-0212 (the core naming policy: curated → stated → humanised)

## Context

The served table on 2026-09-24 showed a slug, host or Board key as `company` on 14,439 Boards.
Seven ATSes on that list have a name source that ADR-0114 did not use:

- **SuccessFactors** has no usable Board title, but every RMK job page ends its `<title>` with
  "| {Company}". The Detail pass already fetches those pages.
- **iCIMS and Jibe** have a Board title on most Boards, and also a per-posting field:
  iCIMS's JSON-LD `hiringOrganization`, and Jibe's `hiring_organization`.
- **Phenom** Boards whose title is marketing copy still state `og:site_name`.
- **Oracle** has a default-site title at the Candidate Experience root.
- **Taleo Business Edition** has an RSS channel title.

ADR-0114 refused per-posting names outright, on Workday's evidence: its `hiringOrganization` is a
per-posting legal entity that varies within one Board. That evidence is Workday's. Measured per
ATS, the field is site-level on most Boards elsewhere:

- **SuccessFactors:** each of the 30 largest Boards stated one name on every page sampled.
- **iCIMS:** 42 of 52 Boards stated one name on every posting; 4 varied (subsidiaries); 6 stated
  only `UNAVAILABLE`.
- **Jibe:** 22 of 28 Boards stated one name on every row, and `reyesholdings` split five ways (43%
  top share).

## Decision

1. **A per-posting name names a Board only when the Board's postings agree on it.** The shared
   helper `company_name.agreed_name(names, share)` returns the modal name when at least `share` of
   the postings that state one agree. Each ATS sets its own floor:
   - SuccessFactors: no floor (the name is site-level by construction).
   - iCIMS: 90%.
   - Jibe: 85%.
   The result still passes through `from_title`'s guards.
2. **Brand before legal name.** The user decided this. Where a Board page yields a name (iCIMS's
   "Job Listings at UWM", Jibe's title, Phenom's title), it wins. The per-posting or `og:site_name`
   name is the fallback ("United Wholesale Mortgage").
3. **A name is stated during the fetch.** It is set in `fetch_raw` or `resolve_company`, never in
   `parse`, so ADR-0212's settle step sees it as stated and keeps it.
4. **A field goes through `from_field`, a page string through `from_title`.** Phenom's
   `og:site_name`, iCIMS's `hiringOrganization` and Jibe's `hiring_organization` are values the
   company typed, so they take ADR-0212's `from_field` guards under the ATS's own vendor aliases.
   Jibe's `customer0` states "iCIMS Talent Acquisition": iCIMS hiring on its own client, which the
   alias set (a bare "iCIMS") does not refuse. A SuccessFactors job-title suffix is page text, so
   it takes `from_title` and a `successfactors` pattern.
5. **When in doubt, refuse.** A doubtful name falls to ADR-0212's curated map or humanised
   fallback rather than being served. Examples:
   - SuccessFactors instance ids and filler: "PMIProd", "erstegro01P2", "Apply now!", "Group".
   - iCIMS office or team labels: "Headquarters", "RS&H Talent Acquisition".
   - Oracle template leftovers: "Candidate Experience site", "Jobs onsemi".
   - No bare catch-all on Taleo Enterprise.

## Consequences

Measured live against the 2026-09-24 served bad-boards list (Boards named / rows named):

| ATS | source | Boards | rows |
| --- | --- | --- | --- |
| successfactors | job-page title suffix, else microdata | 1,210 / 1,469 | 36,554 / 45,120 |
| phenom | title, else `og:site_name` | 20 / 22 | 2,284 / 2,287 |
| icims | listing title, else `hiringOrganization` ≥ 90% | 1,288 / 1,308 | 20,325 / 20,523 |
| jibe | title (lookahead fixed), else `hiring_organization` ≥ 85% | 24 / 28 | 1,290 / 1,528 |
| oracle | Candidate Experience root title | 724 / 796 | 24,525 / 27,878 |
| taleo_enterprise | two more wrappers; logo junk refused | 5 / 43 | 279 / 799 |
| taleo_be | RSS channel title | 20 / 22 | 199 / 204 |

The residue goes to the curated map. `company` is a Fact that `update_meta` re-observes, so served
rows take the new name on their Board's next scrape.

**Two refusals cost real names, and that cost is accepted.**

- The first SuccessFactors design refused a name equal to the tenant's company id, and a full
  census showed that costs real names: "Bechtel", "Amtrak" and "SES" are their tenants' ids. That
  test was replaced by instance-id shapes.
- The 85% Jibe floor leaves `kmbskonicaminolta` (78.8% on 2026-09-25) to the fallback.

**One refusal is loosened, on one ATS.** ADR-0114's `_PAGE_LABEL` refuses a trailing "Careers"
rather than strip it, because stripping turns "Destination Careers" into "Destination". A
SuccessFactors job-page suffix is different: it is the site's own brand line, and the 13 Boards
that end it in "Careers" on 2026-09-24 are each a real employer's careers brand ("Ingersoll Rand
Careers", "GKN Aerospace Careers", "Air India Careers", "KPMG Singapore Careers"), checked by
hand. So the `successfactors` pattern strips it; every other ATS still refuses.

**This ADR records all seven ATSes, which land in three PRs** (SuccessFactors/Phenom,
iCIMS/Jibe, Oracle/Taleo). The iCIMS/Jibe PR carries it too, byte-identical, so either can land
first.
