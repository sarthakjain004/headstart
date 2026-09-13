# Taleo Business Edition surface measurement

Measured 2026-09-13 against public TBE boards, before adding
`headstart.scrapers.taleo_be.TaleoBEScraper`.

## Scope

This is **Taleo Business Edition** only: its public URLs are
`https://{shard}.tbe.taleo.net/{instance}/ats/careers/v2/searchResults?org={ORG}&cws={N}`.
It does not claim support for Taleo Enterprise's separate Career Section URLs.

## Listing and pagination

Six first-page requests across `phe`, `phf`, `phg`, and `tre` all returned HTML 200.  Listings
render ten postings per page (twenty `viewRequisition` strings because each posting has title and
View links), and end with a relative `a.jscroll-next` URL.  On ICANN, a page-two request with the
cookie set by page one returned ten new ids; the same URL without the cookie returned HTTP 500.
The scraper consequently uses the shared session for a serial listing walk and stops only when
there is no next link.  It has a 1,000-page safety cap and marks a loop/cap truncated.

## Detail fields

Direct detail URLs work without the listing cookie.  They do **not** contain JobPosting JSON-LD.
They do expose:

| HeadStart input | TBE HTML surface |
| --- | --- |
| title, department, location | listing card and detail sidebar |
| description | `name="cwsJobDescription"` rich-text container |
| employment type, posted date | optional labelled detail fields |
| salary | optional labelled pay range or paired `Targeted Base Salary Low/High` fields |

ICANN's "Director, Enterprise Architecture" detail page supplied a rich description and
`Targeted Base Salary Low: 142,000 + 20% + Benefits` / `High: 197,400 + 20% + Benefits`.
The scraper keeps this as raw `Job.salary`; `doc_prep.to_meta()` remains the sole caller of
`salary.extract()` and `remote.extract()`.  It does not invent currency or a period where TBE
does not state one.

## Rate and liveness controls

Twenty distinct ICANN detail requests at concurrency 16 all returned 200.  This rules out an
immediate 16-way wall on that shard, not a provider-wide rate-limit claim; the scraper therefore
uses 16 as a bounded detail width and relies on the shared retry seam.

The upstream 167-URL roster plus a complete 318-page Wayback sweep produced 1,780 canonical
candidate URLs. At a 16-worker liveness cap, 539 were live, 6 dead and 1,235 unknown. The
unknown tail is not an egress rate limit: direct and WARP requests returned the same TBE
maintenance/soft-404 outcomes. It stays retryable rather than being falsely called dead.

## Upstream comparison

The reference scraper at
<https://github.com/kalil0321/ats-scrapers/blob/main/src/ats_scrapers/scrapers/taleo.py>
correctly identifies the TBE URL family, but it fetches only the first listing page and expects
detail JSON-LD.  Neither behavior holds on the measured boards, so HeadStart implements the
cookie-backed page walk and native HTML detail parsing instead.
