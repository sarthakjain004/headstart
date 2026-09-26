# ADR-0242: Empty Tail Boards back off, and slow Boards start first in their shard

**Status:** accepted · **Date:** 2026-09-26 · **Amends:**
[ADR-0229](0229-the-slice-reads-every-tech-yielding-board-and-rotates-the-rest.md) (the Tail's order),
[ADR-0064](0064-a-boards-hour-must-buy-tech-jobs.md) (its floor for a
measured-empty Board)

## Context

A review of the seven runs of 2026-09-25/26 (36200233818 to 36218633315) found these planner
costs, all visible in each run's shard `board_cost.csv`:

* **Empty Tail Boards are re-read every rotation.** On run 36218633315, 16,910 unscored Boards in
  the Tail returned 0 postings on a complete scrape after a ledger row that already said 0: 814
  serial minutes, 16% of the run's scrape Board-time. ADP alone was 2,209 Boards and 625 minutes.
  The rotation reaches every unscored Board every ~3 runs, so the same empty Boards were paid for
  several times a day.
* **An empty look almost never turns around within hours.** Across the seven runs, 52,890
  complete empty looks were followed by another complete look; 7 of those found postings.
* **Slow zero-score Boards start last.** A shard submitted its Boards priority-desc, so every
  unscored Board sat at the end. `jibe:commonspirit` started at t=1,181 s, ran 515 s and ended the
  scrape stage 339 s after every other Board.
* **The value gate never judged it.** `seconds <= 600` skipped the Board before the zero-jobs veto
  of ADR-0145 ran.

## Decision

1. **Tail back-off.** In the Tail's rotation, a Board whose last complete scrape found nothing
   competes as if it had been looked at 24 h later than it was (`scrape_plan._tail_stamps`). It is
   read about once a day instead of once per rotation. It still comes round, because the stamp is
   only shifted; nothing is skipped outright. The head never reads these stamps, so Scored Boards
   are unaffected. No new ledger column: the cost ledger's `jobs` already means "the last
   complete scrape" (ADR-0145), and the 7-in-52,890 turnaround rate is why one empty look is
   enough.
2. **Slow Boards first.** Within a shard, Boards measured over 60 s start first, slowest first.
   The rest keep priority order. Shards now finish in ~21 of their 75 minutes, so the time-box
   that priority order protects is not what is at stake.
3. **A measured-empty Board meets the gate from 2 min.** Its yield is known to be zero, so only
   its seconds matter. Each gated Board is also logged on a line of its own, because the
   warning's sample stops at ten and ADR-0064 wants every one named.

## Consequences

* **The Slice stays 80,000 Boards, so the freed Tail slots are refilled.** They go to unscored
  Boards that do have postings. Replaying run 36218633315's plan on the state it read, the
  empty Boards' time in the Slice falls from 830 to 0 serial minutes on the first run. In steady
  state it should be ~96 minutes: ~53,400 empty Boards once a day is ~2,050 a run at ~26 runs a
  day. But the Boards that take their slots cost 4.8 s each, not 2.8 s, so total serial work rises
  from 4,983 to 5,579 minutes. Predicted makespan goes from 21.0 to 23.5 minutes, inside
  ADR-0229's ~25-minute target. The unscored Boards with postings now come round every ~1.6 runs
  instead of every ~3.
* The alternative was to shrink the Tail by the Boards backed off, which saves the time instead
  of re-spending it. That is a smaller `max_boards` (`pipeline.yml`), not planner logic: holding
  the old serial total takes about 72,500. It stays available as a knob, not a code change.
* An empty Board that starts hiring is found up to ~a day later than before. A Board that starts
  hiring tech enters the head once it is scored.
* A slow Board that is gated no longer reaches the back-off; the gate's 14-day re-check applies to
  it instead.

## Amendment (2026-09-26): the ledgers and the coverage verdict

The same review found three places where the join's own bookkeeping misread these Boards.

* **The priority ledger now reads `boards_ok` and the unauthoritative list.** Before, a Board that
  scraped clean with zero jobs wrote no line, so its score was never decayed and was carried
  forever. Rows for Boards off the Scrapable set were never read again, so they were carried too.
  On 2026-09-26 those carried rows were 4,960, holding 17.9% of the ledger's tech credit. Parked
  and dead Boards (`recruitee:rebootmonkey`, `oracle:jpmc-test`) sat in the top ten every run. Now:
  * A clean-empty Board decays.
  * A Board whose scrape was truncated or raised (ADR-0053) is carried unblended.
  * A row whose Board is not Scrapable (case-folded match) is dropped. Against the live ledger
    that is 2,826 of 48,499 rows, and none of them was scraped on the last run, so no row churns.
    A missing liveness dir drops nothing.
* **More failures count as "gone".** An unresolvable host and Jobvite's `?invalid=1` redirect now
  count, alongside 404/410. Both were probed on 2026-09-26: 4 of 5 unresolvable hosts were NXDOMAIN
  on 8.8.8.8, and the fifth had no A record; 2 controls resolved. The invalid Jobvite company 302s
  to `invalid=1`, and a live one answers 200. One resolver hiccup is one strike of the 20 that
  quarantine needs.
  A Board quarantined on this evidence and re-confirmed on parole has its served rows pruned
  (ADR-0206), so an unresolvable host can now remove rows. At 20 consecutive strikes, that trade
  is taken.
* **The coverage verdict leaves Boards that answered 404/410 out of its unusable share.** They are
  the failures ledger's to quarantine. It does not leave out the wider gone class: were
  unresolvable hosts excluded, a shard whose resolver broke would read healthy. At the 80k Slice, dead Tail Boards alone put 36 of 105 shard
  reports over 2%. Without them, the worst of the 105 is 1.09% and the median 0.24%. The 2%
  threshold therefore stands, with ~2x headroom again.
* **The held re-fetch rotation (ADR-0211) counts only served Jobs, and now includes Tesla.** The
  store keeps an evicted Job's text, and 4,652 of 4,896 due ids were evicted. Tesla skipped held
  details but was never rotated; it has 1,742 served Jobs, ~10 due a run. Jobs the index does not
  serve (non-English, or waiting to embed) are no longer re-fetched. That matters once
  multilingual retrieval lands.
* **The embed planner's cost table was re-measured, and its per-shard target fell from 300 to
  165 s** (`docs/AI_Integration/embedding-throughput.md`). The measured rates are ~0.55 of the
  old table, so the old target would have halved the fan-out on the same work.
