# Resilience

How a discovery run survives long enough to finish. Every rule here was paid for by a run that
lost work.

## Checkpoint or lose everything

Mining writes candidates to `data/wayback-ats/{ats}.csv`, which is **gitignored scratch**. Only
`check_liveness.py` promotes verified Boards into `data/validate/liveness/{ats}.csv`, which is the
deliverable.

Run `python scripts/validate/check_liveness.py` **every time a batch accumulates**, not once at the
end. Measured outcome across seven concurrent runs: agents that checkpointed lost nothing to a
mid-run stall; one that had staged 1,568 candidates and never landed them lost the entire session.

The checker is incremental and ledger-backed (ADR-0012), so repeated runs are cheap.

## Do the work inline

A discovery agent that delegates to a sub-agent inherits its failure modes and loses whatever the
child held. Keep every step in one process.

## Block triage

Three failures look similar and need opposite responses. Diagnose before reaching for a tool.

| Symptom | Cause | Response |
|---|---|---|
| `cf-mitigated: challenge`, a JS shell, HTTP 202 with no body | Fingerprint challenge | `curl_cffi`, then `cloudscraper` |
| HTTP 429, `Retry-After` | Rate limit | Back off and slow down |
| Connection refused, `code=000`, TCP reset | IP-level block | Rotate the egress IP |

**Challenges.** `curl_cffi` is already HeadStart's only base dependency — added because Darwinbox
sits behind Cloudflare TLS fingerprinting — and `src/headstart/http.py` wraps it. It impersonates a
real browser's TLS/JA3 signature and defeats most fingerprint blocks alone. Reach for `cloudscraper`
only after it fails. Keep either out of the base `dependencies` list: CI installs base deps only, so
a new import there breaks the quality job. Note the requirement in the script's docstring instead.

**Rate limits.** Rotating an IP to dodge a 429 is both ineffective and impolite; ADR-0026 makes
per-host politeness binding. Wayback allows roughly 60 requests/minute, and ignoring 429s for a
minute earns a **one-hour firewall block that doubles on each repeat**.

## WARP IP rotation

For genuine IP-level blocks.

```bash
warp-cli registration new     # accept ToS
warp-cli mode proxy           # proxy mode, never VPN mode
warp-cli proxy port 40000     # SOCKS5 on localhost:40000
warp-cli connect
curl -x socks5h://127.0.0.1:40000 https://www.cloudflare.com/cdn-cgi/trace   # socks5h, not socks5 (ADR-0092)   # must differ from plain curl
```

**Proxy mode is load-bearing.** VPN mode routes *all* machine traffic through Cloudflare, including
the agent's own API connection, which kills the run mid-flight. Proxy mode touches only the clients
pointed at port 40000, so route the miner through the SOCKS5 proxy and leave everything else direct.

Rotate by rebuilding the registration:

```bash
warp-cli disconnect && warp-cli registration delete && warp-cli registration new && warp-cli connect
```

Two properties to expect. **Rotation can be a no-op** — Cloudflare sometimes reassigns the same
endpoint, so verify and rotate again. And **WARP egress IPs are shared Cloudflare ranges** that some
services block outright, so confirm the new IP works before committing a long sweep to it.

## Route around the block instead

Common Crawl publishes a columnar Parquet index at `s3://commoncrawl/cc-index/table/cc-main/warc/`,
queryable with DuckDB, with no rate limit — roughly 300 GB per monthly crawl. When the index API
keeps blocking, this removes the problem rather than fighting it. Athena works too but retries 503s
poorly; Common Crawl itself downloads the parquet and uses DuckDB.

## Fallthrough detection

A probe that returns the **same job count for many Boards** is broken, not lucky. One run recorded
38 Boards at exactly 63 jobs, including Accenture and ADP — companies with thousands of openings.
Direct probing showed both sitemaps returning identical structure, i.e. a template response, not
per-tenant data.

Guard against it: **assert variance across a sample before writing any batch.** If a probe cannot
produce a real count, the honest verdict is `unknown` — a value that gets re-probed — not a
fabricated number and not `dead`.

The general form: verify a claim against an independent surface before trusting it. A plausible
wrong number survives review precisely because it looks like an answer.

## Trust nothing that has not been run

`mine_zoho.py` could not execute at all — its helper path went stale in a reorg and `check=False`
swallowed the failure, so it reported success while mining nothing for weeks. Its docstring also
named five data centres where ten exist.

Before building on a miner, run it and watch it produce output. Before trusting a docstring's list,
enumerate the real thing.

## Key format collapses Boards silently

Ledgers key on `{ats}:{slug}`, but the `tenant` column sometimes holds a bare label where the Slug
is really a host. Zoho lost 44 distinct Boards this way: `acme.zohorecruit.in` and
`acme.zohorecruit.com` are different companies that collapsed into one row.

When the Slug is a host or a URL, dedupe on the **full** value, and keep `url` populated so
`slug_from(tenant, url)` can recover the canonical form.
