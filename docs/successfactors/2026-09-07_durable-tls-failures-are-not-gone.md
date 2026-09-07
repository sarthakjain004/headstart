# The four TLS-broken SuccessFactors Boards are alive, not gone — measured, 2026-09-07

No code change yet; this records what the four boards actually are, because the fix the five-run
review proposed for them would have written off live coverage. It also names a blind spot in
ADR-0111's alias dedupe that these boards expose.

## What was proposed

`docs/pipeline/2026-09-07_five-run-log-review.md` §3b ranked it fifth:

> Let a durable `CertificateVerifyError` count as a gone-strike … They will be retried forever,
> cheaply (~6 s each) but pointlessly.

The premise checks out. All four still fail TLS verification today, with the same errors the runs
report, so "durable" is right — an expired certificate does not heal on retry.

## What the boards actually are

The conclusion does not follow, because **a board that fails TLS verification is not a board that is
gone**. Re-probed 2026-09-07, verified handshake first, then again with verification disabled purely
to establish whether anything is there:

| Board | verified | unverified | what it is |
|---|---|---|---|
| `jobs.kkg.ch` | cert name mismatch | 301 → `kkg.jobs.hr.cloud.sap` | **duplicate** of a Board already live in the ledger |
| `karriere-solingen.de` | cert expired | 301 → `www.karriere-solingen.de` | **recoverable** — the target has a valid cert |
| `careers.shapoorji.com` | cert expired | 200 | alive, no valid-cert route found |
| `jobs.newway.eu` | cert expired | 200 | alive, no valid-cert route found |

All four serve a real SuccessFactors RMK `urlset` sitemap. Job URLs in each: 13
(`www.karriere-solingen.de`), 11 (`kkg.jobs.hr.cloud.sap`), 9 (`careers.shapoorji.com`), 2
(`jobs.newway.eu`).

So a gone-strike would have recorded a false fact about four live Boards, and would have buried
rather than surfaced the lost coverage the review wanted surfaced. `careers.shapoorji.com` is a real
Indian employer.

Two of the four need no policy decision at all:

- **`jobs.kkg.ch` is a duplicate.** `kkg.jobs.hr.cloud.sap` is *already* its own `live` row in
  `data/validate/liveness/successfactors.csv` (14 jobs, checked 2026-07-27). This is the
  cross-hostname redirect class CLAUDE.md's ledger-duplicates note describes, and the ledger is
  paying to scrape one Board twice.
- **`karriere-solingen.de` is a re-point**, not a duplicate: `www.karriere-solingen.de` is not in the
  ledger at all. The bare host is the only row, and it is the broken one.

## The blind spot this exposes

ADR-0111's `dedupe_boards.py` exists to catch exactly the `jobs.kkg.ch` case — its signal is
`alias_key()`, "the host its Board surface resolves to after redirects". It did not catch it, and
the reason is structural rather than a bug:

```
jobs.kkg.ch                  alias_key -> None
kkg.jobs.hr.cloud.sap        alias_key -> 'kkg.jobs.hr.cloud.sap'
karriere-solingen.de         alias_key -> None
careers.shapoorji.com        alias_key -> None
```

**Resolving an alias requires completing the TLS handshake the board fails**, so `alias_key()`
returns `None` for every broken-cert board and the dedupe cannot see the pair. Confirmed against the
live ledger, not inferred: `data/validate/aliases/successfactors.csv` was regenerated 2026-09-06 and
holds 23 rows, all with signal `redirect`; none of these four appear.

The class is therefore self-concealing — the boards most likely to be stale duplicates are the ones
the duplicate-finder cannot read. Hand-adding a row does not fix it: `dedupe_boards.py` re-derives
every verdict live on each invocation and trusts nothing from a prior run, so a hand-written row is
erased by the next run.

## What this leaves — a decision, not a default

The cost of doing nothing is small and known: 4 boards × ~6 s = ~24 s per run, and 35 postings not
served. The options are genuinely different in kind, so none is being taken silently:

1. **Resolve aliases with verification disabled, for alias resolution only.** Fixes `jobs.kkg.ch`
   and `karriere-solingen.de` automatically and generalises to every future broken-cert board. It
   means trusting a redirect from a host we cannot authenticate — the redirect target is then
   attacker-chosen if the host is ever impersonated. Cheap and general; a real, if small, trust
   concession.
2. **Scrape these hosts with verification disabled.** Recovers all 35 postings including the two
   with no valid-cert route. The largest trust concession, and the one that keeps working when a
   cert expires anywhere else.
3. **Surface them as their own class and fix nothing automatically.** Give the failures ledger a
   durable-TLS bucket so these stop hiding inside "error(s) did not read as gone", and correct the
   two known Boards by hand. Honest, no trust concession, but does not generalise.
4. **Do nothing.** ~24 s per run and 35 postings, and the class stays invisible.

Option 3 plus a manual correction is the smallest honest step and the one this writeup recommends;
option 1 is the one worth discussing, because the blind spot it closes will keep recurring.
**A gone-strike is not among the options** — the boards are not gone, and the review item that
proposed it is withdrawn on that ground.
