# ADR-0182: A ClearCompany Board is its HRM Direct feed, not a clearcompany.com surface

**Status:** accepted · **Date:** 2026-09-23 · **Relates to:** [ADR-0012](0012-liveness-ledger.md) (the ledger is the scrape list), [ADR-0048](0048-skip-details-we-already-hold.md) (skipping a held detail — deliberately not applied here), [ADR-0111](0111-duplicate-boards-resolve-the-board-surface.md) (the alias ledger the duplicate label goes in), [ADR-0158](0158-jazzhr-and-jobvite-are-worth-their-storage.md) (the storage bar), [ADR-0166](0166-gate-the-detail-pass-on-the-tech-filter.md) (the pre-detail tech gate)

## Context

ClearCompany was named by the careers-page fingerprinter (`SUB + clearcompany\.com`) and had no
scraper, no pool and no upstream seed list. Two third-party implementations described it, and
they contradicted each other:

- `Francis1998/agentic-career-search` described per-tenant HTML boards on `{slug}.clearcompany.com`
  with job anchors such as `/careers/job/{id}` and `/position/{id}`.
- `ever-jobs/ever-jobs` (spec 300) described one shared JSON feed,
  `GET careers-page.clearcompany.com/api/v1/careers/jobs` with header `API-ShortName: {slug}`,
  embedding each posting's full HTML description.

Both were treated as hypotheses and measured on 2026-09-23 over 244 tenant labels from one Common
Crawl index — 176 hiring Boards, 6,377 postings
(`docs/clearcompany/2026-09-23_careers-surfaces-measurement.md`; the probe scripts and raw
captures are kept locally, not committed).

**Neither surface is the Board.** `{slug}.clearcompany.com/careers` is a login SPA, byte-identical
for a real tenant and an invented one. Following a ClearCompany job link in real Chrome showed where
the public posting lives: the page calls `/api/v1/careers/jobs/{guid}/posting-url`, which answers
`{slug}.hrmdirect.com/employment/job-opening.php?req=N`, and the browser lands there. **HRM Direct**
— the career-site product ClearCompany owns — is the public Board, and the same subdomain label
keys both hosts.

## Decision

**A ClearCompany Board is `{slug}.hrmdirect.com`, read through `/employment/xml.php`.** The ATS key
stays `clearcompany` (the key the fingerprinter already used); the slug is the lowercased
subdomain label, from either vendor host.

- **Listing: `xml.php`**, the tenant's whole syndication feed in one response. Its req count
  equals the rendered Board's on every Board whose page could be counted (155 of 155). It states
  title, department, office, city/state/country, date, company and the req, and repeats a req once
  per location (434 of 6,377) — rows are grouped per req and every place is kept.
- **Detail: `job-opening.php?req=N`**, for the description (xml.php's is cut at exactly 1,000
  chars on 6,601 of 8,335 rows) and the tenant's `Salary:` row (15 of 510 pages).
- **The tech gate is exact** (ADR-0166): title and department come from xml.php, and the detail
  overrides neither.
- **ADR-0048's skip is declined**, as oracle and pyjamahr decline it: the detail is the only source
  of `salary`, so skipping an already-described posting would blank that field on every later run.
  The cost is small — the gate already limits detail fetches to tech postings (8.7%).
- **Dead versus empty is xml.php's status**: 404 for an unknown or departed tenant (51 of 51 also
  had a 404 Board page; 2,473 of the ledger's 2,474 dead rows re-read 404), 200 with no `<job>`
  for a live Board with nothing open. Refused connections, timeouts, 5xx and DNS failures stay
  UNKNOWN (the wildcard answers nearly every label, so a DNS failure is the local resolver).
- **Company name is `<company>`**, on 100% of rows and constant within a Board (176 of 176).
- **Bytes are decoded one at a time where UTF-8 fails.** One feed can mix cp1252 bytes, UTF-8
  and cp1252 double-encoded through Latin-1 under its `encoding="UTF-8"` declaration, and 443 of
  510 detail pages are cp1252. UTF-8 is read where valid, each rejected byte as cp1252, and a
  leftover C1 control through cp1252: 0 broken of 16,676 feed titles and departments, where a
  whole-document fallback left 174 as mojibake on 46 of 174 Boards.

