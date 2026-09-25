# ADR-0153: A `Fetcher` seam replaces the module-global `http` import

**Status:** accepted · **Date:** 2026-09-15 · **Amends:** neither —
[ADR-0002](0002-pooled-thread-local-http.md) (the pooled `curl_cffi` session) and
[ADR-0056](0056-darwinbox-browser-escalation.md) (the browser escalation) both stand exactly as
decided; this gives each of them a named seam to sit behind instead of being reached as a module
global.

**Amended by:** [ADR-0199](0199-the-fetcher-seam-reaches-every-scraper.md) — the seam now
reaches every Scraper: `get_scraper` takes a `fetcher`, every `__init__` override passes it on,
Workday's listing and Trakstar's feed ride it, and `Fetcher` gains `clear_cookies`. The
"`registry.get_scraper` is unchanged" consequence below no longer holds.

## Context

`BaseScraper.__init__(slug, company)` takes no fetcher parameter. Every scraper reaches HTTP
through `headstart.network.http`, imported at module scope in ~20 scraper files and reached inside
`BaseScraper._get`/`_get_async`/`_fetch`/`_fetch_async` as a bare module reference. That module
holds real, useful process-global state — a thread-local pooled session and a retry counter
(ADR-0002) — which is not the problem; the problem is that nothing sits between a scraper and
that global, so there is no place to put a second HTTP client or a fake one.

Two things had already grown into that gap without a seam to occupy:

- **`browser_http.py`** (ADR-0056) is a second, structurally different HTTP client — a context
  manager (`with browser_http.origin(url) as page: page.post_json(...)`) reached only by
  `darwinbox.py`, via a lazy import inside one method.
- **`tesla.py`** needed the same browser capability and explicitly did not reuse `browser_http`
  (its own module docstring: "`browser_http` isn't reusable as-is"), so it grew a third, private
  Chrome-driver implementation instead.

And one symptom of reaching `http` directly rather than through any shared seam: `join.py` calls
`http.fetch`/`http.fetch_async` directly in four places, with none of `BaseScraper._egress()`'s
kwargs threaded in — so `egress_board` attribution is silently missing from every one of its
requests, and any future egress-fallback opt-in for join would be inert. `base.py` already
comments on exactly this failure mode without fixing it (the two-client situation had no seam
to make the omission visible in a type signature).

