# Indeed host fingerprinting: corrected identities and measured deep recovery

Measured locally on 2026-09-22 in the isolated `8832/HeadStart` checkout. The main checkout's
Indeed harvest was read only; no API credential, raw harvest, ledger edit, or new Board is included
in these changes.

## Result on previously unknown hosts

The input was frozen before the comparison: the 24 highest-job-volume hosts present in the current
harvest that the old `host_fingerprints.json` called `unknown` or `error`. They represented 28,284
harvested jobs. This is a targeted hard-case sample, not an estimate of corpus-wide recall.

| Measurement | Static/DNS baseline | Deep pass, final rerun |
| --- | ---: | ---: |
| Inputs | 24 | Same 24 |
| Raw ATS signals | 11 | 13 |
| Candidates after current affiliation safeguards | 5 | 7 |
| Wall time, eight workers | 143.69 s | 177.44 s |

An earlier deep revision also found 13 signals in 161.66 s. These are individual live-network
runs, not a controlled latency distribution or a claim of overall speedup. The final pass adds
two signals for 33.75 seconds (+23.5%) over this baseline.

Both added detections came from provider-specific API responses:

| Host | ATS | Harvested jobs in frozen input | Live API job count | Same-job evidence |
| --- | --- | ---: | ---: | --- |
| `careers.microland.com` | Zwayam | 548 | 1,135 | Three harvested job URLs matched API records |
| `careers.persistent.com` | Zwayam | 512 | 688 | Three harvested job URLs matched API records |

The live counts are the boards' API counts, not counts from the geographically filtered Indeed
harvest. These are new **fingerprinting recoveries**, not claims of new ledger Boards.

The raw 11/13 totals include unsupported providers and a quarantined name-only Google→Recruitee
guess. Two old wrapper groups also mixed employers (`dsp.prng.co`, `rr.jobsyn.org`). The corrected
candidate totals exclude those three cases. New inputs partition unknown hosts by employer key;
frozen host-only mixed-employer inputs remain explicitly ambiguous.

Certificate and browser channels added **zero primary ATS detections** in this sample. The
certificate pass retained 125 distinct SAN names as unclassified seeds. A separate live handshake
against Persistent returned 66 SANs. Neither number is a count of verified Boards. Browser rendering
was exercised live and is available for later unresolved inputs; its incremental value here was zero.

## What changed

- Reused `fingerprint_careers.py`; the old experiment fingerprinter remains unused.
- Full apply URLs and employer keys survive ingestion. Shared providers group by canonical Board,
  while shortlinks keep their redirect identity. Greenhouse shortlinks are included by default.
- Corrected provider slug/host rules, real Workday URL forms, provider self-filtering, hostname
  boundaries, and social-link false attribution. Radancy/TalentBrew and additional CNAME aliases
  remain explicit detections even without a scraper.
- Failed DNS/HTTP and changed evidence are retryable. Actual enabled channels and deep mode are
  recorded in the cache identity. Job-count changes refresh metadata without reprobing a host.
- Direct URLs and usable DNS results stop before HTTP. A deterministic three-repeat transport
  fixture fell from 9 HTTP calls to 0 with the same Phenom Board identity. This demonstrates request
  elimination on that path, not an end-to-end live latency claim.
- Verification reuses `check_liveness.py`'s read-only probes once per Board. It does not fetch entire
  scraper listings or write liveness ledgers. Unsupported/unimplemented checks remain explicit.
- Job reconciliation compares exact native job identifiers/paths against source apply URLs for
  Zwayam, Phenom, Workday, and Greenhouse. It reads at most three listing pages and caches their
  responses per Board, while retaining per-input matches. Missing matches are inconclusive, not dead.
  Up to three original apply URLs may be followed to recover shortlink/vanity destinations; a
  successful match is reported against the original URL. Workday alternate domains are reconciled
  by tenant/site identity, and Zwayam's fragment-based job routes are recognized.
- Taleo apply URLs no longer collapse to the unsupported `taleo:<host>` bucket. Enterprise URLs
  retain `careersection/{section}` and become `taleo_enterprise`; Business Edition URLs retain
  shard/instance plus `org`/`cws`, replace the job-specific `viewRequisition` path with the
  canonical `searchResults` path, and become `taleo_be`. Host-only Taleo evidence stays generic
  because a hostname cannot identify either kind of Board. The Indeed adapter deliberately
  reprocesses older `_ats: taleo` rows from their preserved full apply URLs.

