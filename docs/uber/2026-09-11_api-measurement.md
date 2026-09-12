# Uber careers API — measured 2026-09-11

Uber runs its own in-house careers system at `jobs.uber.com` — one company, one board, never a
second tenant. ADR-0139 models that as its own `ats` value (`ats="uber"`) with a fixed,
non-discovered `slug` (`jobs.uber.com`) rather than a slug under some shared platform. This is the
first of the eight boards that ADR names (Amazon, Apple, Google, Meta, Tesla, Uber, ByteDance,
TikTok). Everything below was hit live against the real endpoint, not inferred from a sibling
scraper or from docs.

## The transport wall, and why it does not need a workaround

`GET https://www.uber.com/careers/list/` answers HTTP 406 to a bare client with no `Accept`
header — the earlier quick probe that flagged this task. It is not a content-negotiation issue at
all: the whole `uber.com/careers/*` surface 301s to `jobs.uber.com/en/jobs/`, a different site.

The real listing API, `GET https://jobs.uber.com/api/jobs/search/`, sits behind Cloudflare. A bare
`curl` or `python-requests` client gets Cloudflare's own "Just a moment..." interstitial:

```
HTTP/2 403
cf-mitigated: challenge
content-type: text/html; charset=UTF-8
```

This repo's shared HTTP layer (`headstart.http.fetch`) already clears it, with **zero code
changes**: that module runs every request through a `curl_cffi` `Session(impersonate="chrome")` —
its own module docstring says so ("Chrome impersonation lets the same client handle both plain
JSON APIs and the TLS-fingerprinted boards"). Verified directly with `BaseScraper._get`'s exact
headers (`User-Agent: headstart/0.1`, `Accept: application/json`, no `Referer`, no cookie jar):

```
GET https://jobs.uber.com/api/jobs/search/?page=1&pagesize=5  ->  200 application/json
```

18/18 requests in a burst all returned 200. This means the sibling `kalil0321/ats-scrapers`
project's own Uber scraper reaches for a dedicated TLS-impersonating "httpcloak" client to clear
the same wall — a workaround this repo does not need, because `headstart.http` already impersonates
Chrome for every scraper, not just this one. `robots.txt` allows everything (`Allow: /`) and even
publishes a `Sitemap:`, so the API is not something the site is trying to keep hidden from bots in
general — only from clients with a bot-shaped TLS fingerprint.

## The listing endpoint

`GET /api/jobs/search/?page={n}&pagesize={m}` (1-indexed `page`) returns:

```json
{"jobs": [...], "totalPages": N, "totalJobs": N, "page": N, "pageSize": N}
```

As of 2026-09-11: **517 open postings**, `totalPages` recomputes correctly for whatever
`pagesize` is asked (`pagesize=2` -> `totalPages=259`; `pagesize=200` -> `totalPages=3`).

**No server-side cap on `pagesize`.** Tried 100, 1,000, 10,000 and 100,000 — every one echoes
back exactly what was asked and returns all 517 rows once `pagesize >= totalJobs`. (This section
originally used that headroom to argue for pagination as the safer default — see the
2026-09-12 update immediately below for why that reasoning was wrong and what replaced it.)

**Update, 2026-09-12: multi-page pagination is *not* reliable — this backend has the same
replica-ordering instability Eightfold's PCSX API has** (`docs/eightfold/
no-client-side-fix-for-replica-instability.md`). The two full sweeps above (2026-09-11) each
looked clean — zero duplicates, matched `totalJobs` exactly — but that was not representative. A
live re-check the next day caught it directly: paginating the (now 520-job) board at
`pagesize=200` collected 519 unique ids, and independently paginating at `pagesize=100` also
collected 519 — but **a different job missing each time** (`160242` absent from the 100-walk,
`302210` absent from the 200-walk; the 200-walk also served `302210` twice across two different
pages, which is how it landed on 519 rather than 518). A single one-page pull at `pagesize=1000`
(no page boundary to disagree across) returned exactly 520 unique ids with zero duplicates,
confirmed twice more. **The scraper now fetches one page sized to the board's own `totalJobs`,
never walks multiple pages** — verified live immediately after the fix: two calls (a
`pagesize=1` probe, then one `pagesize=520` pull) returned exactly 520 unique ids, matching an
independently-fetched fresh `totalJobs=520` check made moments before. A bounded retry
(`_MAX_ATTEMPTS=5`) re-sizes and re-fetches if the board's count moves between the probe and the
real pull.

This is the same lesson `_api_search`'s multi-sweep convergence loop encodes for Eightfold, at
smaller scale: don't trust "zero duplicates, matched the total" from a small number of sweeps as
proof a paginated backend is stable, and prefer the surface with no page boundary at all when one
exists and is cheap enough (Uber's whole board fits in one HTTP response; Eightfold's routinely
does not, which is why it needs the sweep-and-converge approach instead).

**`page` past the end answers an empty batch, not a blanked envelope.** `page=999` returns
`{"jobs": [], "totalJobs": 517, ...}` — contrast Oracle's `_OFFSET_CEILING`, which zeroes
`TotalJobsCount` itself once the offset ceiling is crossed. This fact is no longer load-bearing —
the scraper does not page past 1 — but it is why the empty-batch case was never mistaken for a
truncation signal while the (now-replaced) paginated version was live.

**No rate limit found.** 18 requests at ~13 req/s, all HTTP 200 (small sample, not a guarantee —
CLAUDE.md's own bar for this kind of claim).

## Field mapping, and three fields worth flagging

Every field the scraper uses is on the listing row — no detail pass. Verified 3/3 sampled
postings: the listing's `Description` (full HTML) is byte-identical to the `description` inside
the per-job page's own JSON-LD `JobPosting` block at `https://jobs.uber.com/en/jobs/{id}/`. Hence
`has_detail_pass = False`.

| Job field | Source |
| --- | --- |
| `title` | `Title` |
| `location` | first `Locations[]` entry, `"{City}, {Region}, {Country}"` (blank segments dropped) |
| `department` | first `Teams[]` entry |
| `posted_at` | `DisplayDate` |
| `description` | `html_to_text(Description)` |
| `remote` | `bool(Remote)` |
| `employment_type` | `ContractType` and `WorkPattern` joined (see below) |
| `url` | the `IsDefault` entry of `Urls[]`, resolved against `https://jobs.uber.com` |

**`Remote` is real but always `False`.** A boolean present on every one of the 517 sampled rows
(never null), yet 0/517 read `true`. Checked for a broken-field explanation (searched titles for
"remote" — the two hits were "Remote Assistance" product names, false positives) and found none;
this reads as consistent with Uber's stated hybrid/RTO policy (several descriptions cite a
50%-in-office minimum) rather than a defect, so the scraper reads it as stated rather than
overriding it with a location-text guess.

