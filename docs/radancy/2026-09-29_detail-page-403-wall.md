# Radancy job pages: a third answered 403 on the runners (2026-09-29)

The served-table audit of 2026-09-29 (BPH-01) found that in one run 38,910 of 116,286 Radancy
detail pages were unreadable, 38,856 of them as HTTP 403 on 51 Boards, and asked whether it
persists and what causes it. This note measures both and records the fix (ADR-0346). Every count
is from Actions logs of the run named, or from a request made on 2026-09-29.

## Persistence: every run, 12% to 36%

The join stage of each run prints `radancy detail loss events` and `radancy detail loss causes`.
Over the 47 successful runs from 2026-09-27 12:53Z to 2026-09-29 12:51Z whose join log could be
read (three more could not be read):

- Radancy detail attempts a run: 109,522 to 123,107.
- `HTTP 403` events a run: 13,875 to 42,314, on 28 to 61 Boards.
- 403 share of attempts: 12.1% to 36.0%, median 23.7%.

The audited run is `36542219238` (38,910 lost of 116,286; 403 x38,856 on 51 Boards, 404 x53 on 17,
one timeout). The most recent complete run at the time of writing, `36570947232`, lost 26,155 of
112,891 (403 x25,938 on 48 Boards). This is not a one-run event.

## Shape: it follows the runner, not the front

Per shard, from the scrape-shard logs of three complete runs (each shard reads 9 to 12 Radancy
Boards). The loss is uneven, and every run has shards near zero beside shards above 45%:

| run | cleanest shards | walled shards |
|---|---|---|
| `36542219238` | 9: 0 of 9,742 · 3: 29 of 5,656 · 5: 14% | 11: 61% · 13: 57% · 2: 5,270 of 10,736 (49%) |
| `36557782900` | 6: 80 of 7,499 · 5: 6% · 4: 16% | 8: 62% · 11: 61% · 10: 49% |
| `36570947232` | 8: 8 of 5,358 · 13: 155 of 10,255 · 6: 8% | 10: 57% · 9: 56% · 2: 49% |

The same Board is walled in one run and clean in the next: of 162 Boards read in at least two of
the three runs, 67 lost more than 5% of their pages in at least one run, and 43 of those 67 were
read with 5% or less lost in another. The 47 Boards that lost more than 5% in the audited run had
72,666 pages listed; 103 Boards lost nothing, 37 of them with more than 260 pages (up to 3,863), so
this is not a per-front cap.

A walled Board is not walled from its first request. On 15 of the audited run's 47 Boards the
scraper read 251 to 265 pages and then got 403 for the rest (`careers.sysco.com` 256,
`careers.nva.com` 260, `jobs.jabil.com` and `jobs.appliedmaterials.com` 262, `careers.ochsner.org`
265). Other Boards read fewer: in run `36570947232`, `www.clubmedjobs.com`, `www.schwabjobs.com`
and `jobs.radancy.com` each read 5 pages, on a shard whose earlier Boards were already refused.
Five Boards failed at the sitemap, before any job page (all `HTTP Error 403` after three attempts, 10 s) (`careers.staples.com`,
`jobs.empower.com`, `www.perduecareers.com`, `careers.iehp.org`, `careers.webfleet.com`).

## Cost of the wall: three attempts at every page

403 is in `http.TRANSIENT`, so the client tries each refused page three times before giving up. Of
`careers.ochsner.org`'s 1,870 pages 1,605 were refused, and the shard's "retry demand" for the
Board was 3,212, two extra requests per lost page. `jobs.jabil.com` took 508 s to read 262 pages
and lose 1,715. Radancy set no `egress_fallback_on`, so nothing ever moved a walled shard off its
IP: shard 2 of the audited run logged `retries: 403-wall 10,723` and `retry budget exhausted:
403-wall 5,272` (Radancy lost 5,266 pages on that shard).

## What a laptop shows

All at no more than one request a second per host, the scraper's own client (`curl_cffi`
impersonating Chrome, `User-Agent: headstart/0.1`):

