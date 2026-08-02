# ADR-0034: Non-production boards are dead by convention

- Status: Accepted
- Date: 2026-08-02
- Extends the liveness verdicts of [ADR-0023](0023-prune-stale-and-duplicate-index-rows.md)'s
  keep-set source (`data/validate/liveness/`, git-authoritative) with a pre-probe rule in
  `scripts/validate/check_liveness.py`. Nothing downstream changes: `index prune` already evicts
  rows on non-live Boards.

## Context

ATS vendors give customers sandbox/UAT/demo tenants, and those tenants serve **fabricated data at
production-looking scale**. Measured: eightfold's `amdocs-sandbox` claims 2,099 jobs where the real
`amdocs.eightfold.ai` has 5; `citigroup-qa-sandbox` claims 3,193; ripplehire's `hdfcbank-uat` was
sighted at 28,886 against production's 173. Probing cannot tell these from real Boards — they
answer 200 with plausible counts — so three separate discovery agents each hit the problem and
each proposed an ad-hoc call. The 2026-07-28 handoff explicitly parked it: "needs a shared
convention, not three ad-hoc calls."

It stopped being theoretical when a user searching with `max_years=1` received an
`amdocs-sandbox` job. A sweep of every ledger found **48 live non-prod Boards claiming 18,016 fabricated jobs** (47 token-matched + `stldemo` via the blocklist) across ashby, eightfold, lever, ripplehire, smartrecruiters, successfactors and
teamtailor.

## Decision

**The host name is the signal, and it is checked before any probe is spent.** `sandbox`, `uat` or
`demo` appearing as a *delimited token* in the tenant or url marks the Board dead in
`check_liveness` — no HTTP, no verdict ambiguity, re-asserted free of charge every time the dead
TTL expires. Token-bounding is load-bearing: one-word names like `sandboxvr` or `thesandbox` must
not match.

**An exact-tenant blocklist carries the concatenated cases the tokens cannot see.** `stldemo`
(ripplehire — STL's demo instance, 137 mirrored jobs) surfaced in served results during this
change: the marker is fused into the name, and widening the regex to catch it would re-admit the
`sandboxvr`/`thesandbox` false positives. Named tenants go on `_NONPROD_TENANTS` instead, with the
evidence in a comment.

**An exact-tenant exception list carries the real-company collisions.** Eyeballing every ledger
match before landing the convention found two: `sandbox-interactive-gmbh` (Sandbox Interactive
GmbH, the Albion Online studio) and `demo-duck` (Demo Duck, a video agency). The exception is
exact-tenant, not a pattern — a listed company can still have a nonprod board under another name.
Note the inverse case is *not* an exception: teamtailor's `20min-demo` and `theindiestone-demo`
belong to real companies but are demo *board instances* — the convention excludes Boards, not
companies, so they correctly die.

**The 48 already-ledgered rows were flipped live→dead in the same change**, so the next
`index prune` sweeps their rows out of the served table rather than waiting up to 7 days for
their live TTL to lapse.

## Alternatives considered

- **Job-count sanity checks against the production tenant** (flag `X-sandbox` when `X` exists and
  is much smaller). Catches only the paired cases, needs the pair to be discovered, and 28,886 vs
  173 shows the fabricated side can dwarf the real one — the comparison direction isn't even
  stable.
- **Content heuristics** ("Test job…" titles). The amdocs sandbox serves *real-looking* mirrored
  postings; heuristics on content are exactly the ad-hoc calls the handoff rejected.
- **Excluding at scrape or index time instead of liveness.** Liveness is the single
  source of truth every stage already keys on (Active list, prune keep-set) — excluding anywhere
  else would leave the Board "live" in the ledger while secretly skipped, the same
  lie-in-the-ledger shape ADR-0030 exists to prevent.

## Consequences

~18k fabricated jobs leave the corpus as their rows are pruned; ledger totals drop by 48 live
Boards (README refreshed). New non-prod tenants entering discovery die at first probe for free.
The residual risk is a company whose *name* collides with a token and isn't in the exception list
yet — the failure mode is a wrongly-dead Board with a 90-day TTL, visible in the ledger diff, and
the fix is a one-line exception. The tokens are deliberately only the three with measured sightings;
`test`/`staging`/`preview` wait for evidence before earning a slot.
