# ADR-0181: A Breezy HR Board is one verbose JSON listing, and a bare `$` is named by country

**Status:** accepted · **Date:** 2026-09-23 · **Relates to:** [ADR-0012](0012-liveness-ledger.md) (the ledger is the scrape list), [ADR-0034](0034-nonprod-boards-dead-by-convention.md) (nonprod names are dead unprobed), [ADR-0050](0050-persist-descriptions-across-runs.md) (`has_detail_pass`), [ADR-0082](0082-salary-extraction-a-two-tier-cascade-no-estimate.md) (salary: precision first), [ADR-0114](0114-a-board-states-its-company-name-in-its-page-title.md) (company names), [ADR-0158](0158-jazzhr-and-jobvite-are-worth-their-storage.md) (the storage bar)

## Context

Breezy HR hosts each customer's careers portal at `{label}.breezy.hr`. It came out of a 20-ATS
evaluation (`experiment/ats-scraper-candidates/LOG.md`: 29,294 jobs sampled, 7.0% tech), with a
4,794-tenant pool already harvested and a third-party implementation to adapt
(`kalil0321/ats-scrapers`, `scrapers/breezy.py`, MIT). That implementation reads `GET /json`,
fetches every posting's HTML page for the description, treats a 302 to `breezy.hr` as a departed
tenant, and throttles itself to 4 concurrent requests against a cross-tenant 403 it reports at
~14 req/s.

Measuring the whole pool before building (2026-09-23,
`docs/breezy/2026-09-23_json-api-measurement.md`: 4,794 tenants, 38,314 postings, ~6,500
requests) contradicted three of those four premises.

## Decision

**The slug is the subdomain label**, one host, no pod: every posting's URL, the JSON listing and
the portal are on `{label}.breezy.hr`, and the listing's `company.friendly_id` equals the label on
all 2,174 hiring Boards. `slug_from` keeps the default.

**One request per Board: `GET /json?verbose=true`.** The undocumented `verbose` flag adds each
posting's `description`, and its text equals the detail page's (`html_to_text` equal on 180 of
180 pages with a JSON-LD `JobPosting`; every line present on the other 24). The listing has no
pagination and needs none — the largest Board, 2,760 postings, is one 8.1 MB response equal to
what its portal links. So there is **no detail pass** (`has_detail_pass = False`), and with it no
ADR-0048 skip and no ADR-0166 tech gate to place. The sitemap and portal page lost because they
omit postings the JSON serves (28 and 2 of 90 Boards) and carry no fields.

**Dead versus empty is the listing's status.** Across the pool: a JSON list is live (2,174
non-empty, 1,703 `[]`), a 404 "Career portal not found" is dead (917, the same page an invented
label gets), and nothing else came back — no 3xx, no 403, no 5xx. The prober does not follow
redirects, so a 3xx that appears later stays UNKNOWN rather than reading as the marketing site's
200. **A DNS failure is UNKNOWN, not DEAD**: `*.breezy.hr` is a wildcard record, so a tenant is
never NXDOMAIN; the first 432-worker liveness pass wrote 41 live Boards dead off the local
resolver's failures, and a replay at that width against 1,500 live Boards drew 100 of them.

**Fields, each on its measured distribution** (module docstring of `breezy.py` for the numbers):
`location` joins the primary place and every `locations[]` entry; `remote` is the primary
location's `is_remote` flag (it matched the page's JSON-LD on 103 of 103), with `remote_details`
"hybrid" as None; `employment_type` is keyed on `type.id`, since `type.name` is localised;
`posted_at` is `published_date`; `company` is the posting's `company.name`, one value per Board on
2,174 of 2,174 — so no board-page request is spent on ADR-0114.

**Salary is re-spelt for the structured parser.** Every stated salary (19,167) matches one
template — `{sym}{lo} – {sym}{hi} / {period}`, a floor `{sym}{n}+`, an exact `{sym}{n}`, or
`Up to {sym}{n}` — and `_field_generic` read only the yearly ones (33.8%), because `/ hour` and
`/ week` are not among its phrase markers. `_salary_field` emits `LO-HI CODE UNIT` and `breezy` is
registered with `_field_range_currency_interval`, which reads 96.4%. A floor keeps no ceiling, an
exact figure becomes coinciding bounds, and a lone ceiling (37) or a biweekly period (161) yields
no salary, since the parser would read the first as a floor and the second as a year. A new
dispatch key touches no stored row, so `DERIVATIONS_VERSION` is not bumped.

