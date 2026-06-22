/**
 * Spec 006 / T03 — Avature constants.
 *
 * The values mirror the upstream Python reference
 * (`OTHERS/Ats-scrapers/avature/api_client.py`) so a future
 * contributor can `git diff` the two and convince themselves the
 * behaviour matches: jobs paginate at 12 per request, polite-pacing
 * sits at 0.5 s, the multi-selector chain matches the same five HTML
 * shapes plus the link-text fallback, and Apply-decoy filtering
 * consumes the same lowercase phrase list.
 */

/** Default page size for Avature pagination. Matches upstream Python. */
export const AVATURE_RECORDS_PER_PAGE = 12;

/** Polite-pacing delay between paginated GETs (seconds). Matches upstream Python's `rate_limit=0.5`. */
export const AVATURE_RATE_DELAY_SECONDS = 0.5;

/** Ceiling on jobs returned when `input.resultsWanted` is unset. */
export const AVATURE_DEFAULT_RESULTS_WANTED = 100;

/** Hard ceiling on pagination loops to prevent runaway scrapes. Matches `(default 100)/12 ~= 9` plus headroom. */
export const AVATURE_MAX_PAGES = 50;

/** Default user-agent / accept headers (browser-shaped) so Avature returns the same HTML it would to a real visitor. */
export const AVATURE_HEADERS: Record<string, string> = {
  'User-Agent':
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
  Accept:
    'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
  'Accept-Language': 'en-US,en;q=0.9',
};

/** Phrases that, when matched (lower-cased) against an anchor's text, mark it as an Apply-button decoy and filter it out. */
export const AVATURE_APPLY_DECOY_TEXTS: ReadonlySet<string> = new Set([
  '',
  'apply',
  'apply now',
  'apply online',
  'learn more',
  'view job',
]);

/** Locale prefixes Avature emits in its career-portal URLs (kept from upstream's `extract_base_url`). */
export const AVATURE_LOCALE_PREFIXES: ReadonlySet<string> = new Set([
  'en_US',
  'en_GB',
  'fr_CA',
  'zh_CN',
  'ja_JP',
  'pt_BR',
]);
