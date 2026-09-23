# Pinpoint — postings API measurement (2026-09-23)

Step 2 of the `add-ats-scraper` skill: every question in `measurement.md`, answered against the
live hosts on 2026-09-23 with its sample size. Probe scripts and raw captures:
`experiment/pinpoint-postings-api/` (`LOG.md`, `probe_*.py`, `field_census.py`, `artifacts/`).

**Samples.** The *census*: `/postings.json` for all 555 slugs in the pool ∪ the upstream seed list
(550 answered 200, 433 hiring, **13,419 postings**), saved whole under `artifacts/listings/`. The
*page sample*: 76 posting pages from 40 random hiring Boards, each fetched twice 5 s apart. The
*Wayback set*: 904 slugs the Wayback sweep found that the census did not have, probed on
`/postings.json` and `/` with redirects off.

## Identity

1. **Slug = the subdomain label of `{slug}.pinpointhq.com`.** One host, no regional pod (the
   Wayback CDX sweep of `*.pinpointhq.com` found only that domain's labels). DNS is
   case-insensitive and so is the board: `CINVEN.pinpointhq.com/postings.json` answers 200 with
   the same Board as `cinven`, so the slug is lowercased. One tenant is one Board: there is no site
   or locale parameter that selects between postings (`/fr/postings/{uuid}` answers 404; `/en/`
   is the only locale served on 76 of 76 sampled pages). **29 of 433 hiring Boards run a vanity
   host** (`careers.dazn.com`, `jobs.butlins.com`, …), and the listing's `url` names that vanity
   host, but the same path on `{slug}.pinpointhq.com` answers 200 with the same page (5 of 5
   vanity Boards checked, JSON-LD present), so the vendor host is used for every link.
   Native posting ids: numeric `id` (13,419 distinct, no duplicates, no `:`), and a UUID in the
   posting URL — `/en/postings/{uuid}` on 13,419 of 13,419 rows. Only the UUID addresses a page
   (`/en/postings/{numeric id}` answers 404), so the UUID is the native id.
2. **Discovery spellings.** Pool, seed list, Wayback and Common Crawl all yield the bare label;
   `slug_from` lowercases it (the seed list and one listing URL spell `Cinven`).

## Listing

3. **Surfaces.** `GET /postings.json` — the board page's own XHR (it is the only JSON path the
   283 KB board HTML names). Alternatives, and why they lost: `/jobs.json` is the requisition
   view (11,715 requisitions vs 13,419 postings; 597 requisitions carry more than one posting,
   up to 41) and addresses `/en/jobs/{id}`, not the posting; the per-tenant `/sitemap.xml` (named
   by `robots.txt`) carries only URLs and a `<lastmod>`; the posting page is ~132 KB of HTML per
   posting. `/postings.xml` and `/postings.rss` answer 406.
4. **No pagination.** One response is the whole Board: `?page=2` and `?per_page=1` return the
   same bytes (ignored). On the three largest Boards the listing's posting set equals the
   sitemap's exactly (trilongroup 932/932, joinparachute 431/431, vgroup 366/366);
   americanveterinarygroup differed by 5 and 2 on one fetch of each, the sitemap lagging the
   listing. There is no stated total to terminate against — the response is one array.
   Largest Board: trilongroup, 932 postings, 6.8 MB in one response.
5. **Filter parameters.** None: the request takes no parameter.
6. **The listing carries the full description.** `description` is present and non-empty on
   13,419 of 13,419 rows, alongside three more HTML sections — `key_responsibilities` (100%),
   `skills_knowledge_expertise` (85.7%), `benefits` (72.3%) — each with a tenant-chosen
   `_header` ("What you'll do", …). The page's own JSON-LD `description` is those four sections
   in that order under their headers, prefixed by a metadata block, so the listing is not a
   teaser: composed text median 3,448 chars, max 20,134, no cap (the single longest `description`
   field is 20,016 chars and unique). Upstream's 25,000-char cap is its own choice.
7. **No hidden rows.** The listing's posting set equals the public sitemap's (item 4); no
   internal/unpublished flag exists on any of 13,419 rows.

## Dead versus empty

