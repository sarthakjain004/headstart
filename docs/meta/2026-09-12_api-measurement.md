# Meta (metacareers.com): what the two public surfaces actually return

Measured live 2026-09-11/12 against `www.metacareers.com`, the single Board this ATS ever has
(ADR-0139). Every figure below is a measurement — direct HTTP fetches from this session, no
browser, no captured HAR — not a reading of the code or of the sibling `kalil0321/ats-scrapers`
project's own `meta.py` (fetched and read for orientation, cited where its findings apply).

Evidence base: two burst tests (20 requests at concurrency 10, 50 at concurrency 20) against
randomly and sequentially sampled job ids, ~80 total detail-page fetches, `robots.txt`, and one
made-up id one past a real posting.

## 0. The listing UI's own GraphQL API is not reachable without a browser

`https://www.metacareers.com/jobs` answers **HTTP 400** to a plain GET (both a bare `headstart/0.1`
User-Agent and a browser-shaped Chrome UA) and **301**s to `/jobsearch/` under `headstart/0.1`. The
redirected page is a real 200, but it is a client-side React app: its server-rendered HTML carries
no embedded job data at all — no `__NEXT_DATA__`, no `job_search_with_featured_jobs` payload
(the query name the sibling project's `meta.py` intercepts from the browser's own GraphQL traffic),
and zero `"__typename":"Job"` matches. The search is fed by GraphQL calls requiring browser-issued
tokens (`fb_dtsg` and friends) — confirmed by inspection, not assumed, and consistent with the
sibling project's own architecture, which drives a real (stealth-patched) Chromium instance and
intercepts the GraphQL responses rather than calling the endpoint directly. This scraper does not
attempt that: no browser automation is used anywhere in this repo's scrape path, and adding one for
a single Board would be a new category of infrastructure cost for one company.

## 1. Two public, unauthenticated surfaces exist instead, found via `robots.txt`

`robots.txt` names two sitemaps:

```
Sitemap: https://www.metacareers.com/sitemap/www_metacareers_com_sitemap.xml.gz
Sitemap: https://www.metacareers.com/jobsearch/sitemap.xml
```

The first — presumably the whole site, not just jobs — answers **403** and is not used. The
second is this scraper's listing surface, and it works.

## 2. The listing: `/jobsearch/sitemap.xml`

