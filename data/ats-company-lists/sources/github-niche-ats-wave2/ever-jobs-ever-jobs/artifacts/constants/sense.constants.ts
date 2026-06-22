/**
 * Constants for the Sense (sensehq.com) careers platform.
 *
 * Sense (sensehq.com — a US recruiting CRM / talent-engagement & TRM platform, which
 * absorbed the Skillate ATS that still powers its hosted career sites) gives each customer
 * tenant a branded, public, unauthenticated candidate-facing career site on the shared host,
 * addressed by its company slug as a sub-domain of the root domain:
 *
 *   https://{tenant}.sensehq.com/careers                 (branded careers board)
 *   https://{tenant}.sensehq.com/careers/jobs/{jobId}     (per-role public detail / apply page)
 *
 * Each tenant career site is backed by a **public, anonymous JSON API** on the same host —
 * the exact feed the career site's own front-end consumes (no bearer token, no API key):
 *
 *   GET https://{tenant}.sensehq.com/careers/api/jobs?page={n}
 *
 * which returns a `{ success, data: { count, rows } }` envelope:
 *
 *   { "success": true,
 *     "data": { "count": 15,
 *               "rows": [ { …job… } ] } }
 *
 * where `data.count` is the total open-role count across all pages and `data.rows[]` is the
 * current page's slice. Each row carries a numeric `id` (the stable ATS id, e.g. `217`), a
 * `title`, a free-text `location`, a `department` (+ `department_id`), a `description_external`
 * (rendered HTML body), a `job_type` (e.g. `FULLTIME`), a `code` (req code, e.g. `IND00217`),
 * a `workplace_type` (e.g. `REMOTE`, when set), a structured `office` ({ city, state, country,
 * location, name, pin_code }), `open_positions`, `experience_start` / `experience_end`,
 * `organization_id`, a `job_status` (e.g. `OPEN`), and `created_on` / `updated_on` epoch-ms
 * timestamps. The adapter GETs this feed, walks pages by `page` index, and maps each row —
 * rather than depending on a client-rendered DOM, a headless browser, or any authenticated
 * Sense TRM API.
 *
 * Pagination model (confirmed live): `page` is **0-based** and the server returns a fixed
 * page size of 10 rows regardless of any `limit` (the `limit` query param is ignored / hard
 * capped at 10). `page=0` (== no param) returns the first 10 rows; `page=1` the next slice;
 * an out-of-range page returns an empty `rows` array (with `count` still populated). The
 * adapter drains pages 0,1,2,… until `rows` is empty, until it has collected `count` roles,
 * or until `resultsWanted` / the page cap is reached.
 *
 * The caller addresses a tenant by `companySlug` (the sub-domain label, e.g. `sensehr`) or
 * by `companyUrl` (a career-site URL on a `sensehq.com` host whose leading sub-domain label
 * is the tenant). An unknown tenant, a tenant with no open roles, or an empty board degrades
 * naturally to an empty result. A fetch error, an HTTP 4xx/5xx, a DNS failure, or a malformed
 * body degrades to an empty / partial result rather than throwing, so a single bad tenant
 * never nukes a batch run.
 *
 * Surface confidence (researched + verified live 2026-06-04, no authentication):
 *  - Confirmed the platform + tenant addressing (`{tenant}.sensehq.com/careers`) and a real,
 *    named tenant on it: `sensehr` (Sense's own careers board — 15 live roles).
 *  - Confirmed the public anonymous JSON feed `GET /careers/api/jobs` returns
 *    `{ success:true, data:{ count, rows } }`, each row carrying a numeric `id` (e.g. `217`),
 *    a `title`, `location`, `department`, `description_external` (HTML), `job_type`,
 *    `workplace_type`, and a structured `office`. The canonical public detail / apply page is
 *    `https://{tenant}.sensehq.com/careers/jobs/{id}` (confirmed HTTP 200 for id `217`).
 *  - Confirmed the 0-based `page` pagination with a fixed server page size of 10 rows.
 *  - Confirmed an unknown tenant host answers an HTTP error (HTTP 500) rather than serving a
 *    feed — the adapter degrades that to an empty result. verified=true.
 */

/** Root domain — used to recognise tenant hosts / URLs passed via `companyUrl`. */
export const SENSE_ROOT_DOMAIN = 'sensehq.com';

/** Hosted careers host suffix — tenant sites live at `{tenant}.sensehq.com`. */
export const SENSE_CAREER_HOST_SUFFIX = '.sensehq.com';

/** Builds a tenant's career-site origin from its slug. */
export const senseCareerOrigin = (tenant: string): string =>
  `https://${tenant}${SENSE_CAREER_HOST_SUFFIX}`;

/** Public, anonymous jobs-feed path on the tenant career host (`/careers/api/jobs`). */
export const SENSE_JOBS_PATH = 'careers/api/jobs';

/**
 * Public per-role detail / apply path on the tenant career host
 * (`/careers/jobs/{id}`). The adapter assembles the canonical detail URL from this since the
 * feed rows carry no absolute URL of their own.
 */
export const SENSE_JOB_DETAIL_PATH = 'careers/jobs';

/**
 * Server page size. The feed returns a fixed slice of 10 rows per page regardless of any
 * `limit` query value (confirmed live: `limit` is ignored / hard capped at 10). The `page`
 * index is 0-based.
 */
export const SENSE_PAGE_SIZE = 10;

/**
 * Default internal results cap. Mirrors the sibling ATS adapters: when a caller omits
 * `resultsWanted` entirely we ingest up to 100 of the tenant's open roles.
 */
export const SENSE_DEFAULT_RESULTS = 100;

/**
 * Hard ceiling on feed pages fetched per scrape. With a fixed 10-row page size this admits up
 * to 200 roles (20 × 10), well beyond any typical SMB / mid-market board; it guards against an
 * unbounded / non-terminating page walk if a tenant ever stops returning an empty final page.
 */
export const SENSE_MAX_PAGES = 20;

/**
 * Upper bound (seconds) on the per-request HTTP timeout. An unresponsive Sense career host can
 * connect-then-hang, so we cap the shared client's 60s default to keep graceful-degradation
 * well inside callers' budgets; a healthy tenant responds in well under a second. A caller may
 * request a SHORTER timeout — we only bound the upper end.
 */
export const SENSE_DEFAULT_TIMEOUT_SECONDS = 15;

/** Default request headers. The JSON feed expects a browser-like UA + JSON Accept. */
export const SENSE_HEADERS: Record<string, string> = {
  Accept: 'application/json, text/plain, */*',
  'User-Agent':
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129 Safari/537.36',
  'Accept-Language': 'en-US,en;q=0.9',
};

/**
 * The `workplace_type` token Sense emits for fully-remote roles, when present (e.g.
 * `REMOTE`). Matched case-insensitively as a substring of the structured token.
 */
export const SENSE_REMOTE_TYPE = 'remote';

/**
 * Detects remote / home-working roles across the title, location, and department fields,
 * complementing the structured `workplace_type` signal Sense emits.
 */
export const SENSE_REMOTE_REGEX =
  /\b(remote|home[\s-]?office|telecommute|teleworking|wfh|work\s*from\s*home|fully\s*remote|anywhere)\b/i;
