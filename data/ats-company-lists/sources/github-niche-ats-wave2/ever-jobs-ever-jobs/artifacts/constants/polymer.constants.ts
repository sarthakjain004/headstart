/**
 * Constants for the Polymer applicant-tracking / careers-page platform.
 *
 * Polymer exposes a documented, unauthenticated **Public API** intended for job
 * board integrations. Every tenant is addressed by an organization slug (its
 * URL-formatted name) under one shared host, `https://api.polymer.co/v1/hire`:
 *
 *   GET https://api.polymer.co/v1/hire/organizations/{slug}/jobs?page={n}&per_page=50
 *     → { items: PolymerJob[], meta: { total, count, page, is_first, is_last,
 *                                      next_page, organization_name } }
 *
 *   GET https://api.polymer.co/v1/hire/organizations/{slug}/jobs/{id}
 *     → PolymerJobDetail  (the list item plus `description` (HTML) + `department`)
 *
 * The list feed paginates (`per_page` rows per page, walk via `meta.is_last` /
 * `meta.next_page`), and `meta.total` is the total number of open roles for the
 * tenant. The list rows do NOT carry the full HTML body; the per-job detail
 * endpoint does, so descriptions are hydrated with a bounded concurrent fan-out.
 *
 * An unknown tenant yields an empty `items` array (or HTTP 404), which we treat
 * as a graceful empty result rather than an error. The public job-detail page
 * lives at `https://jobs.polymer.co/{slug}/{id}` (mirrored by each row's
 * `job_post_url`).
 *
 * Verified live 2026-06-03 against the `teton` and `return` tenants.
 */

/** Shared public API host for every Polymer-hosted careers site. */
export const POLYMER_HOST = 'https://api.polymer.co';

/** Public, unauthenticated jobs-list path template (`{slug}` substituted). */
export const POLYMER_JOBS_PATH_TEMPLATE = '/v1/hire/organizations/{slug}/jobs';

/** Public, unauthenticated single-job detail path template (`{slug}`, `{id}`). */
export const POLYMER_JOB_DETAIL_PATH_TEMPLATE = '/v1/hire/organizations/{slug}/jobs/{id}';

/** Public job-board / job-detail page host (used to synthesize a fallback URL). */
export const POLYMER_JOB_PAGE_HOST = 'https://jobs.polymer.co';

/** Rows requested per list page (Public API default & maximum is 50). */
export const POLYMER_PAGE_SIZE = 50;

/** Bounded concurrency for the per-job detail (description) fan-out. */
export const POLYMER_MAX_CONCURRENCY = 5;

/** Polite delay (ms) between concurrent detail batches. */
export const POLYMER_REQUEST_DELAY_MS = 200;

/**
 * Default internal results cap. Mirrors the sibling ATS adapters: the public DTO
 * default is 15, but when a caller omits `resultsWanted` entirely we ingest up
 * to 100 of the tenant's open roles.
 */
export const POLYMER_DEFAULT_RESULTS = 100;

/** Default request headers. Polymer expects a browser-like UA + JSON accept. */
export const POLYMER_HEADERS: Record<string, string> = {
  Accept: 'application/json, text/plain, */*',
  'User-Agent':
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129 Safari/537.36',
  'Accept-Language': 'en-US,en;q=0.9',
};
