# ADR-0218: An inactive Trakstar account is gone

**Status:** accepted · **Date:** 2026-09-25 · **Relates to:**
[ADR-0058](0058-consecutive-gone-quarantine.md) (the gone quarantine),
[ADR-0200](0200-a-board-scraped-empty-is-in-the-eviction-scope.md) (an empty Board evicts),
[ADR-0206](0206-prune-evicts-a-board-parole-reconfirmed-gone.md) (prune evicts a reconfirmed gone Board),
[ADR-0212](0212-a-board-is-named-by-a-curated-stated-or-humanised-name-never-its-slug.md) (naming)

## Context

Reading Trakstar's careers page for the company name (ADR-0212's work) turned up a second fact on
the same page. 64 of 200 sampled Boards that served a slug on 2026-09-24 answer "Inactive account.
This employer is no longer using Trakstar Hire to collect applications" at HTTP 200. The
`jsapi.recruiterbox.com` listing still returns their openings (`nowfloats1`: 395), and every
`hosted_url` it names 404s (25 of 25 Boards checked, two postings each). So the scrape was
serving live-looking Jobs whose links are dead. Across all 968 affected Boards, 254 are inactive
(1,270 rows).

`check_liveness.p_trakstar` counts job cards on the same page. It reads zero and records these
Boards as live.

## Decision

`TrakstarScraper.fetch_raw` reads the careers page before the API. If the page carries the
inactive-account notice, it raises `HTTP Error 410`, in the shape `board_failures.is_gone`
matches. The Board then earns an ADR-0058 strike each run it is scraped, and after five it is
quarantined.

## Rejected

- **Return `[]`.** ADR-0200 puts a clean empty scrape in the eviction scope, so the rows would
  drain after two runs. But no HTTP status backs the signal. The call was made to never evict
  live postings on a page's say-so, and to leave that to the gone machinery.
- **Keep serving the API's openings.** Every link they carry is dead.

## Consequences

- An inactive Board's rows stay served until the gone machinery removes them: quarantine stops
  scraping it, and once parole re-confirms the verdict, ADR-0206's prune evicts its rows.
- The liveness probe still calls these Boards live. Fixing `p_trakstar` to read the same notice
  is left to a ledger refresh.
