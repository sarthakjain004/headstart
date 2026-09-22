# Eightfold TLS-fingerprint comparison

**Date:** 2026-09-22 (direct network, UTC timestamps below)  
**Scope:** one no-retry GET per host/fingerprint; no WARP, proxy, spare egress, credentials, or scraper changes.

## Result

**No evidence that an alternate `curl_cffi` TLS impersonation helps on this bounded sample.**

* `alnylam.eightfold.ai` stayed an identical `403` under Chrome, Edge, Safari, and Firefox. Its
  JSON body says `PCSX is not enabled for this user.`, so this is an API-capability response, not
  evidence of a TLS/edge block.
* `bayer.eightfold.ai`, a second known PCSX-disabled case, did the same.
* The former shared-edge-405 host `caci.eightfold.ai` and the positive control
  `jobs.nvidia.com` returned byte-prefix-identical PCSX JSON under every fingerprint.

This does **not** test whether a different egress (including WARP) would help a genuine shared-edge
budget wall. That was deliberately out of scope. It also does not establish that every fingerprint
version alias behaves the same: it tests the current Chrome baseline and one installed `curl_cffi`
representative each for Edge, Safari, and Firefox.

## Why this endpoint and these controls

The scraper's primary listing surface is `GET /api/pcsx/search`; its `domain`, `query`, `location`,
and `start` construction is at [`eightfold.py:7-15`](../../src/headstart/scrapers/eightfold.py#L7-L15)
and [`eightfold.py:214-218`](../../src/headstart/scrapers/eightfold.py#L214-L218). The production
transport is a thread-local `curl_cffi` session with `impersonate="chrome"`
([`http.py:1-18`](../../src/headstart/http.py#L1-L18), [`http.py:88-94`](../../src/headstart/http.py#L88-L94)).

`alnylam` is present as a live ledger host, while its old URL-form seed is dead
([`eightfold.csv:35-36`](../../data/validate/liveness/eightfold.csv#L35-L36)). The repository's
previous upstream comparison also recorded a PCSX 403 on Alnylam. Bayer is an independently
documented PCSX-disabled 403 control: the 2026-09-11 census saw 23/103 PCSX 403s, all explicitly
naming PCSX and all recoverable via SmartApply ([`smartapply-fallback.md:17-34`](../../docs/eightfold/smartapply-fallback.md#L17-L34)).
That distinction matters because the scraper intentionally does **not** let its first PCSX 403 mark
the ATS walled ([`eightfold.py:237-251`](../../src/headstart/scrapers/eightfold.py#L237-L251)).

CACI was selected as a previous shared-edge-405 board (not mislabelled as a 403 case); NVIDIA is a
positive control. Historical evidence says 405 can be a shared per-origin budget response rather
than a TLS rule ([`pcsx-replica-instability.md:190-223`](../../docs/eightfold/pcsx-replica-instability.md#L190-L223));
therefore every call here was sequential and direct.

## Method

Runtime: `curl_cffi 0.16.3`. Installed `BrowserType` supports the selected literal values
`edge101`, `safari180`, and `firefox147`; `chrome` is the repository's current baseline.

For every row below, a fresh `curl_cffi.requests.Session(impersonate=<fingerprint>)` performed
exactly one GET (30-second timeout) with the scraper-equivalent fixed headers:

```
User-Agent: headstart/0.1 (job-board reader)
Accept: application/json
Referer: https://<host>/careers
```

No retry wrapper was used. The complete route template was
`https://<host>/api/pcsx/search?domain=<domain>&query=&location=&start=0`.
`prefix-sha256` is SHA-256 of the first 512 response bytes; `schema` is decoded JSON shape.
There were no exceptions or redirects.

## Exact direct results

| Host / reason | Fingerprint | UTC | HTTP | Bytes | ms | prefix-sha256 | Schema / result |
|---|---|---:|---:|---:|---:|---|---|
| alnylam.eightfold.ai / prior PCSX 403 | chrome | 15:28:28.195 | 403 | 50 | 1580.8 | `efe3bf40b88666ebf55b1ed6144350ec11569a71f76c67534adb2806d176f79a` | `dict{message}`; `PCSX is not enabled for this user.` |
| same | edge101 | 15:28:29.776 | 403 | 50 | 1646.7 | same | same |
| same | safari180 | 15:28:31.423 | 403 | 50 | 1309.8 | same | same |
| same | firefox147 | 15:28:32.732 | 403 | 50 | 1621.0 | same | same |
| bayer.eightfold.ai / documented PCSX-disabled 403 | chrome | 15:28:34.354 | 403 | 50 | 1180.8 | `efe3bf40b88666ebf55b1ed6144350ec11569a71f76c67534adb2806d176f79a` | `dict{message}`; same PCSX-disabled message |
| same | edge101 | 15:28:35.534 | 403 | 50 | 758.7 | same | same |
| same | safari180 | 15:28:36.293 | 403 | 50 | 714.9 | same | same |
| same | firefox147 | 15:28:37.008 | 403 | 50 | 650.6 | same | same |
| caci.eightfold.ai / prior load 405 | chrome | 15:28:37.659 | 200 | 90088 | 1433.6 | `3f17feb8f1928c45a2349e1f403ae7338a80a00c970e9c91375b08cc55f68545` | `dict{status,error,data,metadata}`; `data.count=1895`, 10 positions |
| same | edge101 | 15:28:39.094 | 200 | 90088 | 1598.3 | same | same |
| same | safari180 | 15:28:40.693 | 200 | 90088 | 1660.7 | same | same |
| same | firefox147 | 15:28:42.355 | 200 | 90088 | 1438.7 | same | same |
| jobs.nvidia.com / positive control | chrome | 15:28:43.794 | 200 | 9437 | 677.5 | `130443829f29a81bc980ad9d5e816484d41c4a6275f6f49dbe1ec522b005c042` | `dict{status,error,data,metadata}`; `data.count=2691`, 10 positions |
| same | edge101 | 15:28:44.472 | 200 | 9437 | 543.8 | same | same |
| same | safari180 | 15:28:45.016 | 200 | 9437 | 535.0 | same | same |
| same | firefox147 | 15:28:45.551 | 200 | 9437 | 576.0 | same | same |

For the 200 schema, `data` keys were
`appliedFilters,count,debug,filterDef,positions,resultsMetaData,savedSearchMetadata,sortBy`.
The 403 response prefix was exactly `{"message": "PCSX is not enabled for this user."}\\n`.

## Decision

Do not change Eightfold's Chrome impersonation or add TLS-fingerprint fallback from this evidence.
Both blocked hosts have the known, content-identical PCSX-disabled response that the existing
SmartApply branch handles; both healthy hosts work across all four fingerprints. A future experiment
would need to capture a reproducible **generic** direct 403/405/429 from a fresh shared-edge budget
event and counterbalance it against a known-good host, while separately preserving the no-WARP rule.
