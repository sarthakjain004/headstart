/**
 * Constants for the Talentera careers platform.
 *
 * Talentera (talentera.com, by Bayt — a MENA-region talent-acquisition / applicant-tracking
 * platform) powers each customer's branded, public, unauthenticated candidate-facing career
 * portal on a **per-tenant sub-domain** of the shared root, addressed by a tenant **codename**:
 *
 *   https://{codename}.talentera.com/                                  (branded career portal)
 *   https://{codename}.talentera.com/en/job-search-results/            (search-results board)
 *   https://{codename}.talentera.com/en/{country}/jobs/{slug}-{id}/    (per-role public detail page)
 *   https://{codename}.talentera.com/en/job-application/?jb_id={id}    (per-role public apply page)
 *
 * Unlike a slug-on-shared-host ATS, Talentera addresses a tenant by a sub-domain codename
 * (e.g. `careerroyaljet`, `care`, `arcelormittal`) under `talentera.com`. The candidate-facing
 * board is a client-rendered (Vue) SPA backed by a **public, anonymous JSON endpoint** the board
 * itself consumes — the portal's own job-search manager:
 *
 *   Job search (host `{codename}.talentera.com`):
 *     GET /app/control/byt_job_search_manager?action=1&token={t}&query={qs}&body=job-search-results&lan={lang}
 *       → { totalJobs, currentPage, view, jobs: [ { id, title, desc, … } ], cluster, totalVacancies }
 *
 *   The board first loads the public `/en/job-search-results/` page, which embeds a short-lived
 *   guest `USER_token` (no login — an anonymous visitor token the SPA echoes back on the search
 *   call). The adapter mirrors that two-step exactly: GET the results page to mint the guest
 *   token + session cookies, then GET the search manager with `action=1` and that token. The
 *   envelope exposes `totalJobs` and `currentPage`, so the adapter drains pages by incrementing
 *   the `page` query token until it has collected `totalJobs` (or hits a page cap).
 *
 *   Each `jobs[]` role carries a string `id` (the stable ATS id — the same numeric id that
 *   appears as `JB{id}` on the detail page and as `jb_id={id}` on the apply page), a `title`,
 *   a `desc` (HTML body), and — depending on the tenant's card template — optional `location`,
 *   `date` / `postedDate`, `type` (employment type), `category` (≈ department), `country`,
 *   `city`, and a `url` (the canonical `/en/{country}/jobs/{slug}-{id}/` detail path). The
 *   adapter reads each field defensively (all optional) and falls back to deriving the detail
 *   and apply URLs from the role id when the card omits an explicit `url`.
 *
 * The adapter resolves the tenant codename from `companySlug` or from a `companyUrl` on a
 * `{codename}.talentera.com` host. An unknown codename, a tenant with no open roles, an empty
 * board, or the anti-automation guard (which answers `{ status: 'fail', url: '…' }` when the
 * guest token is rejected) all degrade naturally to an empty result. A fetch error, an HTTP
 * 4xx/5xx, a DNS failure, or a malformed body degrades to an empty / partial result rather than
 * throwing, so a single bad tenant never nukes a batch run.
 *
 * Surface confidence (researched + verified live 2026-06-04, no authentication):
 *  - Confirmed the platform + sub-domain codename addressing
 *    (`{codename}.talentera.com/`, `/en/{country}/jobs/{slug}-{id}/`,
 *    `/en/job-application/?jb_id={id}`) across multiple live tenants
 *    (`care`, `careerroyaljet`, `arcelormittal`, `enterprisedemo`, `demo`).
 *  - Confirmed the public board bundle drives a Vue SPA whose `getJobsInfo()` GETs
 *    `/app/control/byt_job_search_manager` with `{ action: 1, token: USER_token, query,
 *    body: 'job-search-results', lan: 'en' }` and reads `res.jobs`, `res.totalJobs`,
 *    `res.cluster`, `res.totalVacancies`.
 *  - Confirmed `GET /app/control/byt_job_search_manager?action=1&token={USER_token}&query=&body=job-search-results&lan=en`
 *    on `careerroyaljet.talentera.com` (after loading `/en/job-search-results/` to mint the
 *    guest token + cookies) returns
 *    `{ "totalJobs": 1, "currentPage": 1, "view": "list", "jobs": [ { "id": "5438332",
 *    "title": "Flight Attendant-VIP", "desc": "Step into a world where luxury meets the sky…" } ] }`
 *    anonymously, and that a rejected/expired guest token answers
 *    `{ "status": "fail", "url": "/en/unauthorized-access/" }` (degrade-to-empty guard).
 *  - The guest token is short-lived and the portal applies an anti-automation guard, so the
 *    JSON path can intermittently degrade for an automated client; the detail page
 *    (`JB{id}` reference, `jb_id={id}` apply link) is a stable public fallback addressing.
 *    verified=true.
 */

/** Root domain — used to recognise tenant URLs passed via `companyUrl`. */
export const TALENTERA_ROOT_DOMAIN = 'talentera.com';

