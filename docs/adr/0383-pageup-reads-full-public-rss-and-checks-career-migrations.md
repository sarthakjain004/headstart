# PageUp reads full public RSS and checks career migrations

**Status:** accepted · **Date:** 2026-10-03 · **Relates to:** 0012, 0111, 0158, 0196

## Context

PageUp's public HTML repeats desktop/mobile links and paginates large Boards. Its RSS is
richer: 17 measured Boards returned 4,354 records, including every HTML-listed id and 19
additional public records. The largest feed contained 2,487 records. All carried full
`job:description`; ordinary RSS `description` is only a teaser. See [the measurement](../pageup/2026-10-03_public-api-measurement.md).

## Decision

The slug retains account, channel and language. Read an unfiltered public `/listing/` and
`/rss`, parse the PageUp namespace, deduplicate native job ids and repeated categories, and
retain every location. Published RSS records, including expression-of-interest records, enter
the normal downstream filters; the smaller HTML list is not an allowlist. No detail pass or
pre-detail gate is needed. A title-only gate would lose 42 of the sample's 261 tech matches.
Partial Remote is hybrid (`remote=None`). `pubDate` is used as the source's publication date;
no crawl-time date is invented.

The same account can have subset channels and equivalent locale views. The repeatable
`pageup_subset_boards.py` validator aliases only complete, nonempty contained posting sets
within an account; numeric-id coincidence across accounts proves nothing. Re-run it whenever
the PageUp ledger changes. The initial alias set changes no previously supported ATS identity.

A retained RSS does not prove a career site still works. Three measured migrated Boards
returned nonempty RSS while their Job links redirected to generic external searches. Check
the public Board too, and on an external migration verify a real feed Job link. Unexplained
redirects/challenges remain unreadable; a measured generic-search migration is gone. Public
channel selection respects the site's robots exclusions. A stale page title alone is not
an internal-audience test: WWU's employer-endorsed public Board says Internal Job Openings.

Names come from measured title wrappers or cited curated names. Generic Jobs/Recent Jobs
titles are not employers, and `businessLayer1` is not universally a company name.

The ATS lands enabled: 56.4 MB of decoded feed data for 261 tech matches is about 0.216 MB per
tech Job, below ADR-0158's comparison point. This is a sample, not a global forecast.

## Alternatives and consequences

Per-job HTML adds requests and can miss RSS-only public records. A feed-only liveness probe
would activate migrated sites with unusable links. Keeping locale coordinates until equality
is measured avoids assuming every deployment's languages expose identical collections.
