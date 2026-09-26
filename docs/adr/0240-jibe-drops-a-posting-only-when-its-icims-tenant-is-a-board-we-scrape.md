# ADR-0240: Jibe drops a posting only when its iCIMS tenant is a Board we scrape

**Status:** accepted · **Date:** 2026-09-26 · **Amends:** [ADR-0189](0189-a-jibe-board-is-a-client-read-under-its-own-robots-rules.md) decisions 3 and 4 · **Relates to:** [ADR-0191](0191-one-module-answers-whether-a-board-is-scraped.md) (the Scrapable Board list)

## Context

ADR-0189 decision 3 drops a Jibe posting when its `apply_url` names an iCIMS tenant that the iCIMS
scraper can read, so the posting is not served twice. "Can read" meant "the tenant's robots.txt
allows `/sitemap.xml`". RFC 9309 reads a 4xx robots.txt as "no rule applies", so a tenant whose
robots.txt returned 404 counted as readable.

A tenant that has left iCIMS answers 404 for robots.txt and for sitemap.xml, and the icims ledger
records it as `dead`. Nothing scrapes it. `ascensionjobs1-ascension.icims.com` and
`us-jobs-conduent.icims.com` both behave this way. The seven-run log review of 2026-09-26 found 97
Jibe clients that read 0 Jobs because of this gate. On 2026-09-26 their listings held 9,252
postings that no scraper served, among them ascension's 3,172, conduent's 1,243, mhm-services'
1,087 and riteaid's 983.

The same review found the opposite waste. About 290 clients have every posting on a live iCIMS
Board that we scrape. They are paced at 5 s a page to read 0 Jobs, for 2.7–3.3k Board-seconds a
run. commonspirit alone took 515 s a run, and on one run it lengthened a shard's wall time by
339 s. Nothing in the log said why: the line naming the drop was at DEBUG.

## Decision

1. **A posting is dropped only when its iCIMS tenant is a Scrapable iCIMS Board.** The test is
   whether the tenant host is in `scrapable_boards.load(min_jobs=0)`'s iCIMS Boards, matched on
   the same lowercased identity the election uses. The committed ledger is read once per process.
   Any other tenant keeps its postings on Jibe: a dead one, one not in the ledger, or one that is
   parked or excluded. Jibe no longer makes any request to an iCIMS host, so the per-tenant
   robots.txt fetch, its cache, its lock and its 1 s spacing are gone.
   - The ledger was chosen over "robots.txt allows the sitemap, and the tenant is in the ledger".
     The robots check added nothing we could measure. All 80 of 80 randomly sampled Scrapable
     iCIMS tenants allowed `/sitemap.xml`. A tenant that disallows it answers 403 there
     (`icims.py`), and the prober marks such a tenant dead.
   - A Scrapable Board that is not in this run's Slice still counts as covered. Its postings are
     served from its own last scrape, and eviction's unit is scrapes of that Board.
2. **A client whose every posting is on a Scrapable iCIMS Board is parked.** This extends decision
   4 from Workday and Oracle to iCIMS. `scripts/validate/jibe_icims_covered_clients.py` walks each
   Scrapable client's whole listing with the scraper and compares each posting's apply host against
   the same set. A client is parked only when it has postings and none of them is elsewhere. On
   2026-09-26, 288 of 1,121 clients met that rule (73,027 postings). Thirteen of the review's
   candidates, chosen from page 1 alone, were kept because the whole walk found postings elsewhere
   (emory 38 of 1,903, prideindustries 161 of 307, peraton 1 of 1,559).
3. **The per-Board drop line is INFO.** A Board that runs slowly for 0 Jobs now says why in the log.

## Consequences

- The 97 clients serve their 9,252 postings again. Before and after, live on 2026-09-26: conduent,
  mhm-services and riteaid went from 0 Jobs to 1,243, 1,087 and 983. The covered clients
  adastragrp and arrowtransportation read 0 both times, with 7 and 25 dropped.
- The gate follows the ledger, not the live host. When an iCIMS Board dies in the ledger, its Jibe
  postings come back on the next run. When a dead one is re-landed live, they are dropped again. A
  tenant that is live in the ledger but has lately stopped answering drops its Jibe postings until
  the prober catches up. That window was also open under the robots gate, which read a 404 as
  covered.
- A parked client stays parked when its postings move off iCIMS. Re-run the script after landing
  jibe or icims rows. It reads only the clients still scraped, so bringing a parked client back
  means taking it out of `PARKED_BOARDS` and running the script again.
- Scrape shards now load the whole Scrapable list once per process that meets a Jibe Board with
  iCIMS postings (~1.7 s).