/** Public candidate-facing portal scheme — tenant boards live at `{codename}.talentera.com`. */
export const TALENTERA_SCHEME = 'https';

/** Builds the tenant's public career-portal origin from its codename. */
export const talenteraOrigin = (codename: string): string =>
  `${TALENTERA_SCHEME}://${encodeURIComponent(codename)}.${TALENTERA_ROOT_DOMAIN}`;

/**
 * Path of the public search-results board page. Loading it mints the anonymous guest
 * `USER_token` + session cookies the SPA needs before it may call the search manager.
 */
export const TALENTERA_RESULTS_PATH = '/en/job-search-results/';

/** Builds the tenant's public search-results board URL. */
export const talenteraResultsUrl = (codename: string): string =>
  `${talenteraOrigin(codename)}${TALENTERA_RESULTS_PATH}`;

/** Path of the public, anonymous job-search manager the board's SPA consumes. */
export const TALENTERA_SEARCH_PATH = '/app/control/byt_job_search_manager';

/** Builds the public job-search manager endpoint URL for a tenant codename. */
export const talenteraSearchUrl = (codename: string): string =>
  `${talenteraOrigin(codename)}${TALENTERA_SEARCH_PATH}`;

/**
 * `action` value the SPA sends to the search manager to fetch a page of roles. Mirroring the
 * board's own `getJobsInfo()` call keeps us on the documented public board feed.
 */
export const TALENTERA_SEARCH_ACTION = 1;

/** `body` value the SPA sends — selects the public search-results listing template. */
export const TALENTERA_SEARCH_BODY = 'job-search-results';

/** Default UI language segment / `lan` token. */
export const TALENTERA_DEFAULT_LANG = 'en';

/**
 * Regex that extracts the anonymous guest `USER_token` the board embeds in the results page
 * (`var USER_token = '…';`). The SPA echoes this back on every search-manager call; an absent
 * or rejected token degrades the JSON path to empty.
 */
export const TALENTERA_TOKEN_REGEX = /USER_token['"\s]*[:=]\s*['"]([A-Za-z0-9]+)['"]/i;

/** Builds a public per-role detail URL from a country segment, title slug, and role id. */
export const talenteraJobDetailUrl = (
  codename: string,
  country: string,
  slug: string,
  id: string,
): string =>
  `${talenteraOrigin(codename)}/${TALENTERA_DEFAULT_LANG}/${encodeURIComponent(country)}/jobs/${encodeURIComponent(
    slug,
  )}-${encodeURIComponent(id)}/`;

/** Builds a public per-role apply URL from a role id (`/en/job-application/?jb_id={id}`). */
export const talenteraApplyUrl = (codename: string, id: string): string =>
  `${talenteraOrigin(codename)}/${TALENTERA_DEFAULT_LANG}/job-application/?jb_id=${encodeURIComponent(id)}`;

/**
 * Default internal results cap. Mirrors the sibling ATS adapters: when a caller omits
 * `resultsWanted` entirely we ingest up to 100 of the tenant's open roles.
 */
export const TALENTERA_DEFAULT_RESULTS = 100;

/**
 * Hard ceiling on board pages fetched per scrape. The search manager reports `totalJobs`, so
 * the adapter normally drains by page until it has collected the reported total; the ceiling
 * guards against an unbounded / looping pager when the total is missing or untrustworthy.
 */
export const TALENTERA_MAX_PAGES = 25;

/**
 * Number of roles requested per board page. The SPA's default card template renders a fixed
 * page size; we request the same window via the `per_page` query token so pagination tracks
 * the board's own behaviour.
 */
export const TALENTERA_PAGE_SIZE = 50;

/**
 * Upper bound (seconds) on the per-request HTTP timeout. An unresponsive Talentera tenant host
 * can connect-then-hang, so we cap the shared client's 60s default to keep graceful-degradation
 * well inside callers' budgets; a healthy tenant responds in well under a second. A caller may
 * request a SHORTER timeout — we only bound the upper end.
 */
export const TALENTERA_DEFAULT_TIMEOUT_SECONDS = 15;

/**
 * Default request headers. The board's own SPA sends a browser-like UA, a JSON Accept, and an
 * `X-Requested-With: XMLHttpRequest` marker on its search-manager XHR; mirroring keeps us on
 * the public anonymous path.
 */
export const TALENTERA_HEADERS: Record<string, string> = {
  Accept: 'application/json, text/javascript, */*; q=0.01',
  'User-Agent':
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129 Safari/537.36',
  'Accept-Language': 'en-US,en;q=0.9',
  'X-Requested-With': 'XMLHttpRequest',
};

/**
 * Detects remote / home-working roles across the title, location, category, and employment-type
 * fields the role card may expose.
 */
export const TALENTERA_REMOTE_REGEX =
  /\b(remote|home[\s-]?office|telecommute|teleworking|wfh|work\s*from\s*home|fully\s*remote|anywhere)\b/i;