A flat `<urlset>` (no `<sitemapindex>`) of every open posting's canonical URL,
`https://www.metacareers.com/profile/job_details/{id}/`, each with a `<lastmod>`. Measured
2026-09-11: **952 postings**, no User-Agent gate (a bare `headstart/0.1`, curl's default, and
`python-requests`'s default all return 200), and no further pagination — `/jobsearch/sitemap-1.xml`
and `/jobsearch/sitemap2.xml` both 404, and `?page=2` is silently ignored: it answers 200 with the
**same 952-member set**, just reordered (byte-identical length, disjoint diff, zero id difference
across two independent fetches). `/jobs/{id}/` (the sibling project's URL shape) 302s to
`/profile/job_details/{id}/`, confirming the canonical form.

**952 is this measurement's number, not a documented invariant** — nothing here claims Meta's
global open-role count is stable, only what this sitemap served at the time of capture.

## 3. The detail surface: the job page's own schema.org `JobPosting` JSON-LD

Every field this scraper emits except `id` comes from here — the sitemap carries no title,
location, or anything else. Measured over 80 ids (a first batch of 30 taken in sitemap order, a
second batch of 50 taken as a random sample to avoid ordering bias): **80/80 HTTP 200, 80/80
carried a parseable `JobPosting` JSON-LD block**, no special headers or query parameters needed
(unlike icims's `in_iframe=1`).

The JSON-LD keys present on every one of the 80: `title`, `description`, `responsibilities`,
`qualifications`, `hiringOrganization` (always `{"name": "Meta", ...}`), `datePosted`,
`validThrough`, `jobLocation`, `employmentType` (always `"Full-time"` in this sample —
part-time/intern postings were not sampled), `directApply`. `jobLocationType` and
`applicantLocationRequirements` appear only on remote-eligible postings (2 of 50 in the random
sample carried `jobLocationType`).

**A stale sitemap entry is a detail gap, not an error.** One id one past a real posting
(`4454815524774630` → `4454815524774631`, deliberately incremented, not a real listed id) answers
**HTTP 200 with no JSON-LD block at all** — a ~383 KB page, same shape as a real one, just missing
the script tag. This is the module's "no JSON-LD on a 200" detail-loss case; it is what a
just-closed posting still named by a not-yet-regenerated sitemap would look like.

## 4. Two dates disagree on how trustworthy they are

The same posting fetched twice, 4 seconds apart:

| field | fetch 1 | fetch 2 | moved? |
|---|---|---|---|
| `datePosted` | `2026-05-11T12:26:04-07:00` | `2026-05-11T12:26:04-07:00` | no |
| `validThrough` | `2026-10-11T11:10:00-07:00` | `2026-10-11T11:13:56-07:00` | **yes, by ~the elapsed time** |

`datePosted` is real and stable and is used directly (with the sitemap's `<lastmod>` as a fallback
for the — unobserved in 80 samples, but not provably impossible — case it is absent).
`validThrough` moves by roughly the request's own elapsed time, the same fabrication signature
icims and eightfold's sitemap fallback both exhibit, so it is not read at all — matching every
other ATS in this repo that shows this pattern.

## 5. `jobLocation` is a list, often a long one — only the first is read

Distribution over the 50-id random sample: mean **2.24** locations per posting, **54% single**
(27/50), one posting listing **14**. This scraper reads only the first `Place`, the same policy
icims applies to its own (much rarer, 4/207) multi-location case and eightfold's sitemap fallback
applies to its own — there is no ranking signal in the JSON-LD to prefer one site over another, and
`Job.location` is one string, so the alternative is inventing a "N locations" summary this repo has
no precedent for.

## 6. Two fields this scraper cannot supply, both measured absent

**No `department` or team anywhere.** Zero of 80 sampled JSON-LD payloads carry
`occupationalCategory`, `industry`, `department`, or `team`. The sibling `kalil0321/ats-scrapers`
project's `meta.py` reads `teams`/`sub_teams` — but from the GraphQL *listing* response it
intercepts via a real browser, a surface this scraper does not reach (§0). There is no equivalent
on the JSON-LD detail page.

**No salary in the JSON-LD, though pay-transparency figures do exist on some US postings — just
not reachable here.** Zero of 80 sampled payloads carry `baseSalary`, unlike icims/oracle where
it is merely inconsistently present. A web search for a "Product Manager" posting's own snippet
surfaced a stated range (`$173,000/year to $241,000/year`) that is **not** in that posting's
server-rendered HTML or JSON-LD at all — confirmed by re-fetching
`/profile/job_details/1238249364564427/` directly and grepping for both dollar figures and
`baseSalary`/`compensation`/`pay_range`/`salary_range` keys: zero matches. So the figure a search
engine's own (JS-rendering) crawler saw is client-side-rendered by the same GraphQL app §0 already
rules out, not a second gap in the detail-page fetch — this scraper's "no salary" finding is
correct for every surface it can reach without a browser, not a claim that Meta discloses no
salary at all.

Both `department`/`team` and salary are real, measured gaps in what this surface can supply — not
something a future patch to this scraper can fix without the browser-automation path §0 rules out.

## 7. No rate limit found

Two bursts (20 requests at concurrency 10, 50 at concurrency 20) against the detail page: **80/80
HTTP 200**, up to 8.6 req/s, no `retry-after` or `ratelimit-*` header on any response, no latency
degradation with the wider burst. `detail_workers = 16` is chosen to match icims/oracle's own
"measured clean, no special justification needed" convention rather than to push this specific
measurement further — this is a single 952-job Board, not a fleet, so there is no cost pressure to
run wider.

## 8. Company name and slug

The ledger's one row holds `tenant="Meta"` (a real display name) and the host in `url`; `slug_from`
prefers `host_of(url)`, the same pattern oracle uses for its bare-label ledger rows. This sidesteps
`resolve_company()`'s generic slug-shaped-name repair entirely — the host `www.metacareers.com`
would read as slug-shaped, but the tenant name never does, so no `board_page`/`company_name`
pattern registration was needed for this single Board.

## 9. Re-verified live 2026-09-12: no sitemap index, no cache lag, no independent total to check against

**The scraper, run end-to-end against the live site right now:** 948 sitemap entries, 941 Jobs
built, 7 lost to the "no JSON-LD on a 200" stale-entry case (§3), `scraper.truncated is None` —
i.e. the built-in tolerance (`mark_truncated_unless_negligible`, ADR-0121) measured 941/948 =
99.26%, above the 99% bar, and did not flag a shortfall. Three independent fetches of the same
sitemap over roughly 24 hours read 952 (§2, first capture), 948, then 947 postings — a small,
smooth decline consistent with ordinary posting churn, not a sudden drop that would suggest a cap
or a broken walk.

**No sitemap index exists to miss a child of.** Re-confirmed on a fresh fetch: the root tag is
`<urlset>`, not `<sitemapindex>` (`grep -c sitemapindex` on the live response is 0), so — unlike
eightfold, which does follow a `sitemap_index`'s children — there is no second level this scraper
could fail to walk. This was true in §2's original capture and is still true now.

**The response is explicitly not cached.** `curl -I` on the sitemap: `Cache-Control: private,
no-cache, no-store, must-revalidate`, `Pragma: no-cache`, `Expires: Sat, 01 Jan 2000 00:00:00 GMT`.
Those headers rule out "the scraper is reading a stale cached snapshot that lags the live `/jobs`
UI" as an explanation for any undercount — the server is telling every client, including this
scraper, that this response is generated fresh and must not be reused.

**No independent numeric signal of the board's true size exists to check the sitemap count
against.** Checked and ruled out: the `/jobsearch/` page's `<meta name="description">` and
`og:description` are static marketing copy ("Search open positions at Meta across AI,
engineering, research, product, design, and more."), not a live count; there is no `totalCount`
or similar field anywhere in that page's server HTML (§0 already established it carries no
embedded job data at all); and a `site:metacareers.com/profile/job_details` web search returns a
handful of individually-ranked results with no aggregate count a search engine's own text
interface exposes. So there is no oracle to cross-check the sitemap's count against — the
sitemap itself, read fully and without a hidden pagination level, is the only enumeration surface
this ATS publishes without a browser session, and this scraper reads all of it.
