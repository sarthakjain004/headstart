/**
 * Constants for the Recooty applicant-tracking careers platform.
 *
 * Recooty (recooty.com) is an SMB-focused ATS that lets each customer publish a
 * branded careers page and embed a "Job Widget" on their own site. The widget is
 * a client-side bundle that fetches the tenant's live open roles from a single
 * public, unauthenticated JSON endpoint:
 *
 *   GET https://standaloneapi.recooty.app/api/widget/{widgetId}?language={lang}
 *     → {
 *         career_page_url: "https://careerspage.io/",
 *         team: { id, name, slug, logo, locations, departments, jobPosts[], job_widget },
 *         translation: { ... }    // i18n strings (not job data)
 *       }
 *
 * The tenant is addressed by a `{widgetId}` — a 32-char hex token issued from the
 * Recooty dashboard (Settings → Job Widget). It is the platform's public API key
 * for read-only job access; there is no per-tenant sub-domain on the API host.
 * The hosted careers page lives at `{career_page_url}{team.slug}` (e.g.
 * `https://careerspage.io/{slug}`) and individual roles at
 * `{career_page_url}{team.slug}/{jobSlug}`.
 *
 * The endpoint returns every open role for the tenant in one response (no
 * server-side pagination), so we slice client-side to honour `resultsWanted`.
 * An unknown / malformed widget id yields HTTP 422 with
 * `{"error":true,"message":"Invalid API Key."}`, which we treat as an empty
 * (graceful) result rather than an error.
 */

/** Shared public API host for every Recooty Job Widget feed. */
export const RECOOTY_HOST = 'https://standaloneapi.recooty.app';

/**
 * Public, unauthenticated widget jobs feed path. `{widgetId}` is the tenant's
 * dashboard-issued widget id (its public read API key).
 */
export const RECOOTY_WIDGET_PATH_TEMPLATE = '/api/widget/{widgetId}';

/** Default UI language passed to the widget feed (controls translation strings only). */
export const RECOOTY_DEFAULT_LANGUAGE = 'en';

/** Fallback hosted careers-page base, used when the feed omits `career_page_url`. */
export const RECOOTY_CAREER_PAGE_BASE = 'https://careerspage.io/';

/**
 * Default internal results cap. Mirrors the sibling ATS adapters: the public DTO
 * default is 15, but when a caller omits `resultsWanted` entirely we ingest up
 * to 100 of the tenant's open roles.
 */
export const RECOOTY_DEFAULT_RESULTS = 100;

/** Default request headers. The feed expects a browser-like UA + JSON accept. */
export const RECOOTY_HEADERS: Record<string, string> = {
  Accept: 'application/json, text/plain, */*',
  'User-Agent':
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129 Safari/537.36',
  'Accept-Language': 'en-US,en;q=0.9',
};
