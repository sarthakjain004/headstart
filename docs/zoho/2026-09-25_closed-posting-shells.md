# Zoho: a closed posting's detail page, and the shell that only looks like one

Measured 2026-09-25 (IST) from a laptop, through `registry.get_scraper("zoho", host)` and plain
`requests`. Follows finding 3 of `docs/pipeline/2026-09-24_five-run-log-review.md`.

## A listed posting can be closed

Zoho's careers page keeps listing postings whose own detail page says they are gone. Until this
change `zoho.py` labelled that loss `posting explicitly unavailable` and still built the Job from
the listing, so the index served a dead link. CI logged 1,247–1,953 of these per run on 23–31
Boards.

Re-probed through the real scraper:

| Board | Listed | Detail pages with a record | `posting explicitly unavailable` |
| --- | --- | --- | --- |
| `harrisonconsultingsolutions.zohorecruit.com` | 740 | 473 | 267 |
| `resourceit.zohorecruit.com` (pt_BR) | 407 | 0 | 407 |

The served link (`/jobs/Careers/{id}/{title}?source=CareerSite`) answers the same shell as the
detail fetch (4 of 4 checked on each Board), so the link a user clicks is dead too. On Harrison the
closed postings sampled were opened 2015–2023 and the readable ones in September 2026. Resourceit
lists 407 postings, and every one is closed.

The scraper now drops these postings. The Board is not marked truncated, because a closure is a
real absence: `sync` sees the id missing and ADR-0083 evicts it on the second consecutive miss.

## The verdict is in the Board's language

The shell is a `sorry-block` page with one `<h4>` verdict, rendered in the career site's language.
A request for an id the tenant does not have (`/jobs/Careers/1`) returns the same shell. On
Harrison and Resourceit it matched the closed-posting text exactly. Swept across 5,000 live
ledger hosts, it turned up 18 languages. All 18 are in `_UNAVAILABLE_VERDICTS`. Only English and
Portuguese were also seen on a listed posting. The other 16 come from the unknown-id probe, which
serves the same template. The oldest 15 listed postings on each of 8 non-English `.eu` Boards (fr,
es, nl, it, de, pt-PT, sv, pl) were all readable pages, so no non-English listed closure was
available to check. Until this change those verdicts fell into `no jobs blob on the page`.

## `this page is currently unavailable.` is a throttle, not a verdict

About 1,000 requests into the sweep, every `*.zohorecruit.com` host began answering
`302 → /html/portal.html`. That page is a different English-only shell, titled
`Page not found`, with the `<h4>` `this page is currently unavailable.` It was served for a posting
that had returned its full record minutes earlier. `.in`, `.eu` and the other data centres kept
answering normally (the shell hit 2,812 of 3,674 `.com` hosts and 0 of 1,326 others). About 7
minutes later the block had lifted.

The scraper follows the redirect and reads a 200 with no jobs blob, so it labels the page
`no jobs blob on the page`. It is deliberately **not** a closure verdict, and a test pins that.
This fits finding 4 of the run-log review: that loss occurs only in CI and, per run, lands on
3,191–5,965 postings across 150–264 Boards. A shard that concentrates detail fetches on the `.com`
data centre from one egress IP would produce it. That remains a hypothesis until a CI log records
the page it got.
