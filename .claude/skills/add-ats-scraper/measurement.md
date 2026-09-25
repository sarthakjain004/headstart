# Measurement checklist

Every question gets a number and a sample size, drawn from **both sides** of whatever the answer
keys on. Each question names the build it cost when it was assumed instead of measured.

Tools: `curl -sS -D- -A 'headstart/0.1'` for single requests; `headstart.network.http.fetch` (curl_cffi,
Chrome TLS) when a host walls plain curl; `headstart.network.browser_http` or a real browser's HAR when
the board is a client-rendered SPA and you need to see its XHRs. Write every probe script to
disk under `experiment/{ats}-{surface}/` so the numbers can be re-derived.

**Measure the rate limit (Q19) before any parallel probe.** ADP allows 200 requests per fixed
60-second window across every tenant; a parallel census trips it within seconds and poisons
every measurement taken after.

## Identity

1. **What is a Board's slug?** A subdomain label, a path segment, a host, a URL, a numeric id? Is
   it case-sensitive? Is there more than one host or regional pod for the same tenant
   (`*.eu.`, `*.de`)? Does one tenant run several sites (csod `careersite/{n}`, Workday sites,
   Oracle `siteNumber`) — is the Board the tenant or the site? Sum the per-site counts against
   the tenant-wide union: csod's were 58,083 against 42,534, so per-site Boards would have served
   15,549 rows twice. Is the key in a query string
   (ADP's `cid`/`ccId`)? The slug is the URL, the API key and the discovery key at once when you
   can make it so (pyjamahr's uuid turned out to be a detour — the API took the path slug).
   A compound slug follows workday's `slug_from`/`board_key` override (`workday.py`).
   Board keys compare case-folded (`scrapable_boards.py`), so two slugs differing only in case are
   one Board; and `board_identity.board_of` splits a Job id on its last `:`, so measure whether
   native ids ever contain one (upstream ADP keyed on `itemID`, which does on 8 of 2,069 rows;
   the all-digit `ExternalJobID` does not).
2. **Which slug spellings does discovery produce**, and does `slug_from(tenant, url)` normalise
   all of them to one? Tenant-key proliferation (#220) and stale casing pairs (#226) both came
   from a discovery source emitting a second spelling of one Board.

## Listing

3. **Where does the public Board really live?** Open a real tenant's careers page in a browser
   and follow one job link to where it lands. The Board can sit on another product's domain:
   ClearCompany's lands on HRM Direct (`{slug}.hrmdirect.com`), which neither upstream
   implementation had found.
3a. **Which listing surfaces does it have?** The JSON the board's own page calls — and probe its
   parameters for undocumented flags (Breezy's `/json?verbose=true` adds the full description
   the plain listing lacks); `robots.txt` (it names surfaces — Zoho's RSS feed was found there
   after a thorough probe missed it); `sitemap.xml`, RSS, and server-rendered HTML. Pick the
   cheapest complete surface `robots.txt` allows, and say why the others lost (icims reads only
   its sitemap: the Boards whose HTML walk 403'd were exactly the ones serving `Disallow: /`).
   Ask each surface the way a browser does: pages can negotiate on `Accept` (Pinpoint's posting
   pages answer `BaseScraper._get`'s default `Accept: application/json, text/html` with 406).
4. **How does it paginate, and what terminates the walk?** Does the stated total match the rows
   served on every Board you measured? Does `hasMore`/`next` tell the truth (oracle's `hasMore`
   was false on a 248-posting Board)? Is there a page-size clamp (phenom silently clamps `size`
   at 500) or a hard window (phenom stops at `from + size >= 10000`, with the total reading 0
   past it)? Where does the offset start (ADP's `$skip` counts from 1: `$skip=0` returns one row
   short, and upstream's `0, 19, 39, …` walk read a row twice)? Measure on the **largest** Board you can find, not a typical one.
5. **Is any parameter a filter rather than an address?** A wrong filter value can still answer
   200 with a well-formed empty envelope (oracle's hardcoded `siteNumber=CX_1` was wrong for 929
   of 1,331 Boards, silently; ADP's `lang` returns an empty list with no total for a language
   the center doesn't post in — Lifemark reads 0 under `en_US` and 460 under `en_CA`).
6. **Does the listing carry the description, and is it truncated?** Compare listing text length
   against the detail's on the same postings (oracle's listing capped at exactly 1,000 chars;
   phenom's listing has only a ~350-char teaser).
7. **Are rows returned that the public board hides?** Compare the API's rows against what the
   board page renders (pyjamahr's `published_internally`: 112 rows the board filters out).

## Dead versus empty

8. **What does an unknown slug return, and what does a live Board with zero openings return?**
   If both are `200` with an empty list (pyjamahr, gem, bamboohr — an empty body), find the
   request that tells them apart — usually the board page's 404 — and measure it on real dead
   tenants from the pool, not on an invented slug alone. `GET /` returning 200 proves nothing: 9
   of 12 Boards already known dead answered it with 200 (#160). On a wildcard DNS zone
   (`*.breezy.hr`) no tenant is ever unresolvable, so a DNS failure is the resolver, never a
   verdict: the first 432-worker Breezy pass, reading DNS failures as dead, wrote 41 live Boards
   dead.
8a. **What does a user's click reach?** A listing can name postings whose links 404 or redirect
   off-platform: 20 Pinpoint Boards (1,273 postings) did when asked as a browser asks
   (`Accept: text/html`), while curl's `*/*` rendered them. Where that happens, probe liveness on
   a posting URL with a browser `Accept` — what the user would actually open — rather than on
   the board root.
8b. **Can an empty listing be wrong?** Re-fetch a sample of empty listings: Pinpoint's came back
   spuriously empty on 12 of 6,030 fetches, so its scraper and prober both ask again before
   believing an empty one.
9. **Where does a departed tenant go?** A redirect to the vendor's marketing site, a parked
   page, a 410? That is the dead verdict the prober keys on. The prober's `_get` follows
   redirects, so a redirect-dead tenant reads as a 200 of marketing HTML — `body-unparseable`,
   UNKNOWN forever — unless the probe asks with `_fetch(..., allow_redirects=False)` and reads
   `Location` (`_sr_board_host` in `check_liveness.py` is the model).

## Detail

10. **Is there a per-Job detail request, and what does it add?** Tabulate, per `Job` field, the
    share of postings that carry it on the listing versus the detail, across many Boards.
11. **What does the detail need?** A tenant key, a header, a query flag (icims' detail without
    `in_iframe=1` returns an 80 KB wrapper with no JSON-LD — a silent empty, not an error)? What
    charset does it arrive in (443 of 510 HRM Direct pages were cp1252, not UTF-8, and HRM
    Direct's `xml.php` mixes cp1252, UTF-8 and double-encoded bytes in one document)?
11b. **Is there a token?** For a token scraped from the page (csod embeds a per-corp JWT in
    `csod.context.token`, with an API host on a regional pod such as `eu-fra.api.csod.com`):
    its lifetime, whether it is per tenant or per site, what an expired one returns (a 401, or a
    200 empty), and which host the API calls go to — gate and rate-limit on that host. Does a
    second host want a value from it as a cookie (csod's US pods: `ASP.NET_SessionId={jwt.aud}` on the
    tenant host, 14 of 14 401 without it and 40 of 40 200 with it)?
12. **Can the tech gate run before the detail?** Does the listing state `title` and
    `department`, and does the detail override either? Exact, approximation (measure the recall
    loss on real tech postings) or no gate — CONTEXT.md's Detail-pass entry lists every
    scraper's verdict and why.

## Fields

13. **Dates.** Is `posted_at` real? Fetch the same postings twice, seconds apart: a date that
    moves by the elapsed time is fabricated (icims fabricated it on 22% of Boards; a
    lastmod-only fallback served dates up to 2,437 days late). Check `validThrough`-style fields
    against postings still listed.
14. **Remote.** Which field states it, and does it agree with the location text? Measure the
    disagreement rate; a native boolean can be dead (pyjamahr's `remote` was false on all 102
    REMOTE postings). Hybrid is not remote (`Job.remote = None`, ashby's rule).
15. **Salary.** What shape does the string take, which period and currency does it state, and
    does `salary.extract(field, None, ats="{ats}")` read it correctly? A lone ceiling must not be
    served as a floor. A shape the generic parser misreads needs a `_field_{ats}` parser.
16. **Experience and employment type.** Is there a native field, how populated is it, and what
    are its observed values (bamboohr's `minimumExperience`: 97.9% populated, unread upstream)?
    Run `employment_type_filter.flags(v)` on every observed employment-type value: the filter matches
    substrings (`full`, `part`, `contract`/`freelance`, `intern` but not `international`;
    `permanent` counts as full-time unless it says `part`), so "FT" or "Temporary" reach no
    filter until mapped to a label.
17. **Location.** How many places can one posting name, and where does each live — a list, or
    the same posting repeated once per place (ClearCompany's `xml.php`)? Join every one ("; ");
    a posting cut to its first location fails the location filter everywhere else (workable,
    uber, amazon and keka shipped that, fixed in #561/#564).
17a. **Department.** Which field states it, on which surface, and on what share of postings? It
    feeds the tech filter's rule 4, which promotes a vague title on a technical department;
    rippling and successfactors shipped it unpopulated and smartrecruiters null on 54.7% of
    postings (#561/#564), so their gate ran on the title alone.
18. **Company name.** Does any surface name the employer — the board page `<title>`, an API
    field? Check it names the employer and not a subsidiary or the vendor (phenom's per-posting
    `companyName` named Blue Dart on a DHL Board). An applicant always sees the employer, so
    before concluding no surface names it, capture every XHR and asset the rendered page loads,
    every field of the detail (nested too) and the apply flow's first step: ADP's name was in a
    `client-features` call (`ClientName`, 120 of 120 centers), after an initial report that no
    surface named the employer.
    A per-posting field on a page the steady-state scrape never fetches is not a Board-level
    name. If nothing does, the slug stays the name —
    which is acceptable for a readable label and not for an opaque one: `scrapable_boards.load`
    passes the ledger's `tenant` as the name, so a GUID slug (ADP's `cid`, before `ClientName`)
    would display as a GUID.
    An opaque slug with no name surface is a **checkpoint**.

## Operating limits

19. **Rate limit** (measured first — see the top). Ramp concurrency (1, 4, 16, 32, 64, 128) on
    one tenant and record req/s,
    latency and every non-200; find the knee and ship below it. Then ramp **across many
    tenants at once**: a shared edge can meter the whole platform (jazzhr's Cloudflare zone
    does; upstream Breezy's claimed wall at ~14 req/s did not reproduce at 113 req/s — measure
    it, don't inherit it). Refusals that span tenants
    need a `_SPANNING` + `_GATES` entry in `check_liveness.py` — an ungated jazzhr pass wrote
    2,740 `dead` rows, and 6 of 10 spot-checked were still live (#463). A bare 403 quota is a
    different mechanism: it reaches no gate unless its host is in `_QUOTA_403` (keyed by
    `_gate_key`; zwayam is the precedent). One fixed API host (ADP's `workforcenow.adp.com`) needs
    no `_SPANNING` entry — the auto-gate already keys it exactly. A status code does not imply its
    mechanism: freshteam's "429s" were 502s from a down origin.
20. **User-Agent.** Does `headstart/0.1` (the shared `USER_AGENT`) get 200? Do bare curl and
    python-requests defaults behave differently (zwayam hangs on them rather than failing)?
    Measure with curl: `-A 'headstart/0.1'`, no `-A`, and `-A python-requests/2.32`.
21. **Response size.** Bytes per listing page and per detail — step 6's cost estimate needs them.

## Population

22. **Tech share and volume.** Over a real sample of postings: rows per Hiring Board, and the
    share `headstart.tech_filter.is_tech(title, department)` keeps
    (`scripts/validate/ats_tech_yield.py` if it handles the ATS).
23. **Language.** Roughly what share is non-English? The index is English-only; a mostly
    non-English ATS is scraped but its rows are held out of the index.
24. **Overlap with Boards already held.** Is this ATS a skin over another one this repo already
    scrapes (phenom: 75 of 91 tenants were backed by Workday/SuccessFactors Boards we held)?
    Resolve each tenant's backing Board and join on registrable domain, then measure each
    collision before dropping it. For vanity-host tenants, once the ledger exists,
    `scripts/validate/cross_ats_duplicates.py` does the join and `dedupe_boards.py --ats {ats}`
    finds same-ATS aliases (ADR-0111); both need the slug to be a host, so they find nothing
    for a vendor-subdomain label.