**A bare `$` is named by the posting's country: US → USD, Canada → CAD, anywhere else → none.**
This is a scoped exception, chosen explicitly by the user, to `salary._symbol_currency`'s rule
that a bare `$` names no currency. Measured against the detail page's JSON-LD
`baseSalary.currency` on 499 postings: USD on 141 of 141 US postings, CAD on 179 of 188 Canadian
ones, USD on 41 of 47 elsewhere (MXN, SGD, COP, unstated). The Canadian rule is **knowingly wrong
on the 9 Canadian postings paid in USD** (~5%); the other-country USD rate (87%) was not accepted.
Every other symbol maps to the one currency it named on every sampled page (£ GBP, € EUR, ₹ INR,
RD$ DOP, …), except `kr` (SEK or DKK), which names none.

**Known limit: most of those codes do not survive extraction.** `_field_range_currency_interval`
reads the code through `salary._CURRENCY_CODES`, which names eleven (USD, EUR, GBP, INR, CAD, AUD,
HKD, SEK, PLN, CHF, AED). The 15 mapped codes outside it — PHP, TWD, PKR, ZAR, CNY, THB, DOP, SAR,
VND, KWD, UAH, JPY, ILS, BRL, KES — are on 103 of 19,167 salaries: 66 reach `extract` correctly
annualised but with currency None (unpriced, as a bare `$` outside the US and Canada is), and 37
are declined by the USD-shaped plausibility bound. The scraper still emits the ISO code, so
widening the shared list — a shared-parser change, with its own bounds and a
`DERIVATIONS_VERSION` bump — is all it would take. It is left out of this ADR's scope.

**Enabled on arrival.** ADR-0158's bar is ~2 MB of fetched storage per tech Job. Breezy's is
**~0.06 MB**: the 2,174 hiring Boards' verbose listings are 216.9 MB for 38,314 postings
(5,660 B each), and `tech_filter.is_tech(name, department)` keeps 3,502 of them (9.1%) —
216.9 MB / 3,502 ≈ 0.062 MB, about 32x under the bar. The committed ledger, after the Wayback sweep grew the pool to
10,207, holds 2,907 Hiring Boards and 47,193 postings: at the same 5,660 B a posting and 9.1% tech
that is ~267 MB for ~4,300 tech Jobs — the same ~0.06 MB, since the ratio does not depend on the
pool's size.

**No gate is seeded.** No rate limit was found: the census ran each tenant once at up to 64
concurrent (94 req/s) with zero refusals, and one tenant served 113 req/s at 128 concurrent. The
upstream's 403 wall did not reproduce, so neither `_SPANNING` nor `_QUOTA_403` gets an entry
without a measurement to stand on; the auto-gate covers a wall that appears later.

## Alternatives considered

- **Plain `/json` plus a detail pass** (upstream's design). One request per posting for text the
  listing already carries — 38,314 extra requests per full pass for nothing.
- **A bare `$` → no currency, always** (`_symbol_currency`'s convention). It would leave the
  17,501 US postings priced in a bare `$` out of currency-aware salary sorting and brackets, to
  avoid an error that measured 0 of 141 on them.
- **A bare `$` → USD only in the US**, Canada → none (the coordinator's first answer, on
  ADR-0082's precision-first stance: ~5% wrong in Canada is above that bar). The user overrode it,
  accepting the 9-in-188 Canadian error for the 179 it names correctly, across the 704 Canadian
  postings priced in a bare `$`.
- **The exact currency from the detail page's JSON-LD**, fetched only for tech postings with a
  salary (702). Exact, but a whole detail pass — fan-out, failure accounting, tests — for one
  field.
- **Collapsing multi-portal duplicates.** One Breezy account can run several portals, and the
  same posting id is then served on each: 190 ids on 26 Boards, 191 extra rows (0.5%), 7 tech.
  Each is a separate Board with its own URLs, and `evict_duplicate` groups within a Board, so
  they are left as they are.

## Consequences

- The scraper is one request per Board with no detail fan-out, so a Board is either wholly read
  or failed; there is no stated total, so a silently short response cannot be detected — none
  was seen.
- `Job.salary` for breezy is our own `LO-HI CODE UNIT` spelling, not the provider's string.
- `sandbox` (a real Virginia organisation, 3 postings) stays dead under ADR-0034's name rule;
  an exception there is keyed by tenant string across every ATS, which is not worth 3 postings.
- If the Canadian USD share grows, or a country other than the US proves reliably USD, the
  country table in `breezy._DOLLAR_BY_COUNTRY` is the one place to change — and the change needs
  a `DERIVATIONS_VERSION` bump then, because Breezy rows will be stored by that time.
