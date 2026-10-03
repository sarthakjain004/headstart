# Common Crawl and Wayback ATS sweep — 2026-10-03

Common Crawl's [live crawl catalogue](https://index.commoncrawl.org/collinfo.json)
still lists **CC-MAIN-2026-39**, September 2026, first. Its capture window is
September 4–17; no newer crawl was published when checked on October 3.

The scraper registry contains 55 providers. This sweep accounts for every one:
42 have enumerable archive namespaces, five use customer-owned domains, and eight
are single-company scrapers exempt from tenant discovery. `test_archive_targets.py`
checks both miners against the registry so a new scraper cannot silently be omitted.

The namespace reconciliation adds Freshteam, JazzHR, Jobvite, SuccessFactors,
Zwayam's `openings.co` boards and Join to the missing source lists, and brings CC's
regional and legacy targets into line with Wayback: Greenhouse ANZ, Zoho's eight
regions, `myworkdaysite.com`, Recruiterbox and the older Workable subdomain boards.
Extraction also preserves Teamtailor's `.na` slugs, Ashby's encoded spaces and
Workday's two-letter sites in posting deep links.

## Scope of company-domain coverage

Phenom, Radancy, Happydance, Spire2Grow and WP Job Openings have no shared public
board hostname that a CDX domain sweep can enumerate. Their 5,549 known hosts from
the liveness ledgers and discovery pools are audited separately. That establishes
archive presence, **not discovery of unknown customers**, and is never counted as
new Board coverage. Eightfold, SuccessFactors, Zwayam and gr8people also have
company-domain Boards alongside their vendor namespaces; their known hosts outside
the swept namespaces receive the same audit. WP Job Openings additionally has an existing global columnar
fingerprint miner; it scans 600 Parquet files in this crawl.

## Running and resuming

Run from the repository root with the project's Python environment:

```sh
PYTHONPATH=src CC_DATA_HOST=1 python -u scripts/discover/cc_miner.py CC-MAIN-2026-39
PYTHONPATH=src python -u scripts/discover/run_wayback_sweep.py \
  --since 20260917 --report data/discover/run-2026-10-03/wayback_report.json
```

The October 3 Wayback run uses captures since September 17, the end of CC's latest
capture window. Omitting `--since` requests the full historical archive. Keep the
same report and window to resume: successful targets are skipped, incomplete ones
are retried and page/resume cursors are retained. A report from a different window
is rejected. Candidate CSVs remain additive.

CC writes `data/discover/cc_ats_tenants.csv`, its page checkpoints and
`data/discover/cc_sweep_report.json`. Wayback writes one candidate CSV per provider
under `data/wayback-ats/`, plus the requested status report. An exhausted request
is incomplete, never an empty success. The sweep runners return exit code 3 when
any target remains incomplete. CC falls back to the data host per failed API target
and continues to later ATSes. The known-host audit downloads the sparse index once
and shares CDX block reads across hosts. It publishes completed hosts in batches
of 50, after all of each host's blocks finish, preserving unfinished hosts for retry.
Common Crawl SURT keys collapse `www.` and apex spellings; the audit reads their
shared range (see [Common Crawl's toolkit](https://github.com/commoncrawl/cdx_toolkit)).

The CC CDX, data-host and Wayback feeder HTTP clients now use `headstart.network.http.fetch`. Direct 429s move
subsequent attempts onto `spare_egress`; a further proxy refusal rotates the spare.
Retries back off, use the shared client's **30-second Retry-After cap**, and pass
through a route pacer. Exhausted CC pages are not checkpointed; Wayback raises
`FetchError` and keeps failed pages/cursors retryable. Refreshing a page-based
Wayback sweep clears old success markers before fetching, so an old success cannot
hide a newly failed page.

WARP was checked in proxy mode (`socks5h://127.0.0.1:40000`) with IPv6 egress.
Real Wayback CDX and CC data-host requests returned 200 through that route.
The CC index API closed the proxied connection during a separate live check; the
data-host fallback is therefore necessary even with working WARP. Deterministic
regressions exercise 429 backoff, direct-to-spare routing, proxy refusal rotation,
failed refreshes, sparse blocks, and sweep resumption. They do not claim that an
actual vendor 429 was induced during this run.

Discovery outputs are candidate-grade. No liveness ledger or served data is
modified by the archive sweep. Unknown candidates require scraper `board_key`
reconciliation, live probing and the provider's landing/alias checks before landing.

## Review corrections

The Standards review found that the bulk known-host audit buffered its results
until every block finished. Completed hosts now publish while the audit runs.
The Spec review found the hybrid-provider tails above and an Eightfold identity
collision: the old label extractor emitted `paypal` alongside the correct
`paypal.eightfold.ai`. Both extraction and resumed scratch rows now use the full
Board host. The resumed namespace outputs are checked before reporting counts.

The first WP Job Openings fingerprint pass completed 323 of 600 parts. The
remaining 277 returned native DuckDB HTTP 403s. Small direct and spare range/HEAD
probes of an incomplete object succeeded, and retrying at one worker resumed
successful reads. This global scan uses DuckDB's native retry policy; its HTTP
proxy setting rejected a SOCKS5 URL in a live metadata probe. It does not inherit
the Python feeder's spare-egress policy. Completion is checked against the manifest,
not the miner's process exit code.
