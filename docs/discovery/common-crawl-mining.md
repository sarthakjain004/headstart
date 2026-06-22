# Mining Common Crawl for India ATS tenants

A record of how we mined Common Crawl's URL index directly to build a from-the-source
catalog of the **India-tier** ATS tenants. HeadStart's scope is global; the global four
(Greenhouse/Lever/Ashby/Workday) come from jobhive + Wayback, so this mine deliberately
targets the India gap those lists miss — that whole family skews US/global and is thin on
India. For *where this fits* among
discovery methods generally, see [`overview.md`](./overview.md); for the
focused-crawler design, see [`crawler-design.md`](./crawler-design.md). This doc is
the operational story of the one run: what we ran, what broke, and what we got.

_Last updated: 2026-06-17._

## What we did, in one line

Queried Common Crawl's per-crawl CDX index for 13 ATS host patterns across every monthly
snapshot from late 2013 to mid-2026, extracted the tenant subdomain from each captured URL,
deduped, and wrote the result to [`data/discover/india_ats_tenants.csv`](../data/discover/india_ats_tenants.csv).

## Result

**18,035 distinct tenant hosts** across 13 providers, from **119 of Common Crawl's 124
monthly crawls** (every snapshot from `CC-MAIN-2013-48` through `CC-MAIN-2026-21`).

| Provider | Tenants | India-specific? |
|---|---:|---|
| recruitee | 5,992 | global |
| workable | 4,529 | global |
| zoho (Recruit) | 3,329 | **India-heavy** |
| freshteam | 2,003 | **India** |
| keka | 755 | **India** |
| jobsoid | 433 | **India** |
| darwinbox | 391 | **India** |
| greythr | 217 | **India** |
| peoplestrong | 205 | **India** |
| turbohire | 73 | **India** |
| ripplehire | 56 | **India** |
| qandle | 38 | **India** |
| beehive | 14 | **India** |
| **Total** | **18,035** | |

The eleven India-native systems (everything except recruitee/workable) are the payoff:
these are the boards LinkedIn and the global aggregators don't mirror. Six of them
(peoplestrong, jobsoid, ripplehire, turbohire, qandle, beehive) we found by probing
candidate domains *during* the mine, beyond the original Zoho-only target.

## The approach: per-crawl CDX index