## Alternatives considered

- **ClearCompany's JSON feed** — one request per Board with full descriptions, and the simplest
  scraper. Rejected on four measured grounds: it was **short** of the public Board on 97 of 161
  Boards (3,597 vs 4,138 postings, 86.9%), with ordinary current postings missing; it served
  **zombie postings** for 14 of 244 tenants whose Board is gone (Linden Lab's newest posting is
  2011-07-25); it answered `[]` for 11 Boards that were hiring; and **`robots.txt` on
  `careers-page.clearcompany.com` and every `{slug}.clearcompany.com` checked (6 of 6) is
  `Disallow: /`**. HRM Direct publishes no `robots.txt` at all (404 on 6 of 6), so it states no
  crawler policy against what this scraper reads.
- **The `{slug}.clearcompany.com` HTML boards** — do not exist; the host is a login page.
- **HRM Direct's rendered Board** (`job-openings.php?search=true`) — templates vary per tenant, it
  paginates (266 of `oakmontmanagement`'s 695 reqs on its first view) and division tenants redirect
  to a filtered view.
- **HRM Direct's RSS** (`/employment/rss.php`) — empty on 140 of 140 hiring Boards.

## Consequences

- **One account is too large for the feed.** `heartlandbehavior` (3,036 reqs on its rendered
  Board) answers xml.php with a 500 after ~104 s, twice, and its sibling labels time out the same
  way. xml.php does honour the Board's own `state=`/`city=`/`dept=` filters, so the account *can*
  be read by splitting it: all 16 states its board offers were read on 2026-09-23 (3-12 s each,
  none failed), giving **4,558 distinct reqs, of which `is_tech` keeps 0**. **Decision:** the
  state split is not built, and the account's labels stay UNKNOWN and unscheduled — a split
  walker, its truncation handling and its tests would buy no tech Job. Revisit if a re-count ever
  finds more than ~20 tech postings there.
- **Most multi-label accounts are one Board.** xml.php returns the whole account from every label
  it owns, and req ids are platform-wide, so labels sharing a req are one account. Over the
  ledger's 1,304 hiring Boards, 1,881 label pairs share reqs — all identically — forming 131
  accounts over 453 labels; 322 duplicates would have re-served 30,209 of the ledger's 61,043
  postings. They are buried in `data/validate/aliases/clearcompany.csv` with signal
  `shared-reqs`, keeping per account a label whose board does not redirect to a division (the
  account-level site), else the lowest default-division id, alphabetical on a tie.
  **Known limitation — the brand.** Each label's `<company>` names its own division (over the
  same 9 reqs `chicagosteel` says Chicago Steel and `wirtzcorp` says Chicago Blackhawks), so for
  all 131 accounts / 453 labels every posting carries the kept label's brand, not the division
  that posted it. Recovering it would need a per-req division lookup the feed does not state. The file is written by `scripts/validate/clearcompany_shared_accounts.py`
  (re-run after every ledger refresh), not by `dedupe_boards.py`: that resolves by redirect,
  finds nothing here, and so refuses `--apply` for this ATS rather than erase the file. It is
  the first *pairwise* alias signal, which `board_aliases.py`'s grouping never had to handle.
- **Discovery.** Wayback (4,166 labels) plus one Common Crawl index (245: the 244-label sample
  plus `offers`, a vendor host). The planned sweep of
  the last ~3 years of Common Crawl indexes is **not measured**: `index.commoncrawl.org` answered
  every request with an empty reply on 2026-09-23. Its yield is unknown, not zero.

## Enable decision

**Enabled.** From the committed ledger, after the aliases: 982 scrapable hiring Boards and 30,834
postings. At 3,309 feed bytes a posting the feeds cost ~102 MB a run; at the measured 8.7% tech
share, ~2,688 tech postings add a 45 KB detail page each, ~121 MB. That is ~223 MB for ~2,688
tech Jobs, **~83 KB per tech Job** against ADR-0158's ~2 MB bar — ~24x under it, and ~556 KB
even ungated.
