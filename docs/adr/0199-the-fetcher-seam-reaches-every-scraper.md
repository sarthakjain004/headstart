# ADR-0199: The Fetcher seam reaches every Scraper

**Status:** accepted · **Date:** 2026-09-24 · **Amends:**
[ADR-0153](0153-a-fetcher-seam-replaces-the-module-global-http-import.md) (finishes what it left
out of scope) · **Relates to:**
[ADR-0103](0103-workdays-400-is-an-invalid-session-cookie-clear-it.md) (the Workday cookie reset
this routes), [ADR-0002](0002-pooled-thread-local-http.md) (the pooled thread-local session,
unchanged)

## Context

ADR-0153 gave `BaseScraper.__init__` an optional `fetcher` so a test could inject a fake instead of
monkeypatching `headstart.http`. It stopped short in four places, measured on `main` at
`12d45409`:

- **`registry.get_scraper` took no `fetcher`.** `scripts/bench/tech_gate_bench.py` built a
  Scraper, then overwrote its private `_fetcher` to count requests.
- **7 of the 9 `__init__` overrides dropped it**: `adp`, `oracle`, `phenom`, `sensehq`,
  `successfactors`, `taleo_be` and `workday` had the signature `(slug, company)`, so passing
  `fetcher=` raised `TypeError`. Only `jibe` and `darwinbox` passed it on.
- **Three requests still reached the module global.** Workday's listing POST (`http.fetch`, sync)
  and its async twin (`http.fetch_async`), and Trakstar's RSS feed, a module-level `_fetch_feed`
  calling `http.fetch`. ADR-0153 named both as out of scope.
- **The cookie jar had no seam.** Workday clears `http.session().cookies` on a listing 400 and a
  detail 400 (ADR-0103). Cornerstone clears one domain's cookies after reading its career-site
  page. `http.session()` is thread-local, and the `Fetcher` protocol had no way to say "forget
  cookies". So a Scraper built on a fake still reset the real pooled jar, and no test could see
  that the reset happened.

Tests grew five separate fakes because there was no shared one: `FakeFetcher` and
`FakeBrowserFetcher` in `test_fetcher.py`, `_FakeFetcher` in `test_bamboohr.py`, `_Fetcher` in
`test_jibe.py`, and `_FakeCsod` plus four subclasses in `test_cornerstone.py`. There were also four
fake Response classes.

## Options considered

**The cookie reset.**

- **(a) A `clear_cookies(domain=None)` method on `Fetcher`.** It is the one verb both callers use.
  Workday clears the whole jar and Cornerstone clears one domain.
- **(b) Expose the jar itself (`fetcher.cookies`).** A fake would then need a jar-shaped double,
  the curl_cffi `KeyError` on an unknown domain would leak to every caller, and the protocol would
  promise a whole cookie API that nothing uses.
- **(c) Leave the reset on `http.session()`.** An injected fake then still clears the real pooled
  jar, and a test cannot see the reset without monkeypatching `headstart.http`. That is the gap
  this ADR closes.

**Decision: (a).** `HTTPFetcher.clear_cookies` clears the calling thread's `http.session()` jar,
the same object the two scrapers cleared before, so the thread-local semantics do not change. It
also swallows the `KeyError` that `CookieJar.clear(domain)` raises when the jar holds nothing for
that domain. Cornerstone used to wrap its call in `contextlib.suppress(KeyError)`; that suppress
moves into the fetcher, because "nothing to forget" is not an error for any caller.

**`BrowserFetcher` does not implement `clear_cookies`.** A no-op would claim a reset that never
happened. A real reset would drop the Cloudflare clearance the tab's navigation earned and re-wall
the tab. Nothing asks a warmed tab to forget its cookies, so the method stays absent, the same
choice ADR-0153 made for its `fetch_async`: if anything ever calls it, the call fails loudly.

**Trakstar's feed.** `_fetch_feed` becomes a method that calls `self._fetch`, the same call the
per-job detail pages already make. `_egress()` adds only `egress_board` for Trakstar, which has no
`egress_fallback_on`, so the request kwargs match the old module function's exactly. The other
choice was to pass a fetcher into the module-level function. That would add a parameter, while the
method removes one: `egress_board` no longer has to be threaded in from the caller's
`board_key()`.

