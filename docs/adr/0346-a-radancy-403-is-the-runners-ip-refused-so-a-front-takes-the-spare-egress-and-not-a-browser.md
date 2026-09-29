# ADR-0346: A Radancy 403 is the runner's IP refused, so a front takes the spare egress and not a browser

**Status:** accepted · **Date:** 2026-09-29 · **Relates to:**
[ADR-0246](0246-a-radancy-career-front-is-a-board-keyed-by-its-host-scraped-in-full.md) (the Radancy
scraper), [ADR-0063](0063-spare-egress-for-a-spent-origin-budget.md) (the spare egress; its
opt-in rule), [ADR-0081](0081-the-spare-egress-pool-is-deep-not-1-3-addresses.md) and
[ADR-0092](0092-resolve-through-warp-not-before-it.md) (the pool, and why it is IPv6),
[ADR-0056](0056-darwinbox-browser-escalation.md) and
[ADR-0228](0228-a-detail-pass-may-send-its-requests-in-batches-from-one-warmed-tab.md) (the browser
transports), [ADR-0053](0053-scope-eviction-on-scrape-outcome.md) and
[ADR-0121](0121-a-negligible-shortfall-is-still-an-authoritative-list.md) (what a short Board does
to eviction)

## Context

The served-table audit of 2026-09-29 (BPH-01) found that in one run 38,910 of 116,286 Radancy job
pages (33.5%) came back unreadable, 38,856 of them as HTTP 403 on 51 Boards. The owner's steer: a
public job page cannot really 403, so it is a client-shaped wall, and a browser (pydoll) should
read it. `docs/radancy/2026-09-29_detail-page-403-wall.md` holds every count below.

- **It is every run.** Over 47 runs from 09-27 to 09-29, 12.1% to 36.0% of 109,522 to 123,107
  detail attempts a run were refused (median 23.7%), on 28 to 61 Boards. A Board that lost pages
  is unauthoritative, so its missing ids are unscraped rather than closed (ADR-0053), and a job
  whose page never arrived is never served: the loss is coverage, not eviction.
- **It follows the runner.** Per shard, three runs of 15: one or two shards lose 2% or less (0 of
  9,742 pages on one) beside three or four that lose above 45% (5,270 of 10,736 on one). Of 67 Boards
  walled in one of three runs, 43 read clean in another. On 15 of 47 walled Boards in the audited
  run the scraper read 251 to 265 pages before the first refusal.
- **The client is not what it is refused for.** From a laptop the scraper's own client
  (`curl_cffi` impersonating Chrome, `User-Agent: headstart/0.1`) read 1,920 of 1,920 pages of six
  walled Boards at about one a second, 28 of 28 sitemap requests (four client variants) to seven
  Boards a runner's sitemap fetch had been refused on, and Chrome through pydoll read 16 of 16
  pages. The fronts sit behind Akamai (`{host}` to `{slug}.talentbrew.com` to `edgekey.net`).
- **Nothing moved a walled shard.** Radancy set no `egress_fallback_on`, and 403 is in
  `http.TRANSIENT`, so each refused page was tried three times on the same IP (one Board: 1,605
  pages lost, 3,212 retries) and the shard stayed on the refused address for the run.

That is the shape ADR-0063 named for Eightfold: a wall about the shard's IP rather than the
request, uneven across shards, the same host clean from another address. What a laptop cannot show
is the runner's side: the refused response itself (no scraper logs it), how long a refusal lasts,
and whether the rate matters.

## Decision

**A 403 from a Radancy front marks its group walled and moves the shard onto the spare egress.**
`RadancyScraper.egress_fallback_on = frozenset({403})`, the whole change. The seam already carries
it on every request the scraper makes (`base._fetch`, and `run_detail_pass` through the Board's
fetcher): robots.txt, the sitemap, the stated total and each job page. The group is the ATS, so
the first refusal on a shard moves all of that shard's Radancy Boards onto WARP, and a refusal seen
through it rotates the address, exactly as for Eightfold. Retry and `Retry-After` still come first;
the fallback never replaces pacing.

WARP runs on the scrape shards (shard 2 of the audited run connected in 1.0 s and rotated three
times in twelve seconds), and the Akamai names carry AAAA records, so `socks5h` egresses over
WARP's IPv6 pool (ADR-0092).

**Not a browser.** The refusal follows the address, and a browser on the same runner leaves from
that address. It is also 8 times slower where it does work: 0.95 s a page (median of 16, mean
1.8 s, first page of a host 4.8 s and 10.7 s) against about 0.12 s on the multiplexed HTTP path,
and the repo's subresource blocking loaded 2 of 6 pages, so a fallback would need its own
transport. 38,910 pages is 10.3 hours on one tab, or about 41 minutes a shard over 15 shards on
one tab: affordable on paper, and worth building only if the spare egress is refused too.

**The yardstick is the join log's Radancy lines**, not the shard report's rescue rate (which is
blind by construction, see the note on `BaseScraper.egress_fallback_on`): `radancy detail loss events` over `attempted`, from
12.1% to 36.0% now, and the number of shards above 45%, three or four a run now. Whatever a run
says, the change stays reversible by deleting the one line.

## Alternatives considered

- **A pydoll fallback for refused pages.** Rejected for now, above. Reopen it if the spare egress
  is measured refused (loss share unchanged after the change lands) and a runner-side probe shows
  Chrome passes where the scraper's client does not.
- **A browser-like `User-Agent`.** Rejected: the laptop read 28 of 28 sitemap requests with the
  scraper's own, and the repo sends an honest one on purpose.
- **Narrower per-Board concurrency.** Unmeasured: the laptop never went above one request a second,
  the scraper sends 16 streams, and no log records the rate at which a refusal began. The spare
  egress narrows a walled group to 12 streams anyway (`spare_egress.stream_width`).
- **Read the ids not yet held first.** A walled Board serves the same first ~260 pages in sitemap
  order every run, so its tail never converges. Ordering unheld ids first would let a fixed budget
  cover a Board over several runs, but it does nothing for a shard that reads 5 pages, and it is
  not needed if a rotation buys a fresh address. Left as the follow-up if the loss stays.
- **Stop the retry ladder on a wall.** Rejected here: with the opt-in, `http` already rotates
  instead of retrying on the refused IP.

## Consequences

- If Cloudflare's addresses are refused by these fronts, nothing improves and every shard pays a
  WARP dial once its first Radancy page is refused. The runner-side probe
  (`experiment/radancy-403-and-coverage-2026-09-29/`, not committed) settles the refused response,
  its duration and the rate question without changing the scraper.
- Radancy's Boards move to the spare egress together, including the ones a shard reads cleanly,
  once one is refused; the cost is the WARP path's latency, and the width narrows from 16 to 12.
- The 13 capped fronts' undrained scope exclusion (ADR-0246) is unchanged.
- Not verified: that the spare egress reaches Radancy's fronts at all, what a refused response
  says, and whether a refusal depends on rate. The first run after this merges answers the first.
