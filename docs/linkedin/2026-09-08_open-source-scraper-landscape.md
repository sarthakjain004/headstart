# Nine open-source LinkedIn scrapers: what they actually do

Read-only static analysis, 2026-09-08. No scraper was run, no request was sent to linkedin.com, no
dependency installed. Every claim below is either a code citation or a quote from a repo's own
README / issue tracker. Repos live in
`/Users/sarthakjain/Projects/HeadStart/experiment/linkedin-jobs/repos/`; paths in citations are
relative to that directory.

Clones arrived at depth 1; I ran `git fetch --unshallow` against **github.com** (not linkedin.com)
so `git log -20` and the GitHub issue queries below have real history behind them.

---

## Executive summary

1. **Only three of the nine are job scrapers that need no login.** JobSpy, VishwaGauravIn and the
   Scrapy playbook all hit the same unauthenticated guest fragment endpoint,
   `/jobs-guest/jobs/api/seeMoreJobPostings/search`. Everything else either requires a `li_at`
   session cookie or requires a full username/password login.
2. **Three of the nine are not job scrapers at all.** `kiryano_Scout` (604★) scrapes social
   *profiles* for lead-gen and has zero job code; `TufayelLUS_LinkedIn-Scraper` (286★) is a
   people/lead scraper; `joeyism_linkedin_scraper` (4,482★) is primarily a person/company scraper
   whose job module is thin and whose job tests are **skipped as broken**.
3. **Exactly two repos extract the external apply URL** — the link out to the company's own ATS.
   `speedyapply_JobSpy` reads it from a `<code id="applyUrl">` blob on the *unauthenticated*
   `/jobs/view/{id}` page (`jobspy/linkedin/__init__.py:337`), and `arshka` reads
   `applyMethod.companyApplyUrl` straight out of the authenticated Voyager JSON
   (`json_paths/data_variables.csv:9`). Both spinlud repos get it a third way — by *clicking* the
   apply button and reading the URL of the tab that pops open. Nobody else gets it at all.
4. **The anti-blocking machinery is thin almost everywhere, and concentrated in one repo.** There
   is no CAPTCHA solver, no `undetected-chromedriver`, no `playwright-stealth` /
   `puppeteer-extra-stealth`, and no residential-proxy vendor SDK in any of the nine. The only
   serious evasion work is hand-rolled, in `spinlud_py-linkedin-jobs-scraper`: headless-token
   masking, `navigator.webdriver` suppression, an adaptive pacer, and a 429 backoff ladder.
5. **Not one of the nine handles HTTP 999.** A `grep` for `999` across all nine returns only test
   fixtures and unrelated strings. They all key on 429, on an `authwall`/`checkpoint` redirect, or
   on a redirect to `/signup`. Whatever these projects are meeting in practice, the code says it is
   429 and redirect-to-login, not 999.
6. **The realistic per-query ceiling is ~1,000 results** and everyone who documents it agrees. JobSpy
   hard-codes `start < 1000` (`jobspy/linkedin/__init__.py:88`), spinlud names it
   `MAX_RESULTS_CEILING = 1000`. JobSpy's own README: *"All the job board endpoints are capped at
   around 1000 jobs on a given search. LinkedIn is the most restrictive and usually rate limits
   around the 10th page with one ip. Proxies are a must basically."*

---

## Q1. Endpoints, and whether a session cookie is required

**This is the load-bearing table.** "Guest" = no cookie of any kind; "li_at" = a LinkedIn session
cookie; "credentials" = the code posts a username and password.