**`Salary` numeric fields are always null.** `MinValue`/`MaxValue`/`Currency`/`Period` are null on
all 517 rows; `Salary.Description` (prose, present on 250/517) duplicates text already inside the
main `Description` on the postings checked. No structured salary is extracted here — `Job.salary`
stays `None`, and the downstream `salary.extract()` pass reads it out of the description text like
every other ATS.

**`DisplayDate` was checked for fabrication, not trusted on sight** — CLAUDE.md flags this exact
class of mistake (a single sample generalised into a provider-wide claim). Two checks: (1) a
10-job snapshot re-fetched 4 seconds later showed every `DisplayDate` unchanged, distinct
per-job — not a request-time computation; (2) across all 517 rows the dates span 2026-06-19 to
2026-09-11, a real spread, not "now" repeated. Reads as a genuine stored timestamp.

**`employment_type` needed both source fields, not one.** `ContractType` ("Full time" on 504/517,
blank on 13) states hours; `WorkPattern` ("Regular" 487, "Intern" 11, "Fixed Term" 3, blank 16)
states the arrangement. An Intern posting states **both at once** — id `300864` carries
`ContractType="Full time"` and `WorkPattern="Intern"` simultaneously — so preferring either alone
drops the other's signal. The scraper joins them (`"{contract} / {pattern}"`) when both are
present and differ, the same pattern `personio.py` already uses for its own two employment
fields, and falls back to whichever one is non-blank otherwise.

## Coverage note

This board mixes Sales (230), Engineer (81), Customer Support (45) and eight more `Teams` values
— like every other multi-department ATS board, the tech-only gate (`headstart.tech_filter`) is
what narrows this to software-engineering roles downstream; the scraper itself reads the whole
board, per CLAUDE.md's project-scope rule.
