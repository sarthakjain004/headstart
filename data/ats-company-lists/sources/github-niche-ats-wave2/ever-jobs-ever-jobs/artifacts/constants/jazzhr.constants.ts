/** JazzHR public career page URL (slug interpolated at runtime) */
export const JAZZHR_CAREERS_URL = 'https://{slug}.applytojob.com/apply/jobs/';

/** Default headers for JazzHR career page requests */
export const JAZZHR_HEADERS: Record<string, string> = {
  Accept: 'text/html',
  'User-Agent':
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129 Safari/537.36',
};
