# Pinpoint (pinpointhq.com) — postings API measurement log

Started 2026-09-23. Step 1 of the `add-ats-scraper` skill: every existing claim about this ATS as
an open hypothesis, each confirmed (C) or killed (K) in step 2. The write-up is
`docs/pinpoint/2026-09-23_postings-api-measurement.md`; raw captures in `artifacts/`.

## Hypotheses (sources: CLAUDE.md, experiment/ats-scraper-candidates/LOG.md, upstream
kalil0321/ats-scrapers `scrapers/pinpoint.py` + `tests/test_pinpoint.py`, seed list
`ats-companies/pinpoint.csv`)

- H1 (candidates LOG): 11,272 jobs, 14.6% tech, 2.4% India, 338 companies, 406 seed slugs.
- H2 (upstream): one endpoint, `GET https://{slug}.pinpointhq.com/postings.json`, returns
  `{"data": [...]}`, every active posting, **no pagination**.
- H3 (upstream): tenants without an active careers site **3xx-redirect to the marketing site**;
  upstream docstring says "return 404" — the two contradict each other.
- H4 (upstream): locale variants `/fr/postings.json` exist; English is the default.
- H5 (upstream): `posted_at` is `first_published_at` on the listing.
- H6 (upstream): compensation is `compensation_minimum`/`_maximum`/`_currency`/`_frequency`,
  and only trustworthy when `compensation_visible` is true ("numeric fields can leak internal
  band data").
- H7 (upstream): `employment_type` values include `permanent_full_time`, `fixed_term_*`, etc.
- H8 (upstream): `workplace_type` is `remote`/`hybrid`/`onsite`; `location.name` is the label.
- H9 (upstream): department is `job.department.name`; requisition id is `reference`.
- H10 (upstream): `description` is the posting body, capped at 25,000 chars (upstream's choice).
- H11 (upstream): default UA is `Mozilla/5.0`, implying a non-browser UA might be refused.
- H12 (upstream fields read): `experience_level`, `schedule`, `tags`, `office`,
  `remote_country_restriction` exist on some postings.
- H13 (pool): slug = subdomain label of `{slug}.pinpointhq.com`; 533 pool rows (all `harvest`),
  406 seed rows, 384 in both, 22 seed-only.

## Surfaces seen in the first requests (workwithus, the vendor's own board)

- `robots.txt` disallows `/mydata`, `/admin`, `/companies`; names `Sitemap: /sitemap`.
- `/sitemap.xml` is a per-tenant urlset: `/`, `/faqs`, custom pages, `/postings/{uuid}` with
  `<lastmod>`.
- `/postings.json` 20,587 B for 2 postings — carries `description`, `benefits`,
  `key_responsibilities`, `skills_knowledge_expertise` (+ `_header`s) as HTML, **no date field**
  (H5 looks false on this board).
- `/jobs.json` 200 — the requisition-level view, `url` `/en/jobs/{job.id}`; no date either.
- `/postings/{uuid}` HTML page 269 KB with a JSON-LD `JobPosting` carrying `datePosted`,
  `employmentType`, `jobLocation.address.addressCountry`, `identifier` = the uuid.
- `/postings/{uuid}.json` answers 406; `/postings.json?page=2` and `?per_page=1` return the same
  20,587 bytes (parameters ignored).

## Findings (2026-09-23) — full write-up in docs/pinpoint/2026-09-23_postings-api-measurement.md

- H1 partly K: census of 555 slugs (pool ∪ seed) → 550 live, 433 hiring, 13,419 postings;
  tech 12.9% under filter v4 (1,734), not 14.6%.
- H2 C: `/postings.json` is the whole Board in one array; `?page`/`?per_page` ignored; largest
  Board trilongroup 932 postings (= its sitemap's 932).
- H3 split: renamed tenants 301 to **another pinpointhq label** (63 of 63 redirects); a slug that
  never existed is a **404** (vendor 11,684-byte page, 168 of 169 Wayback 404s). No redirect to a
  marketing site was seen.
- H4 K for pages: `/fr/postings/{uuid}` 404s; `/en/` on 76/76 sampled pages.
- H5 K: no date field on any of 13,419 listing rows. Date only in the posting page's JSON-LD
  `datePosted` (76/76, stable across refetch).
- H6 C/K: `compensation_visible` gates publication (7,001 true / 6,418 false), but hidden rows
  carry no figures at all (0 of 6,418) — no leak observed.
- H7 C: 20 `employment_type` codes, each with a display label in `employment_type_text`.
- H8 C: `workplace_type` 100% populated (onsite/hybrid/remote); `location.name` is often an
  office/site label, not a place.
- H9 C/K: department `job.department.name` 100%; requisition id is `job.requisition_id` (73.1%),
  not `reference` (absent).
- H10 C: description 100% present and untruncated (max 20,016 chars); plus three more sections.
- H11 K: UA-agnostic (headstart/0.1, python-requests, curl default, empty UA all 200).
- H12 K: none of `experience_level`/`schedule`/`tags`/`office`/`remote_country_restriction`
  occurs on any of 13,419 rows.
- H13 C: slug = lowercase subdomain label; case-insensitive (`CINVEN` = `cinven`).
- Wayback: 1,416 slugs, 904 new; 672 live / 259 hiring / 6,233 postings among the new.
- Rate limit: the first ramps (≈1,450 requests to 128 concurrent) were clean, but a 256-wide
  burst across tenants drew connection refusals that then held for minutes against every
  tenant (`artifacts/spanning_*.txt`); paced 5–50 req/s is clean (`artifacts/paced_*`). Gated
  in the prober as a spanning host at 16.
- Build-time finding: the posting page answers **406** to `Accept: application/json, text/html`
  (the shared `_get`); `text/html` works. And asked as a browser, `/` 404s on 17 hiring Boards
  (1,224 postings) whose postings 404 too, and redirects 160 to their vanity host
  (`artifacts/hiring_html.csv`, `artifacts/empty_roots.csv`).
- Common Crawl: **not measured** — `index.commoncrawl.org` gave an empty reply on every request
  on 2026-09-23 (the sweep's zero is that outage, not a finding).
- Ledger: 1,465 rows, 819 live / 644 dead / 2 unknown; 668 Hiring Boards, 18,364 postings after
  six test tenants were excluded; 12.0% tech; ~195 KB fetched per tech Job.

`artifacts/listings/` (98 MB, one saved listing per live census Board) is not committed; re-derive
it with `python3 probe_population.py <seed csv>`.
