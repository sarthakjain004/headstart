/** Trakstar Hire API base URL template (companySlug is interpolated at runtime) */
export const TRAKSTAR_API_BASE = 'https://{slug}.hire.trakstar.com/api/v1';

/** Default headers for Trakstar API requests */
export const TRAKSTAR_HEADERS: Record<string, string> = {
  Accept: 'application/json',
  'User-Agent':
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129 Safari/537.36',
};
