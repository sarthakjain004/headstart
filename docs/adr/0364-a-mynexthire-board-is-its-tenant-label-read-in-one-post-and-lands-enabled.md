# ADR-0364: A MyNextHire Board is its tenant label, read in one POST, and lands enabled

**Status:** accepted · **Date:** 2026-09-30 · **Relates to:** [ADR-0012](0012-liveness-ledger.md) (the ledger is the scrape list), [ADR-0050](0050-persist-descriptions-across-runs.md) (`has_detail_pass`), [ADR-0114](0114-a-board-states-its-company-name-in-its-page-title.md) and [ADR-0212](0212-a-board-is-named-by-a-curated-stated-or-humanised-name-never-its-slug.md) (company names), [ADR-0157](0157-a-scrapers-job-url-is-declared-once-not-authored-three-times.md) (`url_shape`), [ADR-0158](0158-jazzhr-and-jobvite-are-worth-their-storage.md) (the storage bar), [ADR-0162](0162-a-gone-verdict-expires-quarantine-parole.md) (gone Boards)

## Context

Swiggy and ShareChat, both absent from the ledgers, hire through MyNextHire (issue #971, which
calls the vendor Nexthire; the product is Smaclify Technologies'). Each customer is a label on
`{label}.mynexthire.com`, and the careers page reads its openings from one JSON call. Three public
implementations existed; the measurement
(`docs/mynexthire/2026-09-30_reqlist-measurement.md`: 108 labels, 35 live, 630 postings, a
45,465-label sieve) confirmed their listing call and killed two of their other claims — that a category filter
must be iterated, and that the link `/employer/jobs/{reqId}` opens a posting.

## Decision

**The Board is the tenant label**, lower-case, on `mynexthire.com`; `slug_from` keeps the
default and the registry key is `mynexthire`, the stem the existing candidate pool already used
(the issue's `nexthire` names no file or host). `{label}.careers.mynexthire.io` is the same
tenant's newer front, folded onto the label by discovery, never a second Board.

**One request per Board: `POST /employer/careers/reqlist/get` with `{"source": "careers"}`.** It
returns every open requisition with its full plain-text description, no pagination, so there is
no detail pass (`has_detail_pass = False`) and no tech gate to place. The per-posting record on
`{label}.prod.us1.mynexthire.io` adds only skills and an HTML copy of the same text.

**Dead versus empty is the refusal's `errorMessage`.** DNS is a wildcard, so every label reaches
the application: 417 "Invalid company short name" is no tenant and 402 "… subscription … has
expired." a lapsed one — both DEAD in the prober and raised as gone (ADR-0162) by the scraper,
through one shared function, `mynexthire.departed`. 200 with a null list is a live Board with
nothing open. Anything else (a 417 about the request, a 500) is UNKNOWN or a plain failure.

**The job link is the careers page opened on the posting**:
`https://{label}.mynexthire.com/employer/jobs/careers?src=careers&p={base64 page context}`, the
context the page's own `getEncodedJobboardLink` builds. It rendered the posting in a browser on
three tenants; a closed id renders "Oops! Something went wrong!" at HTTP 200, so a dead link is not
visible to a status check.

**Fields.** `department` is `careerStream`, else `buName` where the tenant left the stream "NA":
`careerStream` is often a tenant-wide default and adds creep, but it kept every tech posting
`buName` kept plus 38 more (the gate is recall-biased, CLAUDE.md). `employment_type` maps the
twelve measured values, because raw `third_party_consultant` reads part-time in the filter.
`experience` is the stated bounds widened to whole years. No salary: the pay band is 0.0 on 630
of 630. `company` is the client record's `clientName` (35 of 35 live tenants), one extra GET for a Board with
postings; the vendor's own names are refused as aliases.

**Vendor tenants are landed and excluded**: `consultant` (the vendor's test tenant), `try` (its
demo, 164 template postings), `staging` (328 postings from 2020-2022), `mars` (a trial whose
postings are test requisitions) and `prodindefault` (the default tenant) are live rows in the
ledger and in `EXCLUDED_BOARDS`, so a re-probe keeps them visible without serving them. Together
they list 527 postings, near as many as the 29 customer Boards' 622.

**Enabled on arrival.** ADR-0158's bar is ~2 MB of fetched bytes per tech Job. MyNextHire's
listings are 3,133,880 bytes for the 622 postings of the 29 customer Boards with postings, and the
tech gate keeps 293 of them: **~10.7 KB per tech Job**, about 190x under the bar (~14 KB with the
~34 KB client record per Board). The yield is concentrated — aziro holds 156 of the 293, Swiggy
yields 5 of 83 — but no measure of cost argues for holding it back.

## Alternatives considered

- **`buName` as the department.** More often a real department, but it lost 38 of 202 tech
  postings the stream kept, and on aziro it names the client, not the team.
- **The issue's link, `/employer/jobs?src=careers&p=…`.** It is the iframe holder page: rendered
  bare it shows only the footer. Swiggy's own wrapper (`careers.swiggy.com/#/careers`) would need
  a per-tenant host the listing does not carry.
- **A liveness probe on the job page.** Every page is the same SPA shell at 200, so it can tell
  nothing; the listing already says which ids are open.

## Consequences

- The sieve and three years of Common Crawl and Wayback found no customer beyond the ones the
  ledger holds; the vendor's client ids stop near 1165, so the population is small and mostly
  lapsed. New customers will surface through the careers-page fingerprinter, which now knows both
  fronts.
- A closed posting cannot be caught by the filter harness's status probe; eviction relies on the
  listing, as for every no-detail ATS.
- Three tenants answer 500 on every call (obmajesco, obquantinsti, spicinemas) and sit UNKNOWN,
  re-probed each pass.
