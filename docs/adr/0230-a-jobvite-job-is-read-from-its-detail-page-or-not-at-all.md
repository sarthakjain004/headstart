# ADR-0230: A Jobvite Job is read from its detail page or not at all

**Status:** accepted · **Date:** 2026-09-25 · **Relates to:**
[ADR-0053](0053-scope-eviction-on-scrape-outcome.md) (the truncation
mark a lost page sets), [ADR-0050](0050-persist-descriptions-across-runs.md) ·
Closes [#557](https://github.com/sarthakjain004/headstart/issues/557)

## Context

Jobvite's listing yields ids and nothing else, so when a detail page cannot be read the Job is
dropped and the Board is marked truncated. ADR-0053's exclusion has no drain, so a Board that loses
a page every run leaves eviction scope for good. #557 asked whether to emit a listing-derived Job
(title and location parsed off the listing row, as `kalil0321/ats-scrapers` does) when the detail
is lost.

Measured on 2026-09-25, before deciding:

* **Who actually loses pages.** Three consecutive pipeline runs (`36128528821`, `36133540276`,
  `36138807277`) lost detail pages on exactly three Boards and on no others: `mini-circuits-review`
  (54 of 54), `wedgewood` (1 of 1) and `blackbear` (129 of 129). Every loss was labelled
  `no posting on a 200`; none was a transport failure.
* **Why.** Each Board has its own cause, and none is a page we cannot read.
  * `mini-circuits-review` writes `<h2 class="jv-header u-text-left">`; the fallback parser matched
    the class exactly.
  * `wedgewood` moved its career site onto its own domain: the job page 302s there, the redirect
    was followed, and the destination renders no posting. The same page with `?nl=1` (the page
    Jobvite's embed widget frames) answers 200 with JSON-LD.
  * `blackbear` is a vendor sales demo. Its postings name a different company each
    ("Chromalloy", "CFO Services", "Blackbear Manufacturing"), one says "DO NOT EDIT - Steve T -
    Using for branded demo", and every page is an unrendered template.
* **What a listing fallback would have bought.** Nothing on these three: two are fixed by reading
  the detail correctly, and the third should not be served at all. It would also re-open the
  listing's five row templates, which returned zero rows, silently, on 19%, 15% and 1.4% of
  Boards (module docstring).

## Decision

A Jobvite Job is built from its detail page or not at all. There is no listing-derived fallback. A
lost page stays a dropped Job and a truncation mark, which is the honest record, and the work goes
into reading the page:

* The detail is requested as `/job/{id}?nl=1` with redirects refused, so a tenant that moved its
  career site is still read, and a page that still moves is labelled `HTTP 302` instead of being
  parsed as empty. The link a user follows stays the plain job page.
* The fallback heading match takes `jv-header` as one class among others.
* `jobvite:blackbear` joins `config.EXCLUDED_BOARDS` with the other vendor demos.

Measured across the whole live pool before shipping: every live Board's listing, up to three ids
each, fetched both ways (today's request and the `?nl=1` one) and parsed both ways. The figures
are in the PR that lands this.

## Consequences

* A Board that starts losing pages is a parser or surface defect to find, not a gap to paper over,
  and it shows as a scope-excluded Board with a labelled cause on the merge log.
* `?nl=1` doubles as the only route to the four tenants the module docstring records as having
  moved their *listing* onto their own domain. Reading their listings that way is not done here.