Tests reached around all of this four structurally incompatible ways: monkeypatching
`headstart.network.http`'s module attributes with hand-rolled response doubles, monkeypatching a
scraper's own `_get`, monkeypatching `fan_out`/`fan_out_async` themselves, or reconstructing
`fetch_raw`'s output by hand from a fixture without touching the network at all
(`tests/test_icims.py`'s `_raw_from_fixture`). No test could simply pass a fake fetcher in.

## Options considered

- **(a) An optional `fetcher` parameter on `BaseScraper.__init__`, defaulting to today's global-
  http behaviour.** Every scraper that does not override `__init__` needs zero changes; the nine
  that do (`keka`, `taleo_be`, `sensehq`, `uber`, `workday`, `bytedance`, `oracle`,
  `successfactors`, `apple`) all call `super().__init__(slug, company)` positionally and keep
  working unchanged, since the new parameter only matters to a caller that passes it.
- **(b) A thorough refactor requiring every scraper to accept and thread a fetcher explicitly.**
  Forces all 35 scrapers (and every call site that constructs one — `registry.get_scraper`,
  every test, `scripts/scrape/`) to learn the new parameter immediately, for a benefit ((a))
  already delivers: today, exactly one scraper (darwinbox) needs a second fetcher at all.

**Decision: (a).** The blast radius of touching `BaseScraper.__init__`'s signature — the one
constructor every scraper subclasses — is the whole reason this was flagged for a design pass
before writing code, and (a) is additive and backward-compatible by construction: the default
argument value is resolved once, inside `__init__`, to `http.DEFAULT_FETCHER`, so a caller that
never mentions `fetcher` gets exactly today's behaviour, unchanged, byte for byte. (b) would buy
nothing beyond what (a) already gives every scraper (an injectable seam), while forcing a
same-day migration of 35 constructors and every existing test file that instantiates a scraper
positionally. Nothing found while building this needed (b).

## Decision

**`headstart.network.fetcher.Fetcher`** is a `typing.Protocol` with two methods:

```python
class Fetcher(Protocol):
    def fetch(self, method: str, url: str, **kwargs: Any) -> Any: ...
    async def fetch_async(self, session: Any, method: str, url: str, **kwargs: Any) -> Any: ...
```

`fetch` returns whatever settles — a status code, a `.json()`, a `.raise_for_status()` — for the
caller to classify, exactly as `http.fetch` already does (ADR-0002 is unchanged: a 4xx/5xx is a
returned value, not an exception). This is deliberately the *whole* interface a scraper needs
from its ordinary HTTP client, and it is genuinely one shape — every scraper's `_get`/`_fetch`
already speaks it.

**`http.HTTPFetcher`** wraps the existing module functions as the default adapter, adding no
behaviour: `HTTPFetcher.fetch`/`fetch_async` call the bare module-level `fetch`/`fetch_async` by
name (not `self`), so a test that monkeypatches `http.fetch` (most of this repo's do) keeps
working against the default fetcher unchanged — the pooled thread-local session and the
spare-egress machinery stay exactly as ADR-0002 built them, now as the adapter's
*implementation*, not its interface. `http.DEFAULT_FETCHER = HTTPFetcher()` is the one shared
instance `BaseScraper.__init__` resolves to when no `fetcher` is passed.

**`BaseScraper.__init__` gains one optional, keyword-friendly parameter**: `fetcher: Fetcher |
None = None`, stored as `self._fetcher` and resolved to `http.DEFAULT_FETCHER` when `None`. Every
method that used to call `http.fetch`/`http.fetch_async` directly — `_get`, `_get_async`,
`_fetch`, `_fetch_async`, and `alias_key`'s own probe request — now calls `self._fetcher.fetch`/
`self._fetcher.fetch_async` instead. `import headstart.network.http` stays in `base.py` for exactly one
reason: resolving that default.

### `browser_http`, deepened to satisfy the same shape

`browser_http.origin()`'s existing contract (navigate once, then `page.post_json`/`get_json` on
the warmed tab, raising `BrowserHTTPError` on a non-2xx answer) is untouched — ADR-0056's tested
behaviour is not being reopened. A new class, **`browser_http.BrowserFetcher`**, is added beside
it: a context manager scoped to one origin (`BrowserFetcher(page_url)`), whose `fetch(method,
url, **kwargs)` strips `page_url`'s origin off `url` to recover the relative path `post_json`/
`get_json` already expect, and wraps the answer in a small `_FetchResult` carrying
`.status_code`/`.json()`/`.raise_for_status()` — the same slice of surface `HTTPFetcher`'s
answers carry, so `DarwinboxScraper` can write its walled path in the same shape as its curl one.

This is honestly a **partial** `Fetcher`: `BrowserFetcher` implements `fetch` only. A browser tab
is one session, not a multiplexed pool, and darwinbox's escalation is entirely synchronous — no
code path ever needs `fetch_async` on it, so adding one would be exactly the unnatural shape this
ADR's brief warned against manufacturing. `headstart/network/fetcher.py`'s module docstring states this
in prose rather than in the type system: this repo runs no type checker in CI, so `Fetcher` is a
documented contract, not an enforced one, and a fake used only for a scraper's synchronous path
is free to leave `fetch_async` unimplemented as long as nothing calls it.

`DarwinboxScraper` is migrated onto both seams explicitly. It gains a *second* constructor
parameter, `browser_fetcher: Callable[[str], BrowserFetcher] = BrowserFetcher` — deliberately not
folded into the one `fetcher` parameter `BaseScraper` defines, because darwinbox needs both
fetchers *at once* (curl first, browser only once the curl attempts prove a wall), where every
other scraper needs exactly one. `_fetch_raw_browser` no longer imports `browser_http` lazily
inside the method; it calls `self._browser_fetcher(page_url)` and, inside that `with` block,
`browser.fetch("POST", api, json=body)` — line-for-line the same shape `_alljobs`'s curl path
already uses. A test can now inject a fake for *either* one (or both at once) without
monkeypatching `headstart.network.http` or `headstart.network.browser_http`; `tests/test_network_fetcher.py` proves it
for the unwalled path, the walled-escalation path, and two ordinary-HTTP scrapers (greenhouse,
icims).

### `tesla.py`'s private Chrome driver: left alone, on purpose

`tesla.py`'s own module docstring already explains why it could not reuse `browser_http`: Tesla's
Akamai wall admits only a request the *page itself* issues on its first navigation, so the
scraper reads a CDP `Network.getResponseBody` off the page's own first-load call rather than
issuing any explicit `fetch`/`post_json` of its own — the "warmed tab, then explicit page-context
requests" contract `BrowserFetcher` wraps is exactly the half of ADR-0056's shape Tesla's wall
refuses. Forcing tesla onto `Fetcher` would mean inventing a method that reads "run one
navigation and hand back whatever network body happened to match a URL suffix" — a shape with
one caller, which is the unnatural-protocol trap this ADR's brief explicitly warned against. Left
as a documented future migration: if a second scraper ever needs Tesla's read-the-page's-own-
response pattern, that is the moment to name it as a real second adapter shape (two adapters make
a seam real) — not before.

### `join.py`'s dropped egress kwargs, fixed

`join.py`'s four direct `http.fetch`/`http.fetch_async` calls (the careers-page fetch, the
listing pagination, and both the sync and async per-job description fetches) are now
`self._fetch`/`self._fetch_async` — the existing `BaseScraper` methods that already thread
`**self._egress()` through automatically. This was a real, narrow bug (item 5 of the motivating
review): join's requests carried no `egress_board` attribution and could never be routed onto the
spare egress even if a future change opted the ATS in. Fixing it needed no new machinery, only
routing through the seam that already existed for exactly this — it stayed invisible until the
seam made "a scraper reaching `http` directly instead of `self._fetch`" a diffable, greppable
fact rather than one comment's warning.