| Repo | Endpoint family | Exact endpoint(s) | Logged-in cookie required? |
|---|---|---|---|
| **speedyapply_JobSpy** | guest HTML fragment + public job page | `/jobs-guest/jobs/api/seeMoreJobPostings/search` (`jobspy/linkedin/__init__.py:120`); `/jobs/view/{job_id}` (`:257`) | **No — and it actively strips cookies.** `create_session(..., clear_cookies=True)` (`:66`), and `RequestsRotating.request` does `self.cookies.clear()` on every call (`jobspy/util.py:77-78`) |
| **VishwaGauravIn_linkedin-jobs-api** | guest HTML fragment only | `https://${this.host}/jobs-guest/jobs/api/seeMoreJobPostings/search?` (`index.js:142`) | **No.** No cookie is ever set; headers at `index.js:239-252` carry only UA/Accept/Referer |
| **python-scrapy-playbook** | guest HTML fragment only, **through a paid proxy middleman** | `https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?...&start=` (`linkedin/spiders/linkedin_jobs.py:5`) | **No LinkedIn cookie — but a ScrapeOps API key is mandatory.** `SCRAPEOPS_PROXY_ENABLED = True` + `ScrapeOpsScrapyProxySdk` (`linkedin/settings.py:20-37`) |
| **spinlud_linkedin-jobs-scraper** (TS) | browser automation (Puppeteer) against `/jobs/search`, two strategies | `urls.jobsSearch = "https://www.linkedin.com/jobs/search"` (`src/scraper/constants.ts:4`), built at `src/scraper/LinkedinScraper.ts:92` | **Both.** `AnonymousStrategy` if `LI_AT_COOKIE` unset, `AuthenticatedStrategy` if set (`src/scraper/LinkedinScraper.ts:38-44`). The cookie injection: `await page.setCookie({ name: "li_at", value: config.LI_AT_COOKIE!, domain: ".www.linkedin.com" })` (`src/scraper/strategies/AuthenticatedStrategy.ts:375-378`) |
| **spinlud_py-linkedin-jobs-scraper** | browser automation (Selenium) against `/jobs/search`, **authenticated only** | `JOBS_SEARCH_URL = 'https://www.linkedin.com/jobs/search'` (`linkedin_jobs_scraper/utils/constants.py:4`); single-job via `?currentJobId=<id>` (`strategies/authenticated_strategy.py:1287-1290`) | **Yes, always.** `AnonymousStrategy` was **deleted in 6.0.0**: *"do not add a second strategy back for the no-credential case, because there is nothing to scrape without a credential"* (`CLAUDE.md`). Three credentials: `LI_AT_COOKIE`, or the pair `LI_RM_COOKIE` + `LI_BCOOKIE`, or a seeded Chrome profile (`linkedin_jobs_scraper/config.py:6-12`; injection at `utils/session.py:133-162`) |
| **arshka_LinkedIn-Job-Scraper** | **authenticated Voyager internal API** | search: `/voyager/api/voyagerJobsDashJobCards?decorationId=...JobSearchCardsCollection-187&count=100&q=jobSearch&...` (`scripts/fetch.py:44`); detail: `/voyager/api/jobs/jobPostings/{}?decorationId=...WebFullJobPosting-65` (`scripts/fetch.py:88`) | **Yes, plus username+password.** Selenium types real credentials from `logins.csv` (`scripts/fetch.py:19-33`), harvests the whole cookie jar into a `requests.Session`, and derives CSRF: `'Csrf-Token': session.cookies.get('JSESSIONID').strip('"')` (`scripts/fetch.py:57`) |
| **joeyism_linkedin_scraper** | browser automation (Playwright) against the logged-in UI | search `https://www.linkedin.com/jobs/search/` (`linkedin_scraper/scrapers/job_search.py:91`), then each `/jobs/view/{id}` (`scrapers/job.py:56`) | **Yes.** `login_with_cookie` sets `{"name": "li_at", "value": cookie_value, ...}` (`core/auth.py:206-208`), or `login_with_credentials` from `LINKEDIN_EMAIL`/`LINKEDIN_PASSWORD` (`core/auth.py:46-61`) |
| **kiryano_Scout** | **authenticated Voyager — profiles, no jobs** | `/voyager/api/identity/dash/profiles?q=memberIdentity&memberIdentity={username}` (`app/scrapers/linkedin.py:97`) | **Yes.** `client.cookies.set('li_at', cookie, domain='.linkedin.com')` (`app/scrapers/linkedin.py:57`) + CSRF lifted from `JSESSIONID` (`:62-64`) |
| **TufayelLUS_LinkedIn-Scraper** | **authenticated Voyager (REST + GraphQL) — people, no jobs** | `/voyager/api/search/blended?...resultType-%3EPEOPLE...` (`CompanyWise_Leads.py:118`); newer variant `/voyager/api/graphql?variables=(start:...)&queryId=voyagerSearchDashClusters.e1f36c1a2618e5bb527c57bf0c7ebe9f` (`LinkedIn Lead Scraper 2024 Edition/LinkedIn_Leads.py:92-94`) | **Yes.** Base variants log in with credentials against `/checkpoint/lg/login-submit` (`CompanyWise_Leads.py:36-62`); the "2024 Edition" and GUI variants take a pasted cookie header and regex the CSRF out of it: `'Csrf-Token': re.findall(r'JSESSIONID="(.+?)"', self.cookie)[0]` (`LinkedIn Scraper GUI/LinkedIn_GUI.py:106`) |

### Notes on the families

**`/jobs-guest/jobs/api/seeMoreJobPostings/search`** — three users, all with the identical shape:
GET with `keywords`/`location`/`f_*` filters and a `start` offset stepping by 25, response is an
HTML `<li>` fragment parsed with BeautifulSoup (JobSpy) or cheerio (VishwaGauravIn) or Scrapy
selectors. This is the only endpoint in the whole set that needs nothing at all.

