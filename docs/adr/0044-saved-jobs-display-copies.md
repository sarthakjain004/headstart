# ADR-0044: Saved jobs are per-record display copies, keyed by the job id

**Status:** accepted · **Date:** 2026-08-12

## Context

ADR-0042 decided what a Saved job *is*: starring keeps a copy of the display fields
alongside the job id, so the Saved tab survives Eviction and can mark a closed posting
"closed"; stars flip optimistically because an HF write is ~1s. This ADR decides the record
and endpoint shape, because the sets precedents (ADR-0043) don't answer everything.

What's different from sets: a Saved job has a natural identity (the served table's
`id = {ats}:{slug}:{native_id}`) where a set has none; its list is read as one page but
written one click at a time, often in bursts; and its content is a *copy of scraped data
the browser sends back*, not something the user typed.

## Decision

**One file per star — `saved/{account}/{record_id}.json` — with the record id derived from
the job id** (`sha256(job_id)[:16]`, the same address-hiding shape as `subscription_id`).
Deriving rather than minting makes starring idempotent by construction: starring an
already-starred job overwrites its record (refreshing the copy and the star time), and two
tabs starring the same job converge on one file. Per-record files extend ADR-0035's
disjoint-writes argument to the real concurrency here — someone starring three jobs in
quick succession is three concurrent writers, and a single list file would lose stars to
read-modify-write races.

**The copy is taken at star time and bounded at the door.** Fields: title, company, url,
location, remote, salary, plus `starred_at` and the `job_id` — location and remote added
deliberately beyond ADR-0042's illustrative list, because the Saved card renders only the
copy, and a saved list you can't scan for where-it-is defeats its purpose. The values
arrive from the browser (they are what its card showed), so each is length-capped in
`SavedJob.create` and the URL scheme is re-checked at render time; nothing else in the body
is trusted or stored. `MAX_SAVED = 100` per Account bounds abuse — checked against a
listing, not a full read, and a re-star is always allowed at the cap.

**"Closed" is an annotation computed at read time, not stored.** `GET /saved` asks the
index which of the listed job ids still exist (one `id IN (...)` query through
`JobSearch.indexed`, quotes escaped like every filter term) and stamps each record
`open: true/false`. Storing closedness would go stale in both directions; computing it
keeps the record immutable after star time.

**Reads ride a small thread pool.** The Saved tab loads every record; up to `MAX_SAVED`
serial HF reads would hold the tab for many seconds, so `Store.saved_for` fetches with
`ThreadPoolExecutor(8)`. Sets don't need this (`MAX_SETS = 10`); saved jobs do.

**Races accepted:** re-starring from two tabs is last-write-wins on one record — the same
job, the same copy, no loss that matters. Unstarring a job another tab already unstarred
answers 404, which the UI treats as the same outcome.

## Options rejected

- **One list file per account:** one read to load the tab, but every star is a
  read-modify-write; burst-starring (the normal gesture) would need client-side write
  serialization and still lose cross-tab races. The tab-load cost is paid with parallel
  reads instead.
- **Id-only pointers resolved against the index:** already rejected in ADR-0042 — at the
  measured churn (one run: −4,673 rows), stars would silently vanish within days.
- **Random record ids (the sets shape):** loses idempotent re-star; the client would need
  the record id before it could unstar, and a double-click would mint duplicates.
