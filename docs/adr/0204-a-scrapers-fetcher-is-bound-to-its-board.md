# ADR-0204: A scraper's fetcher is bound to its Board

**Status:** accepted · **Date:** 2026-09-24 · **Extends:**
[ADR-0199](0199-the-fetcher-seam-reaches-every-scraper.md) (every request now goes through the
scraper's fetcher, but still carries its Board's egress binding as spread keyword arguments) ·
**Relates to:** [ADR-0153](0153-a-fetcher-seam-replaces-the-module-global-http-import.md) (the
`Fetcher` seam), [ADR-0063](0063-spare-egress-for-a-spent-origin-budget.md) (the spare egress whose
opt-in this binds), [ADR-0195](0195-one-retry-policy-drives-both-fetch-paths-and-the-warp-daemon-sits-behind-a-port.md)

## Context

ADR-0199 finished routing every Scraper's HTTP through its `Fetcher`, including Workday's listing
POST, both cookie resets and Trakstar's feed. What it left as it was is how a request learns which
Board it belongs to. The protocol is `fetch(method, url, **kwargs)`, and the kwargs that matter —
`egress_group`, `egress_on` and `egress_board`, which opt a Board into the spare egress and name
it in the retry log — are built by `BaseScraper._egress()` and have to be spread into every call.
`egress_fallback_on`'s own comment said a scraper calling its fetcher directly "must pass
`**self._egress()` itself, or setting this is silently inert". Workday's listing did, in two
places, with `**({} if direct else self._egress())`. `BaseScraper.fan_out_async` and Workday's
page fan-out re-read the group from `_egress()` to call `spare_egress.stream_width`.
`BrowserFetcher.fetch` took `**_ignored`, so it dropped any of these without a word.

## Options considered

- **(a) A `BoardFetcher` wrapping the transport, bound once per scraper.** It holds the Board key,
  the egress group and the wall statuses; adds exactly the kwargs `_egress()` built; and carries
  the Board-level operations the scrapers need: `stream_width` and `clear_cookies`. The transport
  keeps ADR-0199's `Fetcher` protocol unchanged, and `HTTPFetcher` still calls
  `http.fetch`/`http.fetch_async` by name, so the tests that monkeypatch those keep working.
- **(b) Make the transport itself Board-aware** (`HTTPFetcher(board=…)` built per scraper). Every
  injected fake would then have to learn the binding, and the egress rules would be written into
  each transport rather than once.
- **(c) Keep `_egress()` and add a lint** that flags a request without it — a check on a
  convention, where an interface can make the convention unnecessary.

**Decision: (a).**

## Decision

- `headstart.fetcher.BoardFetcher(transport, *, board, egress_group, wall_statuses)`:
  `fetch`/`fetch_async(…, marks_wall=True, direct=False, **kwargs)`, `egress_binding(marks_wall)`,
  `stream_width(ceiling)` and `clear_cookies(domain=None)`. `marks_wall=False` keeps the routing
  and drops the marking, as `_egress(marks_wall=False)` did. `direct=True` sends no binding at all,
  which is what Workday's once-via-direct retry and `alias_key`'s redirect probe always sent.
- `BaseScraper.board_fetcher` builds it and `_egress()` is gone. `_get`, `_get_async`, `_fetch`,
  `_fetch_async`, `alias_key` and `fan_out_async` go through it, and so do Workday's listing, its
  two cookie resets and its page fan-out, and Cornerstone's cookie reset.
- **Bound on first use, not in `__init__`.** The binding needs `board_key()`, and Workday's reads
  `_instance`, which its `__init__` sets only after `super().__init__` returns; a malformed Workday
  slug also raises from `board_key()`, and that has always failed the Board's first request rather
  than the scraper's construction. A `cached_property` binds once per scraper, on the first request.
- `BrowserFetcher.fetch` takes `json` only and raises `TypeError` for anything else. A tab has one
  origin and its own network stack, the spare egress cannot route it, and no `BoardFetcher` wraps
  it. Darwinbox, its only caller, passes `json` alone.

## Consequences

- **The wire shape is unchanged.** `BoardFetcher` adds the same kwargs `_egress()` built, in the
  same cases; the tests that assert those kwargs on Workday's listing, its direct retry and the
  opted-in scrapers pass unchanged apart from reading the binding from `board_fetcher`.
- **A request cannot drop its Board's binding by omission.** The only way to send one without it
  is `direct=True`, which says so at the call site.
- **A caller that swaps `_fetcher` must do it before the first request**, because the binding is
  cached on first use. `registry.get_scraper(…, fetcher=)` injects at construction, which is the
  supported path.
