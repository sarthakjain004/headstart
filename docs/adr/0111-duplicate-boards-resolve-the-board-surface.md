# ADR-0111: A duplicate Board is found by resolving its Board surface, not by comparing its key

**Status:** accepted · **Date:** 2026-09-07 · **Amends:**
[ADR-0023](0023-prune-stale-and-duplicate-index-rows.md) (whose `_dedupe_boards` collapses
*syntactic* duplicates only) · **Relates to:**
[ADR-0012](0012-liveness-ledger.md) (the ledger this one sits beside),
[ADR-0034](0034-nonprod-boards-dead-by-convention.md) (the pre-probe skip shape reused here)

## Context

One company's Board can be live in the ledger under two hostnames. Both are scraped, both are
indexed under different `board_key`s, and the same posting is served twice.

`config._dedupe_boards` already collapses duplicates — but only those that share a canonical
`board_key()` modulo casing or URL form. That is a **syntactic** test. `basf.jobs` and
`basf-se.jobs2web.com` are different strings by every measure available to it, so it cannot see
them. Catching these needs evidence from outside the key.

Until now that evidence was gathered by hand and frozen into code: five SuccessFactors aliases in
`config.EXCLUDED_BOARDS` (#212, #225) and six Eightfold ones in
`check_liveness._EIGHTFOLD_ALIAS_LOSERS` (#154, #157) — the latter carrying its own TODO to
"regenerate, don't hand-maintain".

### What the ledger actually holds

All 2,204 live SuccessFactors Boards were probed on 2026-09-06 (one GET each, redirects followed,
final host recorded; `scripts/validate/dedupe_boards.py` reproduces it). 26 were unreachable, 2,140
resolved to themselves, and 38 resolved somewhere else — and those 38 are **not one population but
four**:

| class | n | resolves to | verdict |
| --- | --- | --- | --- |
| **duplicate** | 23 | another live ledger Board | bury the duplicate |
| **www-variant** | 7 | its own `www.` form, absent from the ledger | reported only |
| **migrated** | 6 | a host absent from the ledger | reported only |
| **tombstone** | 2 | `sap.com/products/hcm/recruiting-software.html` | reported only |

An independent scan two hours earlier, resolving each Board's *root* rather than its Board
surface, found the same 23 duplicates in the same 22 clusters — and differed on the tail (10
migrated, 30 unreachable). The duplicate verdict is the stable part; the residue moves with the
weather, which is why only duplicates are acted on.

Clustered, the 23 duplicates form **22 clusters covering 45 live Boards**, so 23 Boards are
redundant — 1.0% of the live SuccessFactors ledger, carrying **8,221** advertised postings on the
redundant side (summing the committed alias ledger against the liveness ledger's own counts, which
are themselves stale by up to 1.7x within a single cluster — so treat it as the size of the prize,
not a row count). Eightfold's comparable pass measured ~10,240 duplicate rows, 9.5% of that ATS.

Four measurements decided the design.

**Redirect direction does not name the canonical.** BASF points its SAP-hosted name at its vanity
one (`basf-se.jobs2web.com` → `basf.jobs`); Colas points its vanity name at its SAP-hosted one
(`careers.colasjobs.com` → `colas.jobs.hr.cloud.sap`). Both directions occur.

**Clusters are not always pairs.** Paramount is three — `careers.paramount.com` ←
`cbscorporation.jobs`, `viacomcbs.careers`.

**The resolved host alone over-merges.** `careers.toagroup.com` and `jobs.bhs-world.com` both land
on SAP's marketing page. They are two unrelated decommissioned tenants, not each other's
duplicate; grouping on the target without asking whether the target *is a Board* merges two
companies.

**No single signal covers every ATS.** Eightfold's six known aliases do not redirect at all:
`nvidia.eightfold.ai/careers` serves itself and never points at `jobs.nvidia.com`. The signal that
finds 23 of 23 SuccessFactors duplicates finds 0 of 6 Eightfold ones.

## Decision

Resolve each live Board's **Board surface** — the URL the scraper actually fetches, `url()` — and
group the ledger by the host it lands on.

- **The signal is a scraper method.** `BaseScraper.alias_key()` returns the final host after
  redirects; Eightfold will override it to return its tenant id. This mirrors `board_key()` and
  `slug_from()`, which are defaulted in `base.py` and overridden where an ATS disagrees, so the
  seam is the one the codebase already uses for per-ATS identity. It is built now with one
  adapter because the second is measured and deferred, not hypothetical.
- **Grouping is a plain group-by, not union-find.** The general entity-resolution answer to
  transitive matches is a connected-components pass, and it is not needed here: the HTTP client
  follows the whole redirect chain, so `A → B → C` already yields `alias_key(A) = C`. The
  transitive step happens in the transport. A future *pairwise* signal (id-set overlap between
  arbitrary Boards) would bring union-find back; nothing shipping today is pairwise.
- **A group is a duplicate cluster only when its key is itself a live Board.** That condition is
  what stops the `sap.com` over-merge. A second one follows it: the key must also be *among the
  probed members*, or the canonical is live but this scan never reached it, and the only members
  present are the ones pointing away. That case is reported as `canonical-unconfirmed` — a fifth
  outcome beyond the four in the table above, which describes a completed scan.
- **There is no survivorship election.** The canonical *is* the resolved key: the site owner has
  already declared which name is real, and the cluster-formation guard above requires that key to
  be a member, so exactly one member always qualifies. A ladder of tie-breaks was designed first —
  redirect-target, then non-vendor host, then lexicographic — and measuring it against the 22 real
  clusters killed two thirds of it. The first rule decided all 22, and the non-vendor rule was not
  merely dead but *wrong when it fired*: Colas and CONA both have a vendor host as their true
  canonical, so preferring the branded domain would have buried the real Board and kept a
  redirect. A `--prefer` file overrides any cluster by hand.
- **Nothing is verified by content.** For a redirect-formed cluster the redirect *is* the
  evidence: following the duplicate's surface lands on the canonical, so a job-id comparison
  would confirm what the transport already guaranteed. Content verification is required for a
  signal whose members are independently served — Eightfold's — and belongs with that signal
  when it lands, not here.
- **Duplicates live in their own ledger**, `data/validate/aliases/{ats}.csv`, committed beside
  the liveness ledger. `config.load_active_companies` drops them as it reads each ledger, beside
  `EXCLUDED_BOARDS`, because both are keyed on the slug;
  `check_liveness` drops them from its work list before spending HTTP, leaving their liveness row
  untouched rather than writing a verdict onto it.

### Why not mark the duplicate `dead`

Because it is not dead. A buried duplicate answers 200 and serves a full board, which is why
`dedupe_eightfold_aliases.py` marking losers `dead` did not survive contact with the 90-day
dead-TTL re-probe and had to be propped up by a frozen skip-set inside the prober (#157).

Liveness answers "does this host serve a Board?"; aliasing answers "and is it the same Board as
that one?". They are different questions with different re-probe cadences, and overloading one
status to carry both is what forced the hand-maintained set this ADR removes. Keeping them apart
lets the liveness row stay true.

## Consequences

- 23 SuccessFactors Boards leave the scrape list — measured, **Scrapable Board** falls 85,634 ->
  85,611 and **Hiring Board** 53,838 -> 53,815 — and `index prune`'s existing off-Board path
  evicts their rows, so there is no new eviction machinery. (Against the previously *documented*
  figures the net is -20, not -23: three hand-listed `EXCLUDED_BOARDS` entries were retired in the
  same change and are counted below.)
- Three of the five SuccessFactors entries in `EXCLUDED_BOARDS` (CONA, HCLTech, Bombardier —
  #212, #218) were the same three duplicates by hand, and are removed: the ledger now carries
  that fact alone rather than the repo asserting it twice. The other two stay hand-listed, and the
  reason each does is the point: Capgemini's is a *test* board, not a duplicate, and
  LTIMindtree's TLS cert is permanently broken, so `alias_key` can never reach it to read what its
  redirect says. A scan cannot supersede an entry it is structurally unable to verify.
  `_EIGHTFOLD_ALIAS_LOSERS` stays until Eightfold migrates; its TODO now names this framework.
- **Run manually, not on a schedule.** ~2,200 cheap GETs per ATS. Re-run after discovery adds
  Boards, and re-verify rather than trusting a prior run's verdicts — a cluster that has since
  diverged must be reported, never assumed.
- **Migrated, www-variant and tombstone Boards are reported and left alone**, deliberately. Each
  needs a different action (seed the target / normalise the ledger / mark dead) and each is a
  liveness change rather than a dedupe one. The scan surfaces them so the decision is informed;
  it does not take it. Two consequences to keep in view: those 6 migrated Boards are scraped
  every run for content that has moved, and the 2 tombstones are scraped for nothing at all.
- A Board can be buried on evidence that later reverses. The ledger records the signal and the
  host each Board resolved *to* — the destination, not the route that reached it — and the script
  re-derives every verdict live, so a reversal shows up as a changed row rather than as a Board
  that quietly never comes back.
