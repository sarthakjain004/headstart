# Breezy HR `/json`: what the board API actually does

Measured 2026-09-23, before `headstart.scrapers.breezy` was written, against the live tenant
hosts. The upstream implementation this repo would otherwise have adapted
(`kalil0321/ats-scrapers`, `scrapers/breezy.py`, MIT) had the listing endpoint right and three
things wrong: it fetches every posting's detail page for a description the listing already
carries, it expects a departed tenant to 302 to the marketing site (it 404s), and it throttles
itself against a cross-tenant 403 wall that 5,700 requests up to 113 req/s did not find. The raw
captures, the per-tenant census table and the probe scripts are kept locally, not committed;
every number a reader needs is stated here. The decisions are ADR-0181.

Sample: **the whole existing pool, 4,794 tenants**, each fetched once — 3,877 live, 2,174 hiring,
**38,314 postings** — plus 204 detail pages (description), 90 Boards' sitemaps and portal pages
(completeness), 108 detail pages (remote), 499 detail pages (salary currency) and a single-tenant
ramp of 832 requests. ~6,500 requests in total.

## Identity

**Q1 — slug.** The subdomain label of `{slug}.breezy.hr`. One host; the portal, the JSON listing,
the sitemap and every posting URL are on it (38,314 / 38,314 posting `url`s are
`https://{slug}.breezy.hr/p/{friendly_id}`). DNS makes it case-insensitive (`FATHOM.breezy.hr/json`
answers `fathom`'s Board). One tenant is one site: `company.friendly_id` equals the slug on
2,174 / 2,174 hiring Boards. Native ids are 12 or 14 hex characters (34,626 / 3,688), never
contain `:`, never repeat within a Board.

**Q2 — spellings.** Every pool row is a lowercase `[a-z0-9-]` label already; the upstream seed
list (1,384 rows) is the same shape and wholly contained in the pool. Wayback (`sub` style) and
Common Crawl (`label` kind) both emit the label. `slug_from` needs no override beyond the
default; case-folding in `config.board_key` covers casing.

## Listing

**Q3 — surfaces.** Four exist:

| surface | carries | verdict |
| --- | --- | --- |
| `GET /json?verbose=true` | every posting, every field, **and the description** | **chosen** |
| `GET /json` | the same rows without `description` | loses: would need a detail pass |
| `GET /sitemap.xml` | `/p/{friendly_id}` + day-precision `lastmod` | loses: omits postings (below), no fields |
| `GET /` (portal HTML) | links to postings | loses: HTML, omits postings on 2 of 90 Boards |

`verbose=true` is undocumented; the portal's own `index.js` never calls `/json`. Other guesses
(`?description=true`, `?format=rss`) are ignored; `/p/{id}/json`, `/p/{id}.json`, `/json/{id}`,
`/api/positions`, `/rss` 302 to the tenant root. `robots.txt` names no feed.

**Q4 — pagination.** None, and none needed. The largest Board in the pool,
`american-logistics-authority`, returns **2,760 postings in one 8.1 MB response**, equal to the
2,760 its portal page links; the heaviest, `everstar`, 372 postings in 23.6 MB (inline base64
images), in 2.9 s. There is no stated total to check a response against, so a shortfall is not
detectable from the response itself.

**Q5 — filters.** `/json` takes no parameter that narrows the set; `verbose` only adds a key.

**Q6 — description.** Present on 38,314 / 38,314 rows (114 are image- or empty-tag-only and
yield no text). Against the detail page, on 204 postings from 120 random hiring Boards:
`html_to_text(listing)` equals `html_to_text(JSON-LD description)` on **180 / 180** pages that
carry JSON-LD; the other 24 pages carry no JSON-LD (talent-pool and "general interest" postings)
and every line of the listing text appears on the page, **24 / 24**. The raw HTML differs by a
few markup attributes (the listing is never shorter), never in text. Not truncated: text p50
2,695 chars, max 27,105.

**Q7 — hidden rows.** None found. On 90 Boards (the 10 largest, 60 random hiring, 20 random
empty) neither the portal page nor the sitemap lists an id the JSON lacks (0 / 90). The reverse
happens: the sitemap omits postings on 28 Boards and the portal page on 2, and every one of those
postings' pages answers 200 with the posting — they are public, merely unlisted.

A job link built from `friendly_id` lands on the posting: 20 of 20 random postings (20 Boards)
answered 200 with the posting's title in `<title>`, no redirect. An id the Board does not hold
302s to the portal root (`Location: /`), so a closed posting's link lands on the Board, not an
error page.

## Dead versus empty

**Q8/Q9.** Settled by the listing's status alone, on the whole pool:

| response | tenants | meaning |
| --- | ---: | --- |
| 200 `application/json`, non-empty list | 2,174 | live, hiring |
| 200 `application/json`, exactly `[]` (2 bytes) | 1,703 | live, nothing open |
| 404 `text/html`, 3,265 bytes, "Career portal not found \| Breezy HR" | 917 | dead |
| anything else (3xx, 403, 5xx, timeout) | **0** | — |

An invented slug gets the byte-identical 404 on `/json`, `/json?verbose=true` and `/`. Upstream's
"302 to `https://breezy.hr/`" (H4) appeared on none of 4,794 tenants, so there is no
redirect-dead case to read off `Location`; the prober still asks without following redirects,
so a 3xx that appears later reads as UNKNOWN rather than as the marketing site's 200.

**A DNS failure is not a dead tenant.** `*.breezy.hr` is a wildcard record — the invented label
resolves to the same CloudFront addresses as `fathom` — so no tenant is ever NXDOMAIN. The
liveness prober's 432 workers each resolve a different hostname, and the local resolver fails
under that: the first pass wrote 41 live Boards dead (in alphabetical clusters, including
`kimmel-associates` with 445 postings), every one of which answered 200 on a re-fetch. A replay
at 432-wide against 1,500 live Boards drew 1,379 200s, **100 DNS errors** (curl code 6), 21
timeouts and zero 404s. `p_breezy` reads a DNS failure as UNKNOWN.

## Detail

**Q10–Q12.** No detail request is needed: every `Job` field the detail page states is on the
verbose listing, and the description is the same text (Q6). The one thing only the detail has is
the salary's ISO currency (Q15), and the listing symbol resolves it closely enough that a detail
pass is not worth it. No token, no header, no session. With no detail pass there is no tech gate
to place (CONTEXT.md's Detail-pass entry does not apply).

## Fields

**Q13 — dates.** `published_date` is real: 293 / 293 postings on 40 Boards read the same across
two fetches ~40 min apart, and only 34 / 38,314 end `.000Z`. It is the **latest** publish, not the
first: the detail's JSON-LD `datePosted` is ≤ `published_date[:10]` on 180 / 180 (equal 68,
earlier 112). No `validThrough` on the listing.

**Q14 — remote.** `location.is_remote` is what the page publishes. Checked 12 per class against
the detail's JSON-LD `jobLocationType`: `is_remote` true → `TELECOMMUTE` on 46 / 46 with JSON-LD;
false or absent → no `jobLocationType` on 57 / 57. `location.remote_details.value` is stale where
`is_remote` is false (307 rows say remote/remote-location/hybrid there), and says **hybrid** on 995
rows where `is_remote` is true — the page calls those TELECOMMUTE, this repo's rule calls them
`None` (ashby). `is_remote` is absent on 2,194 rows. Across a posting's `locations`, `is_remote`
never disagrees (0 rows).

| `is_remote` | `remote_details.value` | rows |
| --- | --- | ---: |
| false | — | 29,560 |
| true | remote | 2,723 |
| absent | — | 2,194 |
| true | remote-location | 1,469 |
| true | — | 1,066 |
| true | hybrid | 995 |
| false | remote / hybrid / remote-location | 196 / 87 / 24 |

**Q15 — salary.** A string on 19,167 / 38,314 (50.0%), always machine-templated — 87 shapes, the
top five `$N – $N / hour` 6,175, `/ year` 5,903, `/ week` 2,736, `$N+ / hour` 1,058, `$N / hour`
646. The forms are a range `{sym}{lo} – {sym}{hi}`, a floor `{sym}{n}+`, an exact `{sym}{n}`, and a
ceiling `Up to {sym}{n}` (37 rows); ranges are 16,079 and floors 1,732. Periods: `/ hour` 8,205,
`/ year` 6,682, `/ week` 2,824, `/ month` 1,108, `/ biweekly` 161, `/ day` 97, none 90.
`salary.from_field(s, ats="breezy")` today (no parser registered, so `_field_generic`) reads only
the yearly shape: `/ hour`, `/ week` and `/ month` match none of its phrase markers, so
`$25 – $30 / hour`, `$1,500 – $2,000 / week` and `$3,000 – $4,000 / month` read as annual and the
plausibility floor returns None; `$100,000 – $150,000 / year` parses with currency None.

Currency, symbol against the detail's JSON-LD `baseSalary.currency` on 499 postings: a bare `$`
is USD on 141 / 141 US postings, **CAD on 179 / 188 Canadian ones** (USD 9), and USD on 41 / 47
elsewhere (MXN 2, SGD 1, COP 1, unstated 2). Every other symbol names one currency (£ GBP, € EUR,
₹ INR, R ZAR, zł PLN, ₨ PKR, NT$ TWD, ₱ PHP, ฿ THB, CHF, RD$ DOP, CN¥ CNY, ₫ VND, ₴ UAH, ￥ JPY,
₪ ILS, R$ BRL, Ksh KES, and the Arabic-script dirham/riyal/dinar), except `kr` (SEK 3, DKK 1 — by
country).

What shipped (ADR-0181): `_salary_field` re-spells the template as `LO-HI CODE UNIT`, `breezy` is
registered with `_field_range_currency_interval`, and a bare `$` is USD in the US, CAD in Canada
and none elsewhere (the user's choice; 17,501 US / 704 Canadian / 236 other postings carry one).
Over the census, `extract` reads **18,483 of 19,167 (96.4%)** against the generic parser's 6,484
(33.8%) on the raw strings. The 40 rows the generic parser read and this does not are all its
misreads (a PKR or PHP monthly figure read as an annual floor, a tenant's "$80,000 – $100,000 /
hour" read as annual); the 352 yearly strings that do not parse are tenant errors ("$20 – $23 /
year") the plausibility floor rejects; `Up to` (37) and biweekly (161) are withheld. Of the 18,483, 66 carry an ISO code the shared parser does not
name (PHP, TWD, PKR, ZAR, …) and so come out with currency None; 37 more such rows are declined
(ADR-0181's known limit).

**Q16 — experience and employment type.** No experience field: upstream's `experience`,
`category`, `education`, `tags` are on 0 of 38,314 rows. `type.id`: fullTime 25,626, contract
5,501, partTime 5,026, other 1,658, temporary 503 — `type.name` is the same value localised
("Повна зайнятість", "Vollzeit", "A tiempo completo"), so the id is the key. `employment_type_filter.flags`
reads "Full-Time", "Part-Time" and "Contract"; "Temporary" and "Other" reach no filter. No intern
id was observed.

**Q17 — location.** `locations[]` holds 0 / 1 / 2 / 3 / 4 / 5 places on 2,970 / 33,327 / 882 / 356 /
220 / 559 rows; each carries a ready `name` ("Trail, BC"). `location` is the primary, one of
`locations` by name on 34,516 / 35,344, and the only place on 2,969 of the 2,970 rows with no `locations` (the other has no
primary either).

**Q17a — department.** `department`, on 20,407 / 38,314 (53.3%), on the listing.

**Q18 — company name.** `company.name`, one value per Board on 2,174 / 2,174 hiring Boards, the
employer's own spelling ("Mytonomy, Inc.", "Workforce Solutions Coastal Bend"). An empty Board
has no row to read it from and emits no Job either.

## Operating limits

**Q19 — rate limit.** None found. Across tenants (the census, each tenant once): conc 4 / 8 / 16 /
32 / 64 → 9.8 / 19.1 / 35.7 / 66.0 / 93.9 req/s, p50 0.35–0.53 s, and 3,544 more at conc 16,
35.0 req/s — **0 statuses other than 200/404 in 4,794**. One tenant (`salmonjobs`, 600 KB listing):
conc 1 → 128 up to 113 req/s on the listing and 109 req/s on detail pages, 832 / 832 200. Upstream's
403 at ~14 req/s across tenants (H6) is not reproduced; no `_SPANNING` or `_QUOTA_403` entry has
a measurement to stand on.

**Q20 — User-Agent.** `headstart/0.1`, curl's default, `python-requests/2.32` and `Mozilla/5.0`
all 200 with identical bytes.

**Q21 — size.** Hiring-Board listing p50 22 KB, p95 336 KB, max 23.6 MB. All 2,174 hiring Boards:
216.9 MB for 38,314 postings, **5,660 B per posting** (~980 B without descriptions). An empty
Board is 2 bytes; a dead one 3,265.

## Population

**Q22 — tech share.** `tech_filter.is_tech(name, department)` keeps **3,502 / 38,314 (9.1%)**,
on 713 Boards; by country US 1,677, CA 193, UA 186, IN 145, GB 108.

**Q23 — language.** `langdetect` over title + description text: 94.5% English on 2,000 random
rows (Ukrainian 31, Spanish 22, German 14, French 11); 90.2% on 600 random tech rows (Ukrainian 27).

**Q24 — overlap.** Breezy is not a skin: every posting links to its own `{slug}.breezy.hr` page
and nothing on the listing names a backing ATS. `cross_ats_duplicates.py` and `dedupe_boards.py`
key on host slugs and find nothing for a vendor-subdomain label, so a company that is also a
tenant elsewhere is not measured here.

Within Breezy, one account can run several portals under different labels, and a posting is then
served on each with the same id and its own URL: 190 ids on 26 Boards (`lumio-dental` /
`lumio-dental-practice-locations` share 62), **191 extra rows of 38,314 (0.5%), 7 of them tech**.
They are left as separate Boards (ADR-0181).

## Discovery

`breezy.hr/sitemap.xml` (2,640 URLs) lists marketing pages only, and `*.breezy.hr` sits behind
CloudFront with no tenant roster found. The pool is the existing `harvest` list (4,794), the
upstream seed list (0 new) and a Wayback CDX sweep of `breezy.hr` (`sub` style: 194 CDX pages,
9,828 labels, **5,413 new to the pool**, 4,415 re-confirming it). The prober settled the 10,207 at
5,156 live, 5,038 dead and 13 unknown — the unknowns are Breezy's own infrastructure hosts
(`assets-cdn`, `gallery-cdn`, `test-app`, `resources`, …: a CDN's HTML, a 301 to `breezy.hr/blog`
or `help.breezy.hr`, a 302 to `/signin`, or a timeout), none a tenant, left UNKNOWN rather than
killed on a response no real tenant was seen to give. The Wayback-only tenants add 1,280 live, 734
hiring, 8,927 postings. **Common Crawl
was not measured:** `index.commoncrawl.org` returned an empty reply (curl 52) to every request on
2026-09-23, so its contribution is unknown, not zero; `cc_miner.ATS_PATTERNS["breezy"]` (`label`
kind) is wired for a later sweep.
