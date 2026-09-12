# ADR-0109: Hot Application state needs a transactional store, not the HF repo

**Status:** proposed · **Date:** 2026-09-03 · **Amends ADR-0105 §3's claim mechanism; the store itself is an owner decision (options below)**

## Context

ADR-0105 first made the Application claim a `create_commit(parent_commit=HEAD)` on the private
subscribers dataset — real compare-and-swap, verified in `huggingface_hub` 1.21. The third review
pointed at what that buys and what it does not.

- **The HEAD is one global lock.** Every Account's every transition contends on the same repo
  head; "serial per Account" does nothing for repo-wide contention. Losers re-fetch and retry.
  Tolerable at tiny scale, and the wrong shape to grow.
- **Heartbeats can never be commits.** A 10 s lease renewal per active run written as a Git commit
  would be absurd; and even transitions-only means five to ten commits per Application.
- **Git remembers.** Submission snapshots — question text plus the value actually sent, including
  salary, visa status, demographic answers when the Account chose to give them — would sit in
  repository history behind any later "delete". `HfApi.super_squash_history` exists (verified,
  1.21) and can make forgetting real, but only by squashing the whole branch on a schedule; it is a
  janitor, not a delete.
- **The scheduler needs an atomic account-level condition** ("no active Application for this
  Account → grant the slot"), which is a transaction, not a file write.

## Decision

1. **Split hot execution state from the durable record.** Hot: Application state, Attempts,
   `run_id`, lease, execution slot, Queued order. Durable: Answers with provenance, submission
   snapshots, the audit trail. Hot state needs a store with transactions and a unique constraint on
   (Account, Job); heartbeats are never durable writes anywhere (the lease is a timestamp updated
   in place).
2. **The claim becomes `INSERT … ON CONFLICT DO NOTHING` under (Account, Job)**, the slot grant a
   transaction over the Account's active rows, and every transition an update guarded by
   `run_id`. The HF `parent_commit` path stays documented as the fallback if no store is added.
3. **Retention is a written policy, wherever the record lives.** The Account can delete its
   Answers, Attempts and snapshots; deletion is real within a stated window; access is the Account's
   and the operator's; the window for snapshots is stated in the product. If any of this lands on
   HF, `super_squash_history` runs on a schedule so history does not outlive the policy.

## Options for the store — owner's call

| | where | for | against |
|---|---|---|---|
| **A (recommended)** | SQLite (or Postgres) on the existing Oracle box, behind the same SSH tunnel the router uses (`docs/LLM_API.md`) | no new vendor; private by construction, like the router; one file, transactional, deletable; ample at this scale | the tunnel is a single point of failure the product already tolerates — router down degrades the plan, store down makes auto-apply "temporarily off" while search stays up |
| B | a managed free-tier transactional store (Turso / Neon / Supabase) | no tunnel coupling; purpose-built; reachable from the Space directly | a new vendor holding employment-application data; credentials in the Space; a free tier that may not stay free |
| C | keep HF: one document per Application, transitions only, heartbeats in Space memory, nightly `super_squash_history` | nothing new to run | repo-wide contention stays; Space restarts lose in-memory lease tracking mid-run; deletion is a nightly squash, not an act |

**Recommendation: A.** It matches the posture the repo already chose for the router (private box,
tunnel, degrade rather than die), adds no vendor, and gives real transactions and real deletes.
If the owner prefers B, everything above holds unchanged; if C, ADR-0105 §3's CAS text is the
implementation and the contention and squash caveats become accepted risks.

## Consequences

Auto-apply gains a dependency the search product does not have; its failure mode is "auto-apply
temporarily off", never a broken search. Answers move from beside the Profile on HF to the store
(they are read on every plan; the Profile stays where it is). The first Greenhouse experiment
(PLAN §9) needs the store, or the HF fallback, before its crash and two-device cases can run.
