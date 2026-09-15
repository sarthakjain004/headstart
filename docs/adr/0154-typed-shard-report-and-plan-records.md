# ADR-0154: Typed `ShardReport` and `plan.json` records

- **Status:** Accepted
- **Date:** 2026-09-15
- **Amends:** [ADR-0045](0045-per-shard-run-reports.md) (the shard-report shape), touches the
  writers [ADR-0025](0025-parallelize-nightly-pipeline.md)/[ADR-0026](0026-parallelize-nightly-scrape.md)
  introduced and the sizing [ADR-0054](0054-learned-fan-out-speedup.md) reads
- **Related:** [ADR-0028](0028-ingest-package.md) (the `{half}_{role}` layout this change's new
  module fits into), [ADR-0141](0141-scrape-health-travels-to-the-publication-receipt.md)
  (`ScrapeHealth`, one of the four readers this change updates)

## Context

Every scrape shard writes a `_shard_report.json` fragment whose shape was never declared as a
type. `scrape_run._report` assembled it as ~17 hand-written kwargs to
`observability.write_shard(outdir, **fields)`; a 70-line `_safe_shard_report` in `observability.py`
existed purely to coerce that undeclared shape back into something safe to read; and four readers
(`scrape_join`, `shard_speedup.ratios_from_reports`, `update_ledgers.failures`,
`observability.ScrapeHealth.from_reports`) each re-stated the field names independently with
`.get(...) or 0` defensive access. `scrape_run._report` also built a *second*, four-field synthetic
version of the same shape in memory, purely to reuse `ScrapeHealth.from_reports` for its own
mid-run health check — so the on-disk shape was fabricated by hand in two places even within its
own writer.

A related but separate problem: `plan.json` is written by two different modules with two different
shapes. `scrape_plan.py` writes `{shards, count, per_shard_boards, per_shard_minutes?,
per_shard_serial_minutes?}` (the last two fields omitted on a cold start); `embed_plan.py` writes
`{shards, count, makespan_s, per_shard_s}` — a different field set for a conceptually similar
per-stage plan artifact. The one reader that parses fields beyond `shards`/`count`,
`scrape_run._plan_minutes`, reverse-engineered the plan from the shard's own `--assignment` path and
caught six exception types to mean "an older plan format."

Shard-index-from-filename parsing (`Path(assignment).stem.rsplit("-", 1)[-1]`, turning
`shard-3.jsonl` into `"3"`) was duplicated identically three times: `scrape_run._shard_id`,
inline inside `scrape_run._plan_minutes`, and inline inside `embed_run.main`.

## Decision

### One technique, two record families — not one unified type

`ShardReport` (telemetry a scrape shard produces about its own run) and the two `plan.json` shapes
(work a planner assigns *before* any shard runs) both become `@dataclass` records with
`to_json`/`from_json`, but they stay conceptually separate types rather than sharing a base class
or a generic serialization helper:

- **They have no shared consumer.** `ShardReport` has no embed-side counterpart at all — nothing
  in `embed_run`/`embed_merge` writes a report even close to this shape — so a base type spanning
  both would describe a relationship that does not exist in the code.
- **`ScrapePlan` and `EmbedPlan` are packed on different measured units** (Board-seconds from
  `board_cost.csv` vs. per-Bucket Doc-seconds), and only `ScrapePlan`'s fields are ever read back
  structurally (by `scrape_run`, for its predicted/serial minutes). `EmbedPlan` has no reader
  beyond the `shards`/`count` keys pipeline.yml's heredocs already read as plain JSON. Merging the
  two into one shape would need optional fields for whichever half a given plan does not have,
  turning a documented contract back into an undeclared one wearing a dataclass.
- **The tolerance each needs differs enough that a shared serializer would save one line and cost
  a cross-module dependency.** `ShardReport.from_json` does per-field type coercion (str-keyed
  dicts, int-valued dicts, float-or-None) because a corrupt shard report must still contribute its
  valid fields to the join — that is the entire point of ADR-0045's design. `ScrapePlan`/
  `EmbedPlan.from_json` only need whole-record tolerance (missing file, corrupt JSON, an older
  shape without a given key means `None`) because nothing downstream needs a *partially* read
  plan — a shard with no prediction behaves exactly like a shard that could not read one at all.
  A helper generalized to serve both would either lose `ShardReport`'s field-level recovery or
  force `ScrapePlan` to carry machinery it does not need.

