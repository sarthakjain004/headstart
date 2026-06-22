/**
 * Constants for the MokaHR careers platform.
 *
 * MokaHR (mokahr.com, China) is a leading China-region recruitment / ATS SaaS. Each
 * customer ("organisation") publishes a branded, public, unauthenticated
 * candidate-facing career site addressed by a `{tenant}` company slug plus a numeric
 * `{orgId}` organisation identifier, served from the shared application host:
 *
 *   https://app.mokahr.com/social-recruitment/{tenant}/{orgId}      (social-recruitment site)
 *   https://app.mokahr.com/campus-recruitment/{tenant}/{orgId}      (campus-recruitment site)
 *   https://{tenant}.mokahr.com/social-recruitment/{tenant}/{orgId} (alt custom host)
 *
 * The career site is a client-rendered single-page app whose open roles are served by a
 * public, anonymous JSON listing endpoint on the platform API host. The documented
 * public shape is:
 *
 *   GET https://api.mokahr.com/api-platform/v1/jobs/{orgId}?mode=social&limit=&offset=
 *
 * which returns a standard MokaHR envelope `{ code, msg, data }`, where `data` carries
 * the open roles (an array, or an object whose `jobs` / `list` / `items` array holds
 * them). Each role object carries `id`, `title`, a `locations[]` array (each with
 * `city` / `province` / `address`), a `department` object (`{ id, name }`),
 * `description` (HTML body), and `updatedAt` / `publishedAt` timestamps. The numeric
 * role `id` is the stable per-role ATS id and the final segment of the canonical public
 * detail / apply URL `https://app.mokahr.com/apply/{tenant}/{orgId}#/job/{jobId}`.
 *
 * The caller addresses a tenant by `companySlug` (the `{tenant}/{orgId}` pair, e.g.
 * `tesla/46129`, with the bare tenant token also accepted) or by `companyUrl` (any
 * social-/campus-recruitment URL on a `mokahr.com` host, from which both the tenant slug
 * and the numeric orgId are parsed). A tenant with the listing disabled / no published
 * roles yields an empty payload (or an HTTP error), so it degrades naturally to an empty
 * result. A fetch error, an HTTP 4xx/5xx, a DNS failure, or a malformed body degrades to
 * an empty / partial result rather than throwing, so a single bad tenant never nukes a
 * batch run.
 *
 * Surface confidence (researched 2026-06-03, no authentication — verified=false):
 *  - Confirmed the platform + tenant addressing live:
 *    `https://app.mokahr.com/social-recruitment/{tenant}/{orgId}`, with real named
 *    tenants observed: `tesla` (46129), `smoore` (126055), `step` (94904), `bigo`
 *    (37723), `hanslaser` (46382), `mihoyo` (44205). The career site itself is a
 *    client-rendered SPA behind region-host redirects, so a clean live JSON listing
 *    could not be confirmed this run; the adapter implements the DOCUMENTED public
 *    listing shape defensively and degrades to empty when the endpoint does not answer.
 */

/** Root domain — used to recognise tenant hosts / URLs passed via `companyUrl`. */
export const MOKAHR_ROOT_DOMAIN = 'mokahr.com';

/** Public application host that serves the candidate-facing career SPA. */
export const MOKAHR_APP_HOST = 'https://app.mokahr.com';

/** Public platform API host that serves the anonymous job-listing JSON endpoint. */
export const MOKAHR_API_HOST = 'https://api.mokahr.com';

/**
 * Candidate recruitment modes, tried in order. A tenant publishes its open roles under
 * one of these modes; the first mode whose listing yields any roles wins. `social` is
 * the experienced-hire board and `campus` the graduate board.
 */
export const MOKAHR_MODES: readonly string[] = ['social', 'campus'];

/**
 * The public job-listing endpoint path template. The platform exposes a tenant's open
 * roles at `…/api-platform/v1/jobs/{orgId}` filtered by recruitment `mode`.
 */
export const mokahrJobsApiUrl = (orgId: string, mode: string, limit: number, offset: number): string =>
  `${MOKAHR_API_HOST}/api-platform/v1/jobs/${encodeURIComponent(orgId)}` +
  `?mode=${encodeURIComponent(mode)}&limit=${limit}&offset=${offset}&status=open`;

/** Builds the canonical public social-recruitment career-site URL for a tenant. */
export const mokahrSocialSiteUrl = (tenant: string, orgId: string, mode: string): string =>
  `${MOKAHR_APP_HOST}/${mode === 'campus' ? 'campus' : 'social'}-recruitment/${encodeURIComponent(
    tenant,
  )}/${encodeURIComponent(orgId)}`;

/** Builds the canonical public per-role detail / apply URL `…/apply/{tenant}/{orgId}#/job/{jobId}`. */
export const mokahrJobUrl = (tenant: string, orgId: string, jobId: string): string =>
  `${MOKAHR_APP_HOST}/apply/${encodeURIComponent(tenant)}/${encodeURIComponent(
    orgId,
  )}#/job/${encodeURIComponent(jobId)}/apply`;

/**
 * Default internal results cap. Mirrors the sibling ATS adapters: when a caller omits
 * `resultsWanted` entirely we ingest up to 100 of the tenant's open roles.
 */
export const MOKAHR_DEFAULT_RESULTS = 100;

/**
 * Page size requested per listing call. The listing endpoint pages via `limit` /
 * `offset`; we request a generous page and walk pages until the board is exhausted or
 * the caller's `resultsWanted` is met.
 */
export const MOKAHR_PAGE_SIZE = 30;

/**
 * Hard ceiling on listing pages fetched per scrape. Bounds the page walk so a runaway /
 * mis-paged endpoint can never spin a scrape indefinitely.
 */
export const MOKAHR_MAX_PAGES = 50;

/**
 * Upper bound (seconds) on the per-request HTTP timeout. An unresponsive MokaHR host can
 * connect-then-hang, so we cap the shared client's 60s default to keep
 * graceful-degradation well inside callers' budgets; a healthy tenant responds in well
 * under a second. A caller may request a SHORTER timeout — we only bound the upper end.
 */
export const MOKAHR_DEFAULT_TIMEOUT_SECONDS = 15;

/** Default request headers. The listing endpoint expects a browser-like UA + JSON Accept. */
export const MOKAHR_HEADERS: Record<string, string> = {
  Accept: 'application/json, text/plain, */*',
  'User-Agent':
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129 Safari/537.36',
  'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
};

/**
 * Matches the `{tenant}/{orgId}` pair inside a social-/campus-recruitment career-site
 * path (e.g. `/social-recruitment/tesla/46129`). Capture 1 is the tenant slug, capture 2
 * the numeric orgId.
 */
export const MOKAHR_SITE_PATH_REGEX =
  /\/(?:social|campus)-recruitment\/([^/?#]+)\/(\d+)/i;

/**
 * Matches a bare `{tenant}/{orgId}` slug pair (the common `companySlug` form, e.g.
 * `tesla/46129`). Capture 1 is the tenant slug, capture 2 the numeric orgId.
 */
export const MOKAHR_SLUG_PAIR_REGEX = /^([^/?#]+)\/(\d+)$/;

/** Detects remote / hybrid roles across the title, location, and department fields. */
export const MOKAHR_REMOTE_REGEX =
  /\b(remote|hybrid|home[\s-]?(?:based|office|working)|work\s*from\s*home|wfh|telecommute|fully\s*remote)\b|远程|居家办公/i;