- The seven Boards whose sitemap a runner had been refused (the five above plus
  `www.syscocareers.cr` and `jobs.heraeus.com`) answered 200 to four clients each, 28 of 28: the
  scraper's, `curl_cffi` with its Chrome `User-Agent`, and `requests` with either `User-Agent`.
- Six Boards the runner walled after about 260 pages (`jobs.jabil.com`, `jobs.appliedmaterials.com`,
  `careers.sysco.com`, `careers.ochsner.org`, `careers.nva.com`, `careers.questdiagnostics.com`)
  each read 320 pages in a row on one HTTP/2 connection at about one page per 1.7 s: 1,920 of
  1,920 answered 200, no 403 or 429. So no count of about 256 refuses this client from this IP at
  that pace.
- Chrome through pydoll read 16 of 16 pages of `careers.amgen.com` and `jobs.jabil.com` and
  parsed each one's JSON-LD with `radancy._page_fields`.

What that establishes: the client, its headers and its User-Agent are not what a residential IP is
refused for. What it cannot: a datacenter IP (the runner's), request rate above one a second, or
the response a refused runner gets. The hosts resolve `{host}` to `{slug}.talentbrew.com` to
`{host}.edgekey.net` to `*.akamaiedge.net` (six of six hosts checked, 2026-09-29), so the 403 is
Akamai's edge in front of a `server: Kestrel` origin; the refused response itself was never
captured, since no scraper logs it.

## Why a browser is not the fix

- **Measured cost.** One Chrome tab, sequential, real job pages: 0.49 to 10.7 s, median 0.95 s,
  mean 1.76 s over 16 pages (the first page of each host took 4.8 s and 10.7 s cold), against
  ~0.12 s a page on the multiplexed HTTP path (8.34 requests/s at 16 streams, run
  `36542219238` shard 0). The repo's subresource blocking (`browser_http._install_blocking`) loaded
  2 of 6 pages: three page-load timeouts and one command timeout, so it cannot be reused here.
- **Volume.** 38,910 pages at the median is 10.3 hours on one tab, 2.6 hours at the repo's four
  tabs, or about 2,600 pages a shard over 15 shards (about 41 minutes per shard on one tab). That
  is affordable on paper.
- **It leaves from the same IP.** If the refusal is about the runner's address, a browser on that
  runner is refused too; the evidence above (uneven across shards, a Board walled one run and
  clean the next, clean from a residential IP for any client) points at the address.

## The fix, and what would prove it

`RadancyScraper.egress_fallback_on = frozenset({403})` (ADR-0346): the first 403 a shard sees marks
its Radancy group walled, and the requests after it ride the rotating spare egress (Cloudflare
WARP), exactly as Eightfold's do. WARP runs on the scrape shards: shard 2 of the audited run
connected in 1.0 s and rotated to a fresh IPv6 address for Workday at 08:24:03, 08:24:07 and
08:24:12. Akamai's edge names carry AAAA records, and `socks5h` resolves through WARP (ADR-0092).

Yardstick for the next runs, from the join log's Radancy lines: `radancy detail loss events` over
`attempted`, now 12.1% to 36.0% (median 23.7%), and the number of shards above 45% (three, four
and three in the runs above). If the loss stays there, the spare egress is refused too, and the
next step is a runner-side probe (below), not a browser.

## Not verified

- That the spare egress reaches Radancy's fronts. Cloudflare's addresses are a different address
  family and reputation from GitHub's, and nothing here sends a request from either.
- What the refused response says (server, body, `Retry-After`) and how long a refusal lasts.
- Whether the refusal depends on request rate. The laptop stayed at about one request a second;
  the scraper sends 16 concurrent streams per Board.
- Whether a browser from a runner is refused. That is a probe, not a guess.

The runner-side probe that would settle the first three sits with this run's experiment notes; it
reads one sitemap per client variant per host, then job pages one a second, and at the first
refusal writes the headers and body and retries after 5, 30 and 120 seconds with each client.
