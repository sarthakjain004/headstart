/**
 * Constants for the Beisen (北森 / iTalent) recruitment platform.
 *
 * Beisen (brand "iTalent") is the largest enterprise cloud-HR / talent-management SaaS in
 * the China region. Its recruitment product powers the public, unauthenticated career sites
 * of a large roster of major China-operating employers. Each customer ("tenant") publishes a
 * branded career site at a dedicated subdomain on the shared platform root:
 *
 *   https://{slug}.zhiye.com                         (tenant career SPA)
 *
 * The career site is a client-rendered single-page app. Open roles are served by a public,
 * anonymous JSON listing endpoint, reached via a deterministic two-step flow:
 *
 *   1. GET  https://{slug}.zhiye.com/portal/registerSystemInfo
 *      → HTML that inlines `var BSGlobal = { "Key", "Name", "PortalId", "Code" };`
 *        The `PortalId` is required for the listing call; `Name` is the tenant's branded
 *        company display name; the numeric tenant id appears in `stcms.beisen.com/image/{id}`
 *        CDN URLs (read best-effort for provenance).
 *
 *   2. POST https://{slug}.zhiye.com/api/Jobad/GetJobAdPageList
 *      body { PageIndex, PageSize, KeyWords:"", SpecialType:0, PortalId, DisplayFields:[…] }
 *      → { Code, Message, Count, Data:[ { JobAdId, JobAdName, LocNames, Duty, Require,
 *           Salary, Category, ChangeDate, PostDate, … } ] }
 *
 * The numeric `JobAdId` is the stable per-role ATS id and the final segment of the canonical
 * public detail / apply URL `https://{slug}.zhiye.com/portal/jobs/{JobAdId}`.
 *
 * Surface confidence (researched 2026-06-18, no authentication — verified=false): the platform
 * + tenant addressing (`{slug}.zhiye.com`), the `BSGlobal` bootstrap, and the
 * `GetJobAdPageList` listing contract are documented from the public career SPA. A tenant with
 * the listing disabled, a legacy (pre-2022) portal with no `BSGlobal`, or a risk-control /
 * WAF-gated tenant degrades naturally to an empty result. A fetch error, an HTTP 4xx/5xx, a DNS
 * failure, or a malformed body degrades to an empty / partial result rather than throwing, so a
 * single bad tenant never nukes a batch run.
 */

/** Root domain — used to recognise tenant hosts / URLs passed via `companySlug`/`companyUrl`. */
export const BEISEN_ROOT_DOMAIN = 'zhiye.com';

/** Build the public tenant career-site origin for a slug (e.g. `mengniu` → the tenant host). */
export const beisenTenantHost = (slug: string): string =>
  `https://${encodeURIComponent(slug)}.${BEISEN_ROOT_DOMAIN}`;

/** Tenant-resolution path — the HTML that inlines the `BSGlobal` portal config. */
export const BEISEN_REGISTER_PATH = '/portal/registerSystemInfo';

/** Public job-listing endpoint path (paginated POST). */
export const BEISEN_SEARCH_PATH = '/api/Jobad/GetJobAdPageList';

/** Build the canonical public per-role detail / apply URL `{base}/portal/jobs/{JobAdId}`. */
export const beisenJobUrl = (base: string, jobAdId: string): string =>
  `${base}/portal/jobs/${encodeURIComponent(jobAdId)}`;

/**
 * The listing fields requested from the platform. Mirrors the fields the career SPA itself
 * asks for so the tenant returns the full role body (`Duty`/`Require`), location, department,
 * and salary alongside the core role identity.
 */
export const BEISEN_DISPLAY_FIELDS: readonly string[] = [
  'Category',
  'Description',
  'Location',
  'Department',
  'Salary',
];

/**
 * Default internal results cap. Mirrors the sibling ATS adapters: when a caller omits
 * `resultsWanted` we ingest up to 100 of the tenant's open roles.
 */
export const BEISEN_DEFAULT_RESULTS = 100;

/** Page size requested per listing call. The endpoint pages via `PageIndex`/`PageSize`. */
export const BEISEN_PAGE_SIZE = 50;

/**
 * Hard ceiling on listing pages fetched per scrape. Bounds the page walk so a runaway /
 * mis-paged endpoint can never spin a scrape indefinitely (100 × 50 = 5 000 roles).
 */
export const BEISEN_MAX_PAGES = 100;

/**
 * Upper bound (seconds) on the per-request HTTP timeout. An unresponsive Beisen host can
 * connect-then-hang, so we cap the shared client's 60s default to keep graceful-degradation
 * well inside callers' budgets; a healthy tenant responds in well under a second. A caller may
 * request a SHORTER timeout — we only bound the upper end.
 */
export const BEISEN_DEFAULT_TIMEOUT_SECONDS = 20;

/** Default request headers — the endpoints expect a browser-like UA + JSON Accept. */
export const BEISEN_HEADERS: Record<string, string> = {
  Accept: 'application/json, text/html, */*',
  'User-Agent':
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129 Safari/537.36',
  'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
};

/**
 * Anchors the inline `var BSGlobal = {` portal-config block in the career-site HTML. The match
 * end (the opening `{`) is the start index for a balanced-brace scan that consumes the full
 * (possibly nested) object literal so tenants with nested config aren't truncated.
 */
export const BEISEN_BSGLOBAL_REGEX = /var\s+BSGlobal\s*=\s*\{/;

/**
 * Matches the numeric tenant id embedded in the page's image-CDN URLs
 * (`stcms.beisen.com/image/{tenantId}/…`) — the only place the numeric tenant id is
 * consistently exposed. Capture 1 is the tenant id.
 */
export const BEISEN_TENANT_ID_REGEX = /stcms\.beisen\.com\/image\/(\d+)/;

/**
 * Beisen emits `0001-01-01T00:00:00` for an unset date. A value with this prefix is treated as
 * missing rather than parsed into the year 1 AD.
 */
export const BEISEN_UNSET_DATE_PREFIX = '0001-01-01';

/** Detects remote / hybrid roles across the title, location, and category fields (EN + ZH). */
export const BEISEN_REMOTE_REGEX =
  /\b(remote|hybrid|home[\s-]?(?:based|office|working)|work\s*from\s*home|wfh|telecommute|fully\s*remote)\b|远程|居家办公|远程办公/i;