So `ShardReport` stays in `observability.py`, which already owned "the shard-report round trip" in
its own module docstring and is already imported by every one of its four readers (only
`shard_speedup.py` gains a new import — the other three already import `observability`).
`ScrapePlan`/`EmbedPlan` move into a new module (below), and each dataclass gets its own
`to_json`/`from_json` — plain, direct, and about eight lines apiece; sharing a generic helper for
that would be the "abstraction for single-use code" CLAUDE.md's simplicity rule warns against.

### A new shared module for the plan shapes and shard identity: `shard_plan.py`

`ScrapePlan`, `EmbedPlan`, and `shard_index` (the filename-parsing consolidation) live in a new
`src/headstart/ingest/shard_plan.py`. This follows ADR-0028's `{half}_{role}` layout directly:
`binpack.py` is already "shared by both planners" and `doc_prep.py` is already "shared by
embed_plan and embed_run"; `shard_plan.py` is the same pattern for the plan/run pair on *both*
halves — `scrape_plan` and `scrape_run` import it for the scrape half, `embed_plan` and `embed_run`
import it for the embed half.

`shard_index` is a plain function, not a method on either Plan type, because a shard learns its own
index from its *own* `--assignment` CLI argument before it has read any plan at all — the index is
what a shard uses to look itself up in a Plan (`ScrapePlan.predicted_minutes(shard)`), not
something a Plan hands out. Attaching it to `ScrapePlan` would also leave `embed_run` — which needs
the index for its log-context tag but never reads a plan back — with no natural place to get it
from.

Checked against its closest namesake, `index_plan.py` (the `jobs`-table add-evict/prune planner,
deliberately import-light so its scoping invariants stay testable on CI's base-deps-only install):
the two share a `_plan` suffix but not a subject — one plans LanceDB row changes, this one is the
`plan.json` artifact shape plus shard identity — and neither imports the other, so there is no
homograph risk of the `index_sync`/`sync_index` kind ADR-0028 warns about. `shard_plan.py` reads
correctly next to `shard_speedup.py` (an existing "a measured fact about the shard fan-out"
module) as the two natural readings of "shard_" as a prefix in this package.

### `pipeline.yml`'s heredocs: left reading raw JSON keys, unchanged

Both inline `python -<<'PY'` heredocs in `pipeline.yml` (`scrape-plan` and `embed-plan` jobs) read
`plan.json` as plain JSON and touch only `plan["count"]` and `plan["shards"]` — the two fields
present, under the same names, in every plan shape old and new. `ScrapePlan.to_json()`/
`EmbedPlan.to_json()` reproduce the exact hand-assembled dict shape (same keys, same insertion
order, same rounding, same `indent=2`/no-`sort_keys` formatting `_write_plan` used), so the
heredocs need no change and were not touched — this repo's other pipeline steps already establish
the pattern of reaching a real entry point (`python -m headstart.ingest.X`) for anything that needs
the package installed, and these two heredocs do only trivial key access, not logic worth promoting
to a module. `pipeline.yml` is **not otherwise touched by this change** — flagged for extra review
care since it is the live nightly pipeline, even though no line in it changed.

`pipeline-smoke.yml` independently hand-writes and hand-reads `_shard_report.json` as raw JSON in
three places (a minimal synthetic report at write time, a read-modify-write of `errors`/
`truncated` across its three eviction-flap cycles). It was not in this change's scope and was not
touched; it continues to work unmodified because the on-disk `ShardReport` shape is unchanged —
verified by re-reading its field usage (`shard`, `assigned`, `done`, `undone`, `jobs`,
`killed_by_budget`, `errors`, `truncated`, all still present under the same names) against
`ShardReport.from_json`'s tolerant parsing, which accepts a JSON object missing every other field.

### On-disk shape: byte-for-byte unchanged

`ShardReport.to_json()` emits `json.dumps(fields, indent=1, sort_keys=True)` over the same field
set `write_shard(outdir, **fields)` used to receive; `ScrapePlan`/`EmbedPlan.to_json()` reproduce
`_write_plan`'s exact dict construction, field order, and rounding. `malformed` — the one field
`ShardReport` carries that the old `dict` shape did not always have — is set only by `from_json`
(when a *read* had to coerce something) and is deliberately excluded from `to_json()`'s output, so
a shard's own write is unaffected and an already-uploaded fragment from a run on the prior code
reads identically through the new `from_json`. `tests/test_shard_plan.py` pins the exact on-disk
JSON for both plan types (including the cold-start shape with the optional fields omitted, and the
rounding behavior), and `tests/test_scrape_plan.py`/`test_embed_plan.py`'s pre-existing
`json.loads(...) == {...}` assertions pass unmodified — the strongest evidence the shape did not
move.

## Consequences

- `observability.py` gains `ShardReport` (`to_json`/`from_json`) and loses `_safe_shard_report`
  (70 lines) and the ad hoc `write_shard(outdir, **fields)` signature, which becomes
  `write_shard(outdir, report: ShardReport)`. `read_shards` now returns `list[ShardReport]`.
- `ScrapeHealth.from_reports` takes `list[ShardReport]` and reads typed attributes
  (`report.boards_ok`, `report.observations`, ...) instead of `.get(...)` with per-field
  `isinstance` guards for the top-level shape; the per-observation content coercion (a board's own
  loss-cause counters) is unrelated to `ShardReport`'s own shape and is untouched.
- `scrape_join._report_shards`, `shard_speedup.ratios_from_reports`, and
  `update_ledgers.failures` read `ShardReport` attributes directly — `r.seconds`, `r.errors`,
  `r.done` — with no `or 0` fallback, because `ShardReport.from_json` is now the single place a
  corrupt or missing field degrades to a safe default, once, rather than at every reader.
- `scrape_run._shard_id` and `scrape_run._plan_minutes` are deleted; `scrape_run.main` calls
  `shard_plan.shard_index(args.assignment)` once and, when there's an assignment,
  `shard_plan.ScrapePlan.from_json(...)` once, then reads `plan.predicted_minutes(shard)` /
  `plan.serial_minutes(shard)`. `embed_run.main` calls `shard_plan.shard_index` for its log
  context.
- `scrape_plan.py` and `embed_plan.py`'s `_write_plan` shrink to two lines each (`to_json()` +
  write + the pre-existing, currently-unread stdout `print` — left as-is, see below) and construct
  a `shard_plan.ScrapePlan`/`shard_plan.EmbedPlan` instead of a hand-built dict at both call sites
  (the empty-plan early return and the full plan).