Common Crawl publishes a [CDX index](https://index.commoncrawl.org) per monthly crawl. You
query one host pattern at a time and it returns the captured URLs:

```
https://index.commoncrawl.org/CC-MAIN-2024-51-index?url=zohorecruit.in&matchType=domain&output=json&fl=url&limit=10000
```

`matchType=domain` matches the domain and every subdomain, so one query per ATS host
enumerates that provider's tenants in that crawl. We take the subdomain label
(`blazeclan.zohorecruit.in` → `blazeclan`), drop infra labels (`www`, `api`, `careers`, …),
and keep the full host as the tenant key. The 13 host patterns:

```
zohorecruit.in/.com/.eu, freshteam.com, darwinbox.in/.com, keka.com, greythr.com,
peoplestrong.com, jobsoid.com, ripplehire.com, turbohire.co, qandle.com,
beehivehcm.com, workable.com, recruitee.com
```

The crawl list comes from [`collinfo.json`](https://index.commoncrawl.org/collinfo.json)
(cached locally to [`data/discover/cc_index_cache.txt`](../data/discover/cc_index_cache.txt)).

### The tool

[`cc_miner.py`](../scripts/discover/cc_miner.py) — stdlib + `curl`, fully resumable:

- reads the crawl list and the set of already-finished crawls
  ([`data/discover/cc_miner_checkpoint.txt`](../data/discover/cc_miner_checkpoint.txt));
- for each unfinished crawl, queries all 13 domains, unions the tenants, and marks the crawl
  done **only on full success**;
- aborts a crawl on the first failed query (so a throttled crawl is detected in seconds, not
  after grinding all 13), then exits cleanly to be relaunched;
- writes [`data/discover/india_ats_tenants.csv`](../data/discover/india_ats_tenants.csv) after every crawl.

Run / resume:

```
python -u scripts/discover/cc_miner.py all      # picks up from data/discover/cc_miner_checkpoint.txt
```

## What broke, and the fixes

**404 "No Captures found" read as a throttle.** The CDX API returns `404
{"message":"No Captures found"}` when a domain has no captures in a crawl — a *legitimate
empty result*. Our first `curl --fail` treated that as failure and aborted the crawl. It
went unnoticed for 47 crawls because those were all recent (2024–2026) where every provider
had captures; it only surfaced at `CC-MAIN-2020-45`, where the newer ATSes (turbohire,
qandle, beehive — founded ~2019–2021) legitimately have zero captures. Fix: capture the HTTP
status — `200` parse, `404` legitimate-empty, only `429/403/5xx`/timeouts are real blocks.

**Flaky old indexes.** The 2013–2018 crawls are served more slowly and throw intermittent
`502`s. Fix: bumped per-query retries to 6 with backoff, so a transient `502` is absorbed
within a crawl instead of aborting it.

**Transient single-query blips.** Even on a healthy route, one query occasionally fails and
takes the whole crawl down with it (abort-on-first-fail). Fix: an auto-relaunch wrapper —
relaunch on exit (each relaunch resumes from the done-set), with a stall counter that gives
up only after several consecutive relaunches make zero progress (a genuinely sustained
block, vs a blip that clears on the next try).

**Windows `/tmp` ≠ Git Bash `/tmp`.** Windows Python writes to `C:\tmp`; Git Bash `/tmp` is
elsewhere. Write scratch files into the repo dir, not `/tmp`.

## The real blocker: per-IP connection blocks

Common Crawl rate-limits by IP, and not gently: after a few hundred queries (~35–40 crawls)
it **blocks the IP at the TCP level**. Symptom — `curl` returns code `000` (connection
reset, zero bytes) for *every* `index.commoncrawl.org` request, including the static
`collinfo.json`, while other sites (e.g. ipinfo.io) still load fine over the same
connection. So it's CC refusing the IP, not the network dropping.

We confirmed this block on every route we tried: the home ISP range, Cloudflare WARP's
range, and **two separate NordVPN exits** (a Seattle IP, then a London IP) — each one good
for ~35–40 crawls before CC cut it off. The only client-side escape is a **fresh exit IP**:
when blocked, switch the VPN to another server and rerun `scripts/discover/cc_miner.py` — it resumes where it
stopped. That rotation is how we covered the full range across three IPs.

The 2008–2012 tail (4 crawls) was deliberately skipped: counts had been frozen since the
2016 crawls (every ATS we track postdates them), so they add nothing. The data shows it —
recruitee stopped growing by `CC-MAIN-2016-22`, zoho by the 2018 crawls.

## Alternatives considered

**Columnar index (DuckDB / Athena).** Common Crawl also ships the index as
[columnar Parquet](https://commoncrawl.org/blog/index-to-warc-files-and-urls-in-columnar-format)
partitioned by crawl, with a `url_host_registered_domain` column — queryable with DuckDB
over HTTPS ([`duck_probe.py`](../scripts/discover/duck_probe.py)) or AWS Athena. DuckDB works mechanically but
CC throttles the HTTPS range-GETs on burst the same way, and anonymous S3 *listing* is
denied. **Athena (server-side, runs inside AWS) is the robust route for a complete run** and
sidesteps the per-IP block entirely — the fallback if we ever need 100% historical coverage
without IP-rotating by hand.

## Honest limits

- Common Crawl is a *sample*, not a complete crawl, and [caps URLs per
  host](https://commoncrawl.org/blog/introducing-the-host-index) — so recall is below 100%
  even within the crawled snapshots. Union with the Wayback feeder
  (see [`overview.md`](./overview.md)) to close gaps.
- Output is **candidate-grade**: it spans 13 years, so it includes long-dead boards. A tenant
  that appeared in a 2019 crawl may be gone today. Liveness-check before scraping.
- This is a *tenant catalog*, not yet active coverage. We have a working scraper for one of
  the India-native providers (Zoho); the rest (freshteam, darwinbox, keka, …) need scrapers
  built before their tenants here become scrapeable jobs. recruitee and workable are simple
  JSON APIs (jobhive has both as reference).

## Files

- [`cc_miner.py`](../scripts/discover/cc_miner.py) — the miner (run with `python -u scripts/discover/cc_miner.py all`).
- [`data/discover/india_ats_tenants.csv`](../data/discover/india_ats_tenants.csv) — the 18,035-tenant catalog (`ats,host`).
- [`data/discover/cc_index_cache.txt`](../data/discover/cc_index_cache.txt) — cached crawl list.
- [`data/discover/cc_miner_checkpoint.txt`](../data/discover/cc_miner_checkpoint.txt) — finished-crawl set (resume state).
- [`scripts/discover/duck_probe.py`](../scripts/discover/duck_probe.py) / [`scripts/discover/probe_ats.py`](../scripts/discover/probe_ats.py) — columnar query + domain-probe experiments.