**`/jobs-guest/jobs/api/jobPosting/{id}`** — **nobody uses it.** I grepped all nine for
`jobPosting` and the only hits are Voyager's `voyagerJobsDashJobCards` /
`com.linkedin.voyager.dash.jobs.JobPostingCard` type names, not the guest detail endpoint. JobSpy
gets guest job detail from the *public HTML page* `/jobs/view/{id}` instead, and explicitly detects
when that page bounces it: `if "linkedin.com/signup" in response.url: return {}`
(`jobspy/linkedin/__init__.py:262-263`).

**`/voyager/api/…`** — three users, all cookie-bound (arshka for jobs; Scout and TufayelLUS for
people). Note the shared shape that marks a real Voyager caller: `Accept:
application/vnd.linkedin.normalized+json+2.1`, `Csrf-Token` = the `JSESSIONID` cookie with its
quotes stripped, and an `X-Li-Track` client-version blob. arshka's is the only one aimed at jobs,
and it is the richest field set of the nine by a wide margin (see Q4).

**Third-party middlemen** — only one repo *depends* on one: the Scrapy playbook routes every
request through ScrapeOps and prices it in the README (*"The cost for one LinkedIn request is 30
credits… you would have only have enough to make 33 valid requests to linkedin using the free
plan"*, `README.md:28`). Two others merely advertise or reference one: VishwaGauravIn's README
carries a Proxycurl affiliate banner (`README.md:112-118`), and arshka leaves a bare Webshare
referral link as the last line of the fetch module (`scripts/fetch.py:139`). TufayelLUS's git log
shows commit `c2244e1 Delete ProxyCurl_Version directory` — it once had one and removed it.

---

## Q2. Anti-blocking machinery

The volume is the finding, so here it is honestly: **there is very little, and it is lopsided.**

**None of the nine contains**: a CAPTCHA solver (no 2captcha/anticaptcha/capsolver),
`undetected-chromedriver`, `selenium-stealth`, `playwright-stealth`, `puppeteer-extra-plugin-stealth`,
or an integration with Bright Data / Oxylabs / Smartproxy / ZenRows / ScrapingBee. Verified by
grep across all `.py`/`.js`/`.ts`/`.md`/`.txt`/`.toml`/`.json`.

**speedyapply_JobSpy** — round-robin proxy rotation and randomised inter-page delay, nothing more.
`RotatingProxySession` cycles a caller-supplied proxy list per request (`jobspy/util.py:32-52`,
`:80-86`); `TLSRotating` wraps `tls_client` with `random_tls_extension_order=True` for TLS
fingerprint variation (`jobspy/util.py:92`) — **but LinkedIn is deliberately not on that path**:
`is_tls=False` (`jobspy/linkedin/__init__.py:63`), so LinkedIn goes through plain `requests`. Delay
is `time.sleep(random.uniform(self.delay, self.delay + self.band_delay))` with `delay = 3`,
`band_delay = 4` (`jobspy/linkedin/__init__.py:49-50`, `:167`). The user-agent is a **single
hardcoded Chrome 120 string** (`jobspy/linkedin/constant.py:7`) — no rotation. `Retry` with
`status_forcelist=[500, 502, 503, 504, 429]` and `backoff_factor=5` (`jobspy/util.py:65-71`).

**VishwaGauravIn** — the most anti-blocking per line of code of the guest-endpoint three:
`"User-Agent": randomUseragent.getRandom()` on every batch (`index.js:240`), `await delay(2000 +
Math.random() * 1000)` between pages (`index.js:208`), exponential backoff `delay(Math.pow(2,
consecutiveErrors) * 1000)` with a 3-strike stop (`index.js:216-222`), an explicit 429 branch
(`index.js:265-267`), and a 1-hour in-process result cache (`index.js:12`). No proxies at all.

**python-scrapy-playbook** — outsources the entire problem. `ROBOTSTXT_OBEY = False`
(`linkedin/settings.py:18`), `CONCURRENT_REQUESTS = 1` (`:41`), and ScrapeOps' own proxy SDK and
retry middleware replacing Scrapy's (`:33-37`). No UA rotation, no delay, no cookie handling of its
own.

**spinlud_linkedin-jobs-scraper (TS)** — **its evasion is commented out.** The UA randomiser exists
(`src/utils/browser.ts:22-27`) but its only call site is disabled: `// await
page.setUserAgent(getRandomUserAgent());` (`src/scraper/LinkedinScraper.ts:236`). Worse, it launches
Chrome with `"--enable-automation"` (`src/scraper/defaults.ts:10`) — the switch that *sets*
`navigator.webdriver` — and with `"--disable-web-security"` (`:25`). `slowMo: 150` (`:33`), request
interception blocks tracking beacons (`LinkedinScraper.ts:245-249`), and a 429 produces a log line
and nothing else (`LinkedinScraper.ts:292-294`).

**spinlud_py-linkedin-jobs-scraper** — this is where all the real machinery lives, and it is the
single best measure of how hard LinkedIn is pushing back:

- **Headless masking.** Chrome's `HeadlessChrome/` UA token is replaced with the browser's *own*
  de-headlessed UA, resolved at runtime by spending one throwaway browser
  (`utils/chrome_driver.py:90-140`), applied at launch via `--user-agent` so **every tab inherits it
  — including the tab the apply-link click opens, which lands on LinkedIn carrying the session
  cookie** (`chrome_driver.py:44-49`).
- **`navigator.webdriver` suppression.** `--disable-blink-features=AutomationControlled` +
  `excludeSwitches: ['enable-automation']` + `useAutomationExtension: False`
  (`chrome_driver.py:57-59`) — precisely the opposite of what the TS sibling does.
- **An adaptive pacer**, shared across query threads under a lock because *"the limit is enforced
  per account"*: delay doubles on every 429 up to `min(10, slow_mo * 10)`s and eases by 1.5x only
  after 20 clean jobs (`utils/pacing.py:8-24`).
- **A 429 backoff ladder** `(5, 15, 45)` seconds with ±50% jitter, deliberately *not* full jitter
  (`strategies/authenticated_strategy.py:48-54`).
- **429 detection through Chrome's own error page**, since LinkedIn's 429 carries no body: the
  status survives on `performance.getEntriesByType('navigation')[0].responseStatus`
  (`authenticated_strategy.py:41-44`), and per-job throttling that never reaches a navigation is
  counted from `PerformanceResourceTiming.responseStatus` on the `voyager/api/…` XHRs the page
  itself makes.
- **A measured, negative result worth borrowing**: *"LinkedIn sends no `Retry-After` on a 429 and no
  `X-RateLimit-*` on anything, over 312 refusals captured passively over WebDriver BiDi"*
  (`CLAUDE.md`). So the ladder is invented because nothing observable tells you the right number.
- **Session-cookie handling as its own subsystem** — a persistent Chrome profile, a self-renewing
  `li_rm`+`bcookie` remember-me pair, mid-run session recovery capped at `MAX_SESSION_RECOVERIES = 2`.
  No proxy support at all, and no UA *rotation* (the UA is fixed, just de-headlessed).

**arshka** — a **session-cookie pool**, which nobody else has: N logged-in accounts from
`logins.csv`, round-robined per request (`scripts/fetch.py:46`, `:66`, `:135`), split by role
(cheap searches on 1-3 accounts, expensive detail fetches on the rest). Proxy support is written
and **commented out** (`scripts/fetch.py:111`, `:119`). `time.sleep(.3)` between detail calls
(`:136`); a 10-consecutive-error circuit breaker (`:129-130`). Its README is candid about why:
*"Utilize multiple proxies and accounts when running details_retriever.py… Run details_retriever.py
during periods of lower online activity, such as late-night hours and weekends."*

**joeyism** — a **browser warm-up** that visits google.com, wikipedia.org and github.com before
touching LinkedIn *"to appear more human-like… helps avoid LinkedIn security checkpoints"*
(`core/auth.py:17-43`), a `detect_rate_limit` that watches for `linkedin.com/checkpoint`, `authwall`,
a CAPTCHA iframe, and the strings `too many requests` / `rate limit` / `slow down` / `try again
later`, raising `RateLimitError(suggested_wait_time=3600)` (`core/utils.py:57-100`), and
`retry_async` with exponential backoff (`core/utils.py:16-54`). No stealth plugin, no proxy
support, plain `chromium.launch(headless=...)` (`core/browser.py:63-66`).

**kiryano_Scout** — proxy rotation (env var, a file of proxies, or free-proxy scraping via the `fp`
package), a 10-entry UA rotation list, `random_delay(1.5, 4.5)`, `test_proxy()` against httpbin, and
a retry decorator (`app/scrapers/stealth.py:11-155`). Applies to its whole scraper fleet, not
specifically LinkedIn.

**TufayelLUS** — essentially none: hardcoded UAs, hand-copied `X-Li-Page-Instance` / `X-Li-Track`
blobs, no delay, no proxy. Its README's advice is behavioural: *"Do not log in from the IP address
from where you don't usually login to your LinkedIn account, otherwise, it will trigger their
security system and won't let you log in."*

---

## Q3. Is each one alive?

| Repo | ★ | Last commit | Open issues | Verdict | Evidence |
|---|---|---|---|---|---|
| **speedyapply_JobSpy** | 4,247 | 2026-02-18 | 62 | **Maintained, LinkedIn path degrading** | Head commit is literally a LinkedIn repair: `fda080a fix(linkedin): add fallback for date parsing on new job listings (#343)`. But open issue #370 (2026-07-01) *"job_url_direct missing for linkedin… I noticed that my fallback on the apify also stopped working. Linkedin changed something about their DOM"* and #374 (2026-07-21) *"Job Description is empty for linkedin"* are both unfixed 5+ months after the last commit. Both are the `/jobs/view/{id}` guest-page path. |
| **spinlud_py-linkedin-jobs-scraper** | 495 | 2026-08-23 | 0 | **Alive and by far the best-maintained** | Dense recent feature history (CLI, `scrape_job`, geoId, adaptive pacing, filters), a project `CLAUDE.md` documenting measured LinkedIn behaviour, live e2e suites run before merge, zero open issues. |
| **kiryano_Scout** | 604 | 2026-09-05 | 2 | **Alive — but not a job scraper** | Active this month. Contains no job code at all; LinkedIn module is profile-only. |
| **python-scrapy-playbook** | 113 | 2026-01-21 | 1 | **Maintained-as-a-tutorial, not as a scraper** | Recent commit is `e007e83 Updated readme`. It is marketing collateral for ScrapeOps. Its one open issue (#1, 2023) is a user who never set the API key. Whether the LinkedIn spider still works is untestable from here — the paid proxy sits between it and the endpoint. |
| **joeyism_linkedin_scraper** | 4,482 | 2026-04-09 | **144** | **Alive for profiles; job path is self-declared broken** | Both job tests carry `@pytest.mark.skip(reason="Job search selector '.jobs-search__results-list' not found - LinkedIn page structure may have changed")` (`tests/test_job_scraper.py:10`, `:32`). The last five commits are all auth repairs (`fix(auth): use URL-based detection in is_logged_in() to handle A/B tested DOM`). 144 open issues, many long-stale (`#100 You are not logged in!`, 2021). The README's job example prints `job.title`/`job.company` but `JobSearchScraper.search()` returns a `List[str]` of URLs — the docs don't match the code. |
| **VishwaGauravIn_linkedin-jobs-api** | 323 | 2025-07-20 | 5 | **Dormant, ~14 months idle; likely still functional** | The endpoint it uses is the simplest possible and its git history shows three separate commits titled `linkedin checks bypass 🔥` plus `added rate limiting + handling error and rate limits + better headers` — i.e. it has been repaired against blocking before. No open issue reports it broken. Nothing has been touched since. |
| **spinlud_linkedin-jobs-scraper** (TS) | 186 | 2025-03-14 | 12 | **Effectively superseded by its Python sibling** | README carries an explicit stand-down: *"⚠ WARNING: due to lack of time, anonymous session strategy is no longer maintained. If someone wants to keep support for this feature and become a project maintainer, please be free to pm me."* (`README.md:160-161`). Its own open issue #51 (2026-08-23) is titled *"Parity with py-linkedin-jobs-scraper v7.0.10"*. Its UA rotation is commented out and it still launches with `--enable-automation`. |
| **arshka_LinkedIn-Job-Scraper** | 194 | 2023-12-28 | 3 | **Abandoned; the Voyager `decorationId`s are 2+ years stale** | Untouched for ~21 months. It pins exact Voyager decoration ids (`JobSearchCardsCollection-187`, `WebFullJobPosting-65`) and a hardcoded `clientVersion` of `1.13.5589` — all of which LinkedIn rotates. Treat the *field map* as the valuable artefact, not the running code. Its output is published as a Kaggle dataset, which is the honest way to consume it. |
| **TufayelLUS_LinkedIn-Scraper** | 286 | 2025-07-08 | 0 | **Dormant, and not a job scraper** | People/lead scraping only. Three generations of the same code in one repo (credentials → cookie → GUI), the newest pinning `queryId=voyagerSearchDashClusters.e1f36c1a2618e5bb527c57bf0c7ebe9f`, a hash LinkedIn rotates on deploy. |

**Cross-cutting**: the repair commits cluster in exactly two places — **selectors** and **auth**.
spinlud's own note is the general case: *"Selectors are the fragile surface… the git history is
mostly selector fixes."* Nobody's history shows a fight with an IP-level block; the fights are with
DOM churn and with session expiry.

---

## Q4. What fields do they get?

`✓` = extracted; `—` = not extracted; `(opt)` = only when an extra per-job request is made.

| Repo | id | title | company | location | description | posted date | salary | seniority | employment type | **external apply URL** |
|---|---|---|---|---|---|---|---|---|---|---|
| speedyapply_JobSpy | ✓ | ✓ | ✓ | ✓ | (opt) | ✓ | ✓ | (opt) | (opt) | **✓ (opt)** |
| VishwaGauravIn | — | ✓ | ✓ | ✓ | — | ✓ | ✓ | — | — | — |
| python-scrapy-playbook | — | ✓ | ✓ | ✓ | — | ✓ | — | — | — | — |
| spinlud TS | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | insights only | insights only | **✓ (opt)** |
| spinlud py | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | insights only | insights only | **✓ (opt)** |
| arshka | ✓ | ✓ | company_id | ✓ | ✓ | ✓ | ✓ (min/med/max + period + currency) | ✓ | ✓ | **✓** |
| joeyism | — | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — | — |
| kiryano_Scout | n/a — no jobs | | | | | | | | | |
| TufayelLUS | n/a — no jobs | | | | | | | | | |

### The apply URL, in detail — three mechanisms

**1. JobSpy — a hidden `<code>` blob on the unauthenticated public page.** This is the cheapest
mechanism of the three by a long way: one extra unauthenticated GET, no browser.

```python
# jobspy/linkedin/__init__.py:71
self.job_url_direct_regex = re.compile(r'(?<=\?url=)[^"]+')

# jobspy/linkedin/__init__.py:330-345
def _parse_job_url_direct(self, soup: BeautifulSoup) -> str | None:
    job_url_direct = None
    job_url_direct_content = soup.find("code", id="applyUrl")
    if job_url_direct_content:
        job_url_direct_match = self.job_url_direct_regex.search(
            job_url_direct_content.decode_contents().strip()
        )
        if job_url_direct_match:
            job_url_direct = unquote(job_url_direct_match.group())
    return job_url_direct
```

Gated behind `linkedin_fetch_description=True`, and the README prices it: *"fetches full description
and direct job url for LinkedIn (Increases requests by O(n))"* (`README.md:107-108`).
**Caveat, and it matters**: open issue #370 (2026-07-01) says this field is now missing, attributed
to a LinkedIn DOM change. I did not test it — do not assume it still works.

**2. arshka — a first-class field in the Voyager JSON.** No parsing, no heuristics:

```csv
# json_paths/data_variables.csv:9,10,29
['data']['applyMethod']['companyApplyUrl'],application_url,0,,jobs
['data']['applyMethod']['$type'],application_type,2,,jobs
['data']['sourceDomain'],posting_domain,0,,jobs
```

`DatabaseStructure.md` glosses them: `application_url` = *"URL where applications can be
submitted"*, `application_type` = *"Type of application process (offsite, complex/simple onsite)"*,
`posting_domain` = *"Domain of the website with application"*. **That triple is exactly an ATS
identification signal** — an offsite `application_type` plus a `posting_domain` of
`boards.greenhouse.io` / `jobs.lever.co` / `myworkdayjobs.com` names the board directly. It costs a
logged-in Voyager call per job.

**3. Both spinlud repos — click the button and read the popup tab's URL.** Expensive but robust to
DOM changes in the link itself:

```python
# spinlud_py-linkedin-jobs-scraper/linkedin_jobs_scraper/strategies/authenticated_strategy.py:1246-1271
driver.execute_script(r'''
        const applyBtn = document.querySelector(arguments[0]);
        if (applyBtn) { applyBtn.click(); return true; }
        return false;
    ''', Selectors.applyBtn)

if len(driver.window_handles) > 1:
    while elapsed < timeout:
        targets_result = driver.execute_cdp_cmd('Target.getTargets', {})
        ...
                if target['attached'] and target['type'] == 'page' and target['url'] and \
                        target['url'] != current_url:
                    driver.execute_cdp_cmd('Target.closeTarget', {'targetId': target['targetId']})
                    return {'success': True, 'apply_link': target['url']}
```

Off by default (`apply_link=False`), because it costs a click, a tab open, up to 4s of polling and a
tab close **per job**. The TS sibling does the same via CDP (`AuthenticatedStrategy.ts:286-304`) and,
in the *anonymous* strategy only, additionally reads a plain attribute —
`a[data-is-offsite-apply=true]` → `getAttribute("href")` (`AnonymousStrategy.ts:26-28`, `:409-412`).
That anonymous variant is the one no-login selector for an apply link anywhere in the nine, and it
sits in the strategy its own README declares unmaintained.

The py repo also ships a validator that treats a LinkedIn-hosted apply link as suspicious:
`if 'linkedin.com/jobs/search' in data.apply_link:` … `'  <-- linkedin host, check for
mis-capture' if 'linkedin.com' in r.apply_link` (`tests/manual/validate_fields.py:136`, `:199`) —
i.e. a correctly captured apply link is expected to be **off-LinkedIn**.

### Other field notes

- **spinlud py** has the richest browser-derived set: `EventData` carries `job_id, link, apply_link,
  title, company, company_link, company_employee_count, company_img_link, place, description,
  description_html, date, date_text, insights, salary, is_easy_apply, applicant_count, benefits,
  reposted` (`linkedin_jobs_scraper/events/events.py:40-62`). Salary is scraped as a **display
  string** by regex over the fit-level buttons / salary rail card
  (`authenticated_strategy.py:127-150`), not a structured min/max. Seniority and employment type
  are not separate fields — they arrive mixed into the `insights` string list. It explicitly has
  **no `skills` field**: *"LinkedIn no longer exposes required-skill names anywhere in the DOM, only
  a Premium-gated 'N of M skills match' control"*.
- **JobSpy** parses seniority (`Seniority level`), employment type (`Employment type`), industry and
  job function from the public page's `description__job-criteria-*` spans
  (`jobspy/linkedin/util.py:17-85`) — all only on the optional detail fetch. Salary comes from the
  search card's `job-search-card__salary-info` and is split naively on `-` with the currency taken
  from the first character (`jobspy/linkedin/__init__.py:176-190`).
- **arshka** is the only one with genuinely structured compensation (`min/med/max`, `pay_period`,
  `currency`, `compensation_type`), plus `applies`, `views`, `expireAt`, `closedAt`,
  `formattedExperienceLevel`, `workRemoteAllowed`, `jobFunctions`, `industries`, `benefits`. Four
  columns are marked EMPTY in its own docs (`inferred_benefits`, `years_experience`, `job_region`,
  `degree`).
- **joeyism** is the thinnest: `Job(linkedin_url, job_title, company, company_linkedin_url, location,
  posted_date, applicant_count, job_description, benefits)` (`linkedin_scraper/models/job.py:13-21`)
  — no id, no salary, no apply link.

---

## Q5. Rate limits and volume ceilings

**~1,000 results per query is the consensus hard ceiling**, and it is encoded twice:

```python
# speedyapply_JobSpy/jobspy/linkedin/__init__.py:87-89
continue_search = (
    lambda: len(job_list) < scraper_input.results_wanted and start < 1000
)
```

```python
# spinlud_py-linkedin-jobs-scraper/linkedin_jobs_scraper/strategies/authenticated_strategy.py:32-36
PAGINATION_SIZE = 25
# LinkedIn stops serving results past start=1000, so an unlimited run (limit=0) paginates
# no further than this regardless of how many results the query reports.
MAX_RESULTS_CEILING = 1000
```

**Documented thresholds, quoted:**

- JobSpy README, *Notes*: *"All the job board endpoints are capped at around 1000 jobs on a given
  search."* / *"LinkedIn is the most restrictive and usually rate limits around the 10th page with
  one ip. Proxies are a must basically."* (10 pages × 25 = 250 results before the first block, on a
  single IP.)
- JobSpy README FAQ: *"Received a response code 429? … All of the job board sites are aggressive
  with blocking. We recommend: Wait some time between scrapes (site-dependent). Try using the
  proxies param to change your IP address."*
- JobSpy README, *LinkedIn limitations*: only **one** of `hours_old` / `easy_apply` may be used in a
  single search.
- JobSpy open issue #258 is the best real-world volume datapoint in the whole set: a user reports
  **2,422 jobs visible in LinkedIn's UI for one company, and a maximum of 139 scraped**.
- spinlud TS README: *"429 too many requests… this is especially true when using authenticated
  sessions where the rate limits are much more strict"*, with the rule of thumb *"add 100 ms for
  each concurrent query"* on top of `slowMo`.
- spinlud py README: *"On every 429, the delay between jobs doubles, up to `min(10, slow_mo * 10)`
  seconds. After 20 jobs in a row without a 429, the delay shrinks back towards `slow_mo`… When a
  whole page is throttled, the scraper waits and asks for it again: first 5s, then 15s, then 45s."*
  Defaults: `slow_mo=0.8`, `max_workers=1`, and *"Increasing default concurrency, or lowering that
  default, is a behavioural regression, not an optimisation."*
- spinlud py, on session lifetime (this is a real ceiling, not a rate limit): a bare `LI_AT_COOKIE`
  is *"retired after roughly a hundred job loads"*, and the live test suites *"exhaust it in about
  two runs"* — hence the whole `li_rm`+`bcookie` remember-me apparatus (`CLAUDE.md`).
- python-scrapy-playbook: *"The cost for one LinkedIn request is 30 credits… 1000 credits so you
  would have only have enough to make 33 valid requests"*; `CONCURRENT_REQUESTS = 1` because *"Max
  Concurrency On ScrapeOps Proxy Free Plan is 1 thread"*.
- arshka README: *"Each search generates approximately 25-50 results, all of which must be
  individually queried"*; defaults `MAX_UPDATES = 25` per cycle with `SLEEP_TIME = 60`
  (`details_retriever.py:9-10`) and `time.sleep(.3)` between detail calls — **so the shipped default
  is ~25 jobs/minute of detail enrichment**, and it needs an account pool to hold even that.
- TufayelLUS README (people, not jobs): *"Result is limited to 10,000 records only (this is a
  limitation from LinkedIn's side)"*; its GUI paginates `range(0, 1000, 10)`
  (`LinkedIn Scraper GUI/LinkedIn_GUI.py:125`).
- VishwaGauravIn encodes **no ceiling** — it pages until the endpoint returns empty
  (`index.js:186-224`), stopping only after 3 consecutive errors.

---

## Q6. Licences

Three repos ship **contradictory** licence signals. If any code were ever to be borrowed, this is the
part to read twice.

| Repo | `LICENSE` file | Package metadata | GitHub API | Verdict |
|---|---|---|---|---|
| speedyapply_JobSpy | MIT (`LICENSE:1`, © 2023 Cullen Watson) | — | MIT | **MIT. Clean.** |
| spinlud_py-linkedin-jobs-scraper | MIT text (`LICENSE`, header reads "© 2018 The Python Packaging Authority" — a template artefact) | `license = "MIT"` (`pyproject.toml:11`) | MIT | **MIT.** Consistent, though the copyright line is a leftover template. |
| spinlud_linkedin-jobs-scraper (TS) | **no LICENSE file** | `"license": "MIT"` (`package.json:24`) | NONE | **Ambiguous.** package.json asserts MIT but no licence text is distributed and GitHub detects none. |
| VishwaGauravIn_linkedin-jobs-api | **Apache-2.0** (`LICENSE:1`) | `"license": "MIT"` (`package.json:23`) | Apache-2.0 | **Three-way contradiction** — the README badge additionally claims *GPL v3* (`README.md:7`). Do not rely on any one of them. |
| joeyism_linkedin_scraper | **GPL-3.0** (`LICENSE:1`) | `license = {text = "Apache 2.0"}` (`pyproject.toml:11`), `license='Apache 2.0'` (`setup.py:44`) | GPL-3.0 | **Contradiction, and the copyleft side wins by default.** GitHub reads the LICENSE file as GPL-3.0. **Treat as GPL-3.0 — do not borrow code.** |
| kiryano_Scout | MIT (`LICENSE:1`, © 2026 Scout) | — | MIT | **MIT. Clean.** |
| TufayelLUS_LinkedIn-Scraper | **GPL-3.0** (`LICENSE:1`) | — | GPL-3.0 | **GPL-3.0. Do not borrow code.** |
| arshka_LinkedIn-Job-Scraper | **none** | — | NONE | **No licence — all rights reserved.** Not reusable. Its *published Kaggle dataset* is separately licensed; the code is not. |
| python-scrapy-playbook | **none** | — | NONE | **No licence — all rights reserved.** Not reusable. |

**Copyleft to avoid**: `joeyism_linkedin_scraper` (GPL-3.0) and `TufayelLUS_LinkedIn-Scraper`
(GPL-3.0). **No licence at all** (worse than GPL for reuse): `arshka_LinkedIn-Job-Scraper` and
`python-scrapy-playbook`. **Safe to borrow from**: JobSpy (MIT), spinlud py (MIT), Scout (MIT), and
— with the caveat that no licence text ships — spinlud TS.

---

## Uncertainties

Stated plainly, since none of this was executed:

- **Whether any of these currently work is untested.** Everything above is what the code *attempts*.
  JobSpy #370/#374 and joeyism's skipped tests are the repos' own admissions of breakage; absence of
  such an admission elsewhere is not evidence of health.
- **The `<code id="applyUrl">` blob**: I read the parser, not a live page. JobSpy's own open issue
  says the field is now missing. Whether the guest `/jobs/view/{id}` page still carries the external
  apply URL for an unauthenticated caller is the single most important open question here, and it is
  answerable only by fetching a real page.
- **arshka's Voyager `decorationId` values** are almost certainly stale (pinned in 2023). The
  *shape* of the response — `applyMethod.companyApplyUrl`, `sourceDomain` — is the durable part; the
  decoration ids are not.
- **spinlud TS's anonymous strategy** would be the one no-cookie path to an apply link
  (`a[data-is-offsite-apply=true]`), but its README declares that strategy unmaintained, so I would
  not assume the selector is current.
- **python-scrapy-playbook** cannot be assessed on its merits: ScrapeOps sits between it and
  LinkedIn, so its liveness measures ScrapeOps' proxy fleet, not the LinkedIn endpoint.