- The stdout `print(json.dumps({"shards": ..., "count": ...}), flush=True)` inside both
  `_write_plan`s predates this change and is dead code — nothing in `pipeline.yml` reads a scrape-
  or embed-plan step's stdout as JSON, only the `plan.json` file the same step writes. Left
  untouched per this repo's surgical-changes convention (noticed, not removed, since removing it
  was not asked for and is not needed to fix the undeclared-shape problem).
- Tests: `tests/test_shard_plan.py` (new, named after the new module) covers `shard_index`,
  both Plan types' `to_json`/`from_json` round-trips and on-disk shape, tolerance of an older or
  corrupt plan, and an out-of-range shard index. `tests/test_observability.py`,
  `tests/test_scrape_run.py`, `tests/test_scrape_join.py`, `tests/test_shard_speedup.py`, and
  `tests/test_log_contract.py` are updated to construct `ShardReport` instances (or, for the
  handful of tests specifically exercising tolerance of malformed input, to route a raw dict
  through `ShardReport.from_json` first) instead of passing bare dicts to functions that now expect
  the typed record. `tests/test_update_ledgers.py`, `tests/test_scrape_plan.py`, and
  `tests/test_embed_plan.py` needed no changes — they exercise the CLI end to end and assert on
  the resulting JSON file's parsed content, which is unchanged.

## Rejected alternatives

- **One shared record type / one generic JSON-dataclass helper for both `ShardReport` and the Plan
  shapes.** Rejected above: no shared consumer, different packing units, and different tolerance
  needs each reader actually relies on.
- **Fold `ScrapePlan` and `EmbedPlan` into one `Plan` dataclass** with a superset of fields.
  Rejected: the two are written by different modules for different fan-outs and never compared to
  each other; a merged shape would need optional fields for whichever half a given `plan.json`
  lacks, which is the same undeclared-by-omission problem this change exists to close.
- **Replace the `pipeline.yml` heredocs with a `python -m headstart.ingest.X` entry point** that
  prints `count`/`shards`. Rejected as unnecessary complexity for two lines of plain, still-correct
  JSON key access that needs no package import — promoting it would add an entry point whose only
  job is echoing two keys already in a file the step just wrote.
- **`shard_index` as a method on `ScrapePlan`.** Rejected: a shard determines its own index from
  its `--assignment` filename before it has read (or, for `embed_run`, ever reads) any plan; a
  Plan does not hand out shard identity, a shard asks a Plan for its own entry using an index it
  already has.
