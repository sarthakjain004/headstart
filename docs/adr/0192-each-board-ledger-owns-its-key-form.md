# ADR-0192: Each per-Board ledger owns the form its Boards are looked up in

**Status:** accepted · **Date:** 2026-09-24 · **Builds on:**
[ADR-0191](0191-one-module-answers-whether-a-board-is-scraped.md) (a `ScrapableBoard` carries its
identity in both casings), [ADR-0096](0096-one-key-for-both-board-ledgers.md) (one key for the
cost and priority ledgers, case preserved), [ADR-0049](0049-match-boards-by-prefix-not-by-parsing.md)
(a ledger's casing and a fresh `board_key()` need not agree),
[ADR-0155](0155-one-module-for-board-identity-two-failure-policies-by-name.md) (strict and lenient
identity)

## Context

Four per-Board ledgers are read by the scrape planner, and they disagree about case:

| Ledger | Stored keys | Planner compares |
|---|---|---|
| `board_cost` | verbatim `board_identity` | verbatim |
| `board_priority` | verbatim `board_of(job id)` | verbatim |
| `board_description_gap` | lowercased on load and save | lowercased |
| `ingest.board_failures` | verbatim `board_key_of(report key)` | lowercased, at the call site |

Only the gap ledger said so in code, through its `key_for`. For the other three every caller had to
remember which form to use: `scrape_plan` lowercased the quarantine set and the Board
(`lower_key(board_identity(c))`), passed the raw identity to the value gate, and `pick_boards`
looked scores up verbatim beside a gap lookup that folded. Getting it wrong fails silently, as a
lookup that misses.

## Decision

1. **Every per-Board ledger module exposes `key_for(board)`**, the form its rows are compared in.
   It takes a `ScrapableBoard` or a key already in hand (a `board_of` result, a stored row).
   `board_cost` and `board_priority` return the identity verbatim, and a key passes through.
   `board_description_gap` and `board_failures` return it lowercased. Callers look a Board up
   through the ledger's `key_for` and no longer call `lower_key` or choose between `identity` and
   `lowercase_identity` themselves. The gap ledger's existing `key_for` was the pattern, and the
   deletion test kept it: removing it would put the folding back into `pick_boards`,
   `scrape_plan` and `update_ledgers gap`.
2. **Nothing on disk changes.** `load` and `save` of all four read and write exactly what they did.
   The gap ledger's own `load` and `save` now fold through its `key_for`, which is the same
   `lower_key` call.
3. **The cost and priority ledgers stay verbatim, because the casing is load-bearing.** Measured
   against `data/state/*` pulled from HF on 2026-09-24:
   - A folded priority lookup would score **102 Scrapable Boards** (72 Workday, 30 SmartRecruiters)
     that the verbatim lookup misses. Their rows are spelled in a casing the Board's
     ADR-0023 survivor no longer emits, such as `smartrecruiters:AntaresTech1` for
     `smartrecruiters:antarestech1`, and they carry 141.7 points of score. Folding would move those
     Boards into the priority head and change the slice.
   - The cost ledger holds **1,956 groups of case-variant keys**. A folded load would merge each
     group into one row and change the seconds the packer reads. For the Scrapable Boards
     themselves, a folded lookup finds nothing the verbatim one misses (137,641 hits either way).

   ADR-0096 already recorded that case is preserved in these two files. This change leaves that
   policy where it was, and now each ledger states it.
4. **`board_failures` stays verbatim on disk and folds in `key_for`.** `update` pairs its rows
   verbatim with the `board_of` keys of the same run, which is the ADR-0155 strict path. Only the
   planner's quarantine test folds (ADR-0049). Of its 1,018 rows, 6 are not lowercase, and none
   are case variants of each other.

## Rejected: one identity per Board in `harvest`

`harvest.scrape_all` keys resume, errors, truncations and the `on_board` callback by the shard's
`{ats}:{slug}` working key, and keys only the cost row by `board_identity`. The join and
`update_ledgers failures` convert the working key back with `board_key_of`. Having the Board carry
one key does not make this redundant, for two reasons:

- **Different jobs.** Resume answers "did I already fetch this URL" and cost answers "what does
  this Board cost", and ADR-0096 keeps them apart on purpose: a Workday tenant on two pods is one
  Board and two fetches.
- **Different failure policies.** `board_key_of` is ADR-0155's strict form, which drops a key that
  will not parse. Reusing the lenient identity would give the failure ledger and the
  unauthoritative list a synthetic key that no scrape ever produces.

The shard files also carry plain `{ats, slug, name}` for the same reason, so `scrape_run` still
reads `CompanyRef`s.

## Deferred

The 102 priority rows that only a folded lookup reaches are a real miss: those Boards have earned
a score and compete as unscored. The fix is a behaviour change, and this ADR is scoped to changing
none, so it is left for its own decision. Two routes are open: fold the priority lookup (but not
the stored key), or rewrite the rows under the survivor's casing.

## Verification

`scrape_plan` was run at ADR-0191's tip and at this change over the same HF state, with the shuffle
seeded. The two runs produced the same ordered slice (20,000 Boards), the same Boards removed by
quarantine, the same value-gate verdicts, identical shard files and `plan.json`, and identical log
lines.

## Consequences

- A new per-Board ledger declares its key form in a `key_for`. Its callers use that function and
  never pick a casing themselves.
- Any change to a ledger's key form now happens in one function, and this ADR's measurements are
  the baseline to re-measure against.