## Taleo correction on the live v3 harvest

Read-only replay on 2026-09-23 over the completed 679,687-Job v3 harvest classified 2,258 of its
2,268 generic Taleo records: 1,603 Enterprise and 655 Business Edition. Ten job URLs on two hosts
(`manpower.taleo.net`, 7; `valero.taleo.net`, 3) use `/careersection/jobdetail.ftl` with no Career
Section identifier; they remain generic rather than minting `jobdetail.ftl` as a fake Board. The
other 49 host buckets expand to 146 canonical Boards because one host may serve several Career
Sections or TBE `org`/`cws` sites: 53 Enterprise and 93 Business Edition. Compared by canonical
`board_key()` against every liveness-ledger row, 120 were already known and 26 were unledgered
candidates (11 Enterprise, 15 Business Edition). These remain candidates until the normal
verification/liveness workflow lands them; the replay made no ledger changes.

## Deep channels and bounds

`--deep` adds live TLS SAN discovery, schema-checked Phenom/Zwayam/Eightfold listing probes, and
passive browser rendering after ordinary evidence fails. Certificates yield at most 100 SAN seeds;
only four same-company career names may be followed automatically, and never across a known
provider namespace. Other SANs stay separate from the employer attribution.

Browser work uses a fresh context, at most two concurrent browsers, 60 requests, GET/HEAD only,
12-second navigation, and no clicks, login, or challenge solving. It selects a successful supplied
apply destination before generic career paths and excludes failed pages. API probes use that
successful destination host too. POST concurrency is capped per origin, with a throttle stop for
shared endpoints. Certificate trust uses certifi roots; domains use tldextract's packaged PSL with
private suffixes and no startup download.

## Running it

```sh
uv sync --extra discovery
# If no system Chrome or Playwright Chromium is installed:
uv run --extra discovery playwright install chromium

uv run --extra discovery python scripts/discover/fingerprint_careers.py indeed \
  /absolute/path/indeed_jobs_v3.jsonl /absolute/path/results.csv --deep

uv run --extra discovery python scripts/discover/fingerprint_careers.py verify \
  /absolute/path/results.csv --harvest /absolute/path/indeed_jobs_v3.jsonl \
  --recheck-known --out /absolute/path/verified.csv

uv run --extra discovery python scripts/discover/fingerprint_careers.py certificates \
  /absolute/path/known-career-hosts.csv /absolute/path/certificate-seeds.csv
```

Use a new output path for the expanded schema. Without `--harvest`, same-job verification uses the
stored sample apply URLs. `matched-job` is stronger than a live board or a recognizable JSON schema;
`no-match-in-bounded-sample` can simply mean the job expired or lies beyond the sample. Job checking
for other ATSes explicitly reports `not-implemented-for-provider`.

## Validation and retained evidence

Independent critique iterations scored 4.0, 6.5, 6.0, 7.3, 8.5; the new deep extension scored 8.2
before destination/gating corrections and **8.6 after corrections and live same-job evidence**.
Scores reflect method quality, not recall. The final small Workday site-namespace tightening has
a regression test. The fingerprinting, Board identity, and existing Wayback tests total **115
passing tests**; Ruff and whitespace checks pass.

Local run-owned evidence is intentionally retained under `experiment/fingerprint-validation/`
(gitignored), including `unknown-inputs.json`, `unknown-results.csv`, `unknown-deep-v2-results.csv`,
`unknown-deep-v2-summary.json`, and `zwayam-job-evidence.csv`. Input manifest SHA-256:
`5ca191e5e20c03bb36e1c9237a0af8485eeb024df53c18248eb66c09e9f914b7`.
The local `.venv` is retained for reproducing the checks. No measurement process is intended to
continue running; the pre-existing main-checkout harvest was not a run-owned process.

Remaining gaps: iCIMS has no repository liveness probe in this checkout; some custom/blocked portals
remain unresolved; rendered browsing and certificate rosters are not complete censuses; inferred
names still need affiliation proof; verified candidates still require alias reconciliation and the
normal liveness landing workflow.
