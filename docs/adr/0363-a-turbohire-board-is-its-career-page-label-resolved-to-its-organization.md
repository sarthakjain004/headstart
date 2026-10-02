# ADR-0363: A TurboHire Board is its career-page label, resolved to its organization, and lands active

**Status:** accepted · **Date:** 2026-09-30 · **Relates to:** [ADR-0001](0001-per-ats-slug-derivation.md) (a scraper's slug is its own to define), [ADR-0012](0012-liveness-ledger.md) (the ledger is the scrape list), [ADR-0048](0048-skip-details-we-already-hold.md) (the held-description skip, not taken here), [ADR-0114](0114-a-board-states-its-company-name-in-its-page-title.md) (company names), [ADR-0157](0157-a-scrapers-job-url-is-declared-once-not-authored-three-times.md) (`url_shape`), [ADR-0158](0158-jazzhr-and-jobvite-are-worth-their-storage.md) (the cost bar), [ADR-0166](0166-gate-the-detail-pass-on-the-tech-filter.md) (the tech gate), [ADR-0189](0189-a-jibe-board-is-a-client-read-under-its-own-robots-rules.md) (robots.txt per host) · **Issue:** #976

## Context

TurboHire sat on CLAUDE.md's "To build" list with a token flow verified in July. It named three
companies: Flipkart, Ola and Cleartrip. Discovery had already found about 100 `*.turbohire.co`
labels (Common Crawl and Wayback). Issue #976 left four questions open:

- how a label resolves to the organization GUID the API keys on, without a browser;
- the token's lifetime;
- whether the listing pages, and whether a detail fetch is needed;
- Cleartrip's Board.

`docs/turbohire/2026-09-30_career-api-measurement.md` answers them. It measured every label in the
pool twice. The landing census read all 114 labels after fresh Wayback and Common Crawl sweeps:
74 organizations, 1,341 postings, and a detail for each posting.

## Decision

1. **A Board is the career-page subdomain label** (`flipkart` in `flipkart.turbohire.co`), stored
   bare in the ledger's `tenant` column with `https://{label}.turbohire.co` as its `url`.
   - The label is what discovery yields, the page's host, and the job link's host.
   - The API resolves it: `GET /api/publicorganizations?accountName={label}` returns the org
     `OrgID` and `OrgName`, case-insensitively.
   - `CareerPageSubdomain` equalled the label on 65 of 65 organizations (first census), and the 75
     live labels of the ledger resolve to 75 distinct orgs, so there are no alias labels to bury.
   - The GUID was rejected as the key: it is opaque, discovery never yields it, and a GUID slug
     would display as the company name until a name source existed.
2. **Four calls, all to `api.turbohire.co`, all the SPA's own:**
   - an anonymous token (`/api/token/noauth`, Referer on a `*.turbohire.co` host), read once per
     Board and good for 3,600 s;
   - the organization lookup;
   - one `POST /api/careerpagev2/filteredjobs?orgId=…&pageType=0`, which returns the whole Board
     (`Total` equalled the rows served on 65 of 65 in the first census);
   - a detail per posting.

   `pageType=1` and `pageType=2` (the internal and referral pages; 7,556 and 71 Flipkart
   postings) are never asked.
3. **Dead versus empty is the organization lookup.** Every label serves the SPA with 200, so the
   page settles nothing. The lookup answers 404 with an empty body for a label no organization
   holds (40 of the 115 labels in the ledger).
   - `p_turbohire` reads DEAD there, and LIVE with the listing's row count otherwise.
   - The scraper raises the same 404 as a gone Board.
4. **The detail pass runs, gated, and never skips a held description.**
   - The listing cuts the description at 500 characters, blanks the salary and hides the type;
     the detail carries all three.
   - The detail's `JobTitle` and `Department` equalled the listing's on 1,341 of 1,341 postings (the landing census),
     so the ADR-0166 gate is **exact**.
   - ADR-0048's skip is not taken: the detail is also the only source of `employment_type`,
     `salary`, and on 441 rows `posted_at`.
5. **Fields follow what the job page shows:**
   - **Salary:** the detail's `CTCInfo`, unless `HiddenFrom` names JobSeekers.
     `salary.from_field`'s floor refuses the lakh-typed "12"–"16" rupee figures the page itself
     shows verbatim.
   - **Remote:** read from the "Remote Job" place in the location.
   - **Description:** the page's own concatenation of the description with its Roles &
     Responsibilities and Eligibility sections.
   - **Company:** `OrgName` from the organization record, through `adopt_company`.
6. **It lands active.** A run fetches about 10.8 MB for about 327 tech Jobs, roughly 33 KB per tech
   Job, against ADR-0158's accepted bar of about 2 MB. Tech share is 25.1% (337 of 1,341).
7. **Landing:**
   - **Cleartrip** has no Board of its own: `careers.cleartrip.com` links only to Flipkart's
     career page, and the `cleartrip` label is no organization. The `flipkart` Board serves it.
   - The vendor's two demo organizations (`thdemo`, `democareers`, "TurboHire - Demo Account")
     are in `EXCLUDED_BOARDS`.
   - **Cipla**'s two pages are in `PARKED_BOARDS`. On its held SuccessFactors Board
     `careers.cipla.com` sit 17 of `cipla`'s 18 distinct titles and 2 of `ciplasouthafrica`'s 3,
     so each posting would be served twice. The same comparison on seven other employers with
     Boards elsewhere found 0 to 2 shared titles, and those are landed. CLAUDE.md's landing rules
     carry the check.

## Alternatives considered

- **Read the GUID off the career page's HTML.** The server-rendered page carries a
  `company/{OrgID}/` logo URL, but only for an organization that uploaded a logo. The API lookup
  answers for every organization and settles dead in the same request.
- **Skip the detail pass and serve the 500-character teaser.** That would save ~7.5 MB a run, but
  it serves a truncated description on 877 of 1,268 postings and no salary or type anywhere.
- **Honour the tenant hosts' robots.txt for the API.** `*.turbohire.co` serves
  `User-agent: * Disallow: /` (Googlebot allowed on `/job/`). The API host serves none (404).
  Under ADR-0189 robots.txt binds only the host that serves it, and nothing is requested from a
  tenant host.

## Consequences

- The ledger lands 115 rows: 75 live (54 hiring) and 40 dead.
  - They are the census's 114 labels plus `careers`, the vendor's own hiring organization (0
    postings), which no sweep found.
  - With the two demo organizations excluded and the two Cipla pages parked, 71 are Scrapable.
- Discovery is wired: `ATS_HOSTS` in `wayback_feeder.py` and `ATS_PATTERNS` in `cc_miner.py`. The
  organization lookup is a 0.1-second oracle for guessed labels, which a later gap search can use.
- 202 of 1,268 postings state no description on any surface (27 only as an attached PDF or DOCX),
  so about a fifth of TurboHire's tech Jobs are embedded from their title alone.
- An agency Board (`act`, `elementshrs`, `i4consulting`) is served under the agency's name,
  though its postings name the client employer in `ClientName`.
- Live-row checks in `verify-search-filters` run only once the pipeline has scraped these Boards.