8. **An unknown slug is a real 404** — the vendor's 11,684-byte HTML 404 on both
   `/postings.json` and `/` (`zzqqnotatenant8127`, `asdfghjklqwerty`), and exactly that on 168 of
   the 169 Wayback slugs that 404 (the other is the vendor's own `trends` host). **A live empty
   Board is 200 `{"data":[]}`** (11 bytes), and its board page `/` answers 200 (117 of 117 empty
   Boards in the census). Two Wayback tenants (`betashares`, `goenumerate`) answer the listing with
   200 `{"data":[]}` but their board page with a 404: the account exists, the careers site is
   switched off. So: 200 with postings → live; 200 empty → the board page decides (200 live, 404
   dead); 404 → dead.
9. **A departed or renamed tenant 301s to another tenant.** 63 slugs (5 census, 58 Wayback)
   answer `/postings.json` with a 301 to `https://{other}.pinpointhq.com/postings.json` — all 63 to
   a pinpointhq label, none to a marketing site. 57 of the 63 targets are already live slugs;
   followed, they would read as a second live row for the same Board (the cross-hostname-redirect
   duplicate). The prober reads the redirect without following it and calls the old label dead;
   the 6 targets not yet held are folded into the pool as tenants in their own right. Vendor
   infrastructure hosts in the Wayback set (`developers`, `content`, `changelog`, `try`, `trends`,
   `sl`) answer 404 or 204 on `/postings.json` — not tenants.

## Detail

10. **The only field the listing lacks is the date** (and the location's country). No listing
    field states a date on any of 13,419 rows: upstream's `first_published_at` does not exist,
    nor does anything like it. The posting page's JSON-LD `JobPosting` states `datePosted` on 76
    of 76 sampled pages, and `jobLocation.address.addressCountry` on 75 of 76 (19 countries).
    Everything else the page states (`employmentType`, `baseSalary` on 31/76, `validThrough` on
    17/76 = the listing's `deadline_at`) the listing already has.

    | `Job` field | listing | posting page (76) |
    | --- | ---: | ---: |
    | title | 100% | — |
    | department (`job.department.name`) | 100% | — |
    | description (4 HTML sections) | 100% | 100% (same text) |
    | location (`name`/`city`/`province`) | 100% / 95.2% / 92.5% | — |
    | country | 0% | 98.7% (`addressCountry`) |
    | remote (`workplace_type`) | 100% | — |
    | employment type (`employment_type_text`) | 100% | — |
    | salary (visible) | 52.2% | — |
    | posted_at | **0%** | **100%** (`datePosted`) |

11. **The page needs nothing** — no header, token or query flag. It is ~132 KB raw (median of
    76; ~23 KB gzipped) and served chunked (no `content-length`).
12. **The tech gate is exact.** `title` and `job.department.name` are listing fields on 100% of
    rows (department null on 0 of 13,419), and nothing on the page overrides either.

## Fields

13. **Dates.** `datePosted` is real: identical across two fetches 5 s apart on 76 of 76 pages,
    spread from 2023-01-27 to 2026-09-23, in the tenant's zone (`+01:00`/`+00:00`). The sitemap's
    `<lastmod>` is not a substitute: it disagrees with `datePosted` on 36 of 76 postings, always
    later (up to 16 months), i.e. it is a modification date.
14. **Remote.** `workplace_type` is populated on 100%: onsite 9,282, hybrid 2,738, remote 1,399.
    Against the location-text guess (`is_remote`): of 12,020 onsite/hybrid rows, 82 name a remote
    location (0.7%; several are the vendor's own test postings, "Elvin new test"); of 1,399 remote
    rows, 919 name only a city. The stated type wins; hybrid is `None` (ashby's rule).
15. **Salary.** `compensation_visible` is the tenant's publication choice: 7,001 visible, 6,418
    hidden — and the hidden rows carry **no** figures (0 of 6,418 have a min, max or string), so
    upstream's "numeric fields can leak internal band data" did not occur. Visible rows: 6,414
    carry the structured quad (min, max, ISO currency, `compensation_frequency`) plus a display
    string (`"$20.00 - $24.00 / hour"`); 466 carry only a free-text string (`"£26,123 pro rata per
    annum"`, `"Starting at $18/hour + …"`); 119 carry nothing. min = max on 1,342 (a single
    figure); no row has a max without a min. Frequencies: year 3,233, hour 2,781, month 342, week
    33, day 16, two_weeks 10. Currencies: USD 4,376, GBP 1,480, CAD 171, EUR 129, PHP 120, 20+
    more. `salary.extract(…, ats="pinpoint")` (the generic Tier-1 parser) reads
    `"20-24 USD per-hour"`, `"50000-55000 USD per-year"`, `"2000-3000 USD per-month"` and
    `"13.77-13.77 GBP per-hour"` correctly, and returns None for week/day/two-weeks spellings —
    so those 59 rows (0.9% of the structured) are not emitted rather than read at the wrong
    period. The 466 free-text strings pass verbatim to the same parser, which reads some and
    declines the rest (`"Up to $70K (…)"` → None — no lone ceiling served as a floor).
16. **Experience / employment type.** No native experience field exists (upstream's
    `experience_level` is absent from all 13,419 rows). `employment_type_text` is 100% populated
    with 20 labels; `employment_type.flags` maps 14 of them (12,984 rows, 96.8%) onto a filter.
    The six it does not — Temporary 191, Flexible 187, Apprentice 23, Zero Hours 22, Programme 9,
    Volunteer 3 — pass through as the tenant spells them.
17. **Location.** One `location` object per posting (no multi-location field on any row).
    `name` is the tenant's label for the place — often an office or site name ("GM Tech",
    "North Suburban YMCA", "Shipboard") — while `city`/`province` are structured (95.2%/92.5%).
    `name` equals `city` on only 1,704 rows. The location string joins `name`, `city`,
    `province` (and the page's country, when fetched), each only when not already contained in
    what precedes it.
17a. **Department.** `job.department.name`, listing, 100%.
18. **Company name.** The board page `<title>` is `Jobs at {Name} | {Name} Careers` on 40 of 40
    sampled Boards and names the employer ("Jobs at The Jed Foundation", "Jobs at inDrive"). The
    JSON-LD `hiringOrganization.name` is per posting and was not needed.

## Operating limits

19. **Rate limit: none found.** One tenant's listing, concurrency 1→128 (512 requests): all 200,
    peaking at 60.8 req/s. Many tenants at once, 1→128 (512 requests over 512 distinct Boards):
    all 200, 27 req/s at 64 (the listing bodies are large). Posting pages of one Board, 1→128
    (431 requests): all 200, 106.7 req/s at 64, falling to 15 req/s at 128 — the knee is ~64.
    No spanning gate is needed; the detail pass ships at 16 workers.
20. **User-Agent-agnostic.** `headstart/0.1`, `python-requests/2.32`, a browser string, curl's
    default and an empty UA all answer 200 with identical bytes.
21. **Sizes.** Listing: 101,115,083 bytes for the census's 13,419 postings = **7.5 KB per
    posting**. Posting page: ~132 KB raw, ~23 KB gzipped.

## Population

22. **Tech share and volume.** `tech_filter.is_tech(title, department)` keeps **1,734 of 13,419
    (12.9%)** — below the candidates LOG's 14.6%, which predates tech filter v4. Postings per
    hiring Board: median 9, mean 31. Tech concentrates: trilongroup 128, impulsespace 110, vgroup
    74, davies 57.
23. **Language.** `langdetect` over title + description, 1,000 random postings: 985 English
    (98.5%); de 8, es 2, 5 others 1 each.
24. **Not a skin.** Postings, pages and the apply flow are Pinpoint's own; the 29 vanity hosts
    serve the same pages as the vendor host. The slug is a vendor label, so
    `cross_ats_duplicates.py` cannot join it; an overlap with another ATS would be a company
    running two ATSes, not one Board served twice.

## Discovery

- Pool 533 (`harvest`), upstream seed 406 (384 shared, 22 seed-only).
- **Wayback** (`wayback_pages.py pinpoint`, 47 CDX pages): 1,416 slugs, **904 not in the pool or
  seed** — 672 of them live, 259 hiring, 6,233 postings on first probe. Wayback nearly doubles the
  provider, as it did for iCIMS.
- Common Crawl: sweep of the last ~3 years of indexes, run in the shared discovery slot.
- Vendor roster: none found (the per-tenant sitemap is per tenant; there is no cross-tenant
  sitemap on `www.pinpointhq.com` that names boards).

## Vendor test tenants

`acme` ("ACME candidate 1" upstream), `developers-test` ("Jobs at Developer Acme"),
`joe-testing`, `joveo-sandbox`, `integration-testing`, `myinterviewdemo`, `smithsonian-sandbox`
answer as live Boards; each is checked by its postings before the ledger ships and excluded via
`config.EXCLUDED_BOARDS` if it is a test or sandbox tenant.