**Workday's listing POST.** It calls `self._fetcher.fetch` and `self._fetcher.fetch_async`, not
`self._fetch`. `_fetch` always adds `_egress()`, and the listing's one retry after a transient
non-JSON page (`direct=True`) deliberately sends none of it.

**Workday's own listing `AsyncSession` is left alone.** `BaseScraper.fan_out_async` opens its
session the same way, so nothing in the seam opens sessions. The session reaches the fetcher as an
opaque argument, and a fake ignores it.

## Decision

1. `get_scraper(ats, slug, company, *, have_details=None, fetcher=None)` passes `fetcher` to the
   constructor. The bench injects its counting fetcher there instead of overwriting `_fetcher`.
2. All nine `__init__` overrides accept `fetcher: Fetcher | None = None` and pass it to
   `super().__init__`. `tests/test_fetcher.py` checks every registered ATS.
3. `Fetcher` gains `clear_cookies(domain: str | None = None) -> None`, and Workday's two resets and
   Cornerstone's reset go through `self._fetcher`. The resets on Workday's per-pass
   `AsyncSession` jar stay as they were, because that jar belongs to the session and not to the
   fetcher.
4. Workday's listing POST, sync and async, and Trakstar's feed go through the Scraper's fetcher,
   with the same request kwargs as before.
5. **One shared test fake, `tests/fake_fetcher.py`.** `FakeFetcher(route)` sends every request,
   sync or async, to `route(method, url, kwargs)`, which returns a `FakeResponse` or an exception.
   It records each request as a `FakeRequest(method, url, kwargs)` and each cookie reset in
   `cookie_clears`. Migrated to it:
   - the `test_fetcher.py` fake, which becomes a dict-backed route;
   - the `test_bamboohr.py` fake, which becomes a route that answers every request the same way;
   - the `test_cornerstone.py` fake, where `_FakeCsod` is now a `FakeFetcher` whose four
     specialised subclasses override the route instead of `fetch`, and whose cookie test reads
     `cookie_clears` instead of monkeypatching `http.session`.

   Two fakes stay specialised. `test_jibe.py`'s becomes `_ClockedFetcher`, a subclass that stamps
   each request with the fake clock, because its crawl-delay tests read the gaps between requests.
   `FakeBrowserFetcher` stays its own class, because it stands in for darwinbox's browser factory,
   a context manager behind a different seam, but it now answers with the shared `FakeResponse`.
6. **Migrate when touched.** The existing monkeypatches of `headstart.http` stay: 93 `setattr`
   calls in 8 test files on `main`. `HTTPFetcher` still forwards to `http.fetch`/`fetch_async` by
   name, so they still work. A test that is rewritten for another reason moves to the shared fake;
   nothing is mass-rewritten.

## Consequences

- **Every Fetcher adapter must now implement `clear_cookies` if a Scraper it serves resets
  cookies.** The full test suite found a third adapter: `check_liveness.py`'s `_GatedFetcher`,
  which the Cornerstone probe injects. Without the method, `p_cornerstone` raised
  `AttributeError` on every Board whose career-site page it read. It now delegates to
  `http.DEFAULT_FETCHER`, because its `_fetch` rides the pooled session, so it clears the same jar
  as before. The bench's `_CountingFetcher` forwards the method too.
- **No request changed.** One live Board per ATS was scraped before and after this change, and
  every `(method, url, kwargs)` was recorded at `headstart.http`, which both paths reach:
  - `workday:blackline/BlackLineCareers`: 111 Jobs both times, with the same ids and the same
    fields apart from `scraped_at`. It made 118 requests (the instance probe, 6 listing POSTs of
    which 5 were async, and 111 details), and the request multiset was identical.
  - `trakstar:turnkeyconsulting`: 34 Jobs from `fetch_raw` and 34 from `fetch_via_feed` both
    times, with the same ids. It made 2 requests (the jsapi page and the feed), and the request
    multiset was identical.
- **The bench now counts Workday's listing POSTs.** They used to bypass the fetcher it wrapped, so
  a Workday request count taken before this change is not comparable with one taken after it.
- **A test can prove a request stays on the seam.** `tests/test_fetcher.py` replaces
  `http.fetch`, `http.fetch_async` and `http.session` with functions that raise, then runs
  Workday's `fetch_raw` and Trakstar's feed on the shared fake. Pointing Workday's listing, its
  cookie reset and Trakstar's feed back at the module global fails four of those tests.