`trakstar.py`'s and `workday.py`'s own hand-rolled partial bypasses of the egress-kwargs threading
are out of scope for this pass — CLAUDE.md's surgical-changes rule, and neither was named as the
bug to fix.

## Consequences

- **Zero behaviour change for the 34 scrapers that do not inject a fetcher.** `HTTPFetcher`
  forwards to the same module functions by name, so every existing test that monkeypatches
  `http.fetch`/`http.fetch_async` keeps working against the default fetcher unchanged
  (`tests/test_base.py`, `tests/test_scrapers.py`'s 471 cases, `tests/test_icims.py`,
  `tests/test_meta.py` all pass unmodified).
- **A scraper can now be tested by injecting a fake fetcher at construction**, proven for one
  plain single-fetch board (greenhouse), the sitemap-plus-per-job-detail-pass pattern shared by
  icims/successfactors/meta (icims), and the browser adapter on both its paths (darwinbox) — see
  `tests/test_network_fetcher.py`. This is additive: none of the four pre-existing faking techniques were
  migrated or removed, only shown to no longer be the only option.
- **`self._fetcher` is per-instance, not per-thread.** `HTTPFetcher` itself carries no state (it
  forwards to the thread-local session `http.py` already manages), so this changes nothing about
  ADR-0002's pooling — a `DarwinboxScraper` instance still shares its thread's pooled session for
  every ordinary request; only the browser escalation gets its own, separately-scoped fetcher.
- **`registry.get_scraper` is unchanged.** It still constructs `cls(slug, company)` positionally;
  nothing in the pipeline injects a non-default fetcher today. The seam exists for tests and for
  a future second HTTP-shaped adapter, not because production needs one yet.
- **The `Fetcher` protocol is a documented contract, not an enforced one.** This repo runs no
  type checker in CI (`ruff` only), so a fetcher that satisfies `fetch` but not `fetch_async` (or
  vice versa) fails at the call site the day something actually calls the missing method, not at
  import time. That is the same guarantee level every other duck-typed seam in this codebase
  already has (`browser_http._chrome_factory`, for one), not a new weaker one.
