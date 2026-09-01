#!/usr/bin/env python3
"""The `merge` job — five stages and a storage check, none of them read by any other analyser.

`merge` runs `embed_merge`, `update_meta`, `index sync`, `index prune --apply`, `role_trends`, the
three HF uploads, and finally a workflow-embedded (not Python-package) storage check, all in one
job's log, `if: always()` — it is the only stage that runs even when `scrape`/`embed` partially
failed, so it is also where a partial run's consequences show up first.

**`embed_merge`** — `prior store: P vectors; F fragment(s)`, then `+n from {fragment} (running
total)` per fragment, then `merged A new vectors — store now holds T`. `F` less than the embed
job's shard count means a shard's artifact didn't arrive (a failed shard, or a cancelled matrix) —
`T` still grows correctly from whichever fragments did, but `A` undercounts what was planned.

**`update_meta`** — `derivations vX stored, vY in code — SWEEPING|no sweep`. A **sweep** re-derives
every stored row's experience/salary fields (`DERIVATIONS_VERSION` bumped, see CLAUDE.md) and is a
real, one-time wall-clock cost for this job — a merge job that's unusually slow right after a
derivations bump is not a regression, check this line before calling it one.

**`index sync`** — the corpus/index health surface a past investigation
(`docs/pipeline/2026-08-13_five-run-log-review.md`) had to reconstruct BY HAND, counting `[index]
add`/`evict` id-batch lines per board prefix across five runs to prove whole Eightfold boards were
flapping out and back in every run. That mechanism (ADR-0053's `unauthoritative` scope-exclusion vs
ADR-0046's collapse guard were two DIFFERENT protections — conflating them is the same mistake
CLAUDE.md's "duplicate boards" section warns about for the liveness ledger) is fully logged:
`scrape outcome: N Board(s) ... not authoritative ... excluded from the eviction scope` names each
excluded Board and why; `collapse guard: withheld W evictions across B Boards` was the *other*
guard, for a Board that scraped short without going unauthoritative, until ADR-0101 removed it —
still parsed because runs before 2026-09-01 carry the line, and absent from newer ones because
nothing withholds that way any more, never because a run was clean. This file parses both, plus
`plan: add A (L new + R re-embedded), evict E -> net D rows` (the one line that says whether the
served index actually grew), and buckets the `add`/`evict` id batches by ATS (not by full board —
`ats:tenant:id` is ambiguous past the first `:`, per CLAUDE.md, and splitting further is how a past
prune bug misattributed Workday ids) so oscillation shows up without hand-counting again.

**`index prune`** — `keep-set: N live Boards`, then `evict off-Board`/`evict duplicate` each with
their own per-ATS ranked breakdown (the emitter's own top-5, not this file's).

**`role_trends`** — family assignment counts, the non-tech% (the ADR-0017 tech filter's measured
creep into the served table), and family-transition counts (the signal that told #57's "software-
engineering decline" apart from reassignment vs real closures).

**Storage** — the workflow's own (not Python-package) `usedStorage X GB · live Y GB · N commits`
line, plus the squash outcome if one fired. `live` is the number to trend — squash-independent,
unlike `usedStorage`, which rises and falls on HF's own orphan-collection schedule.

Run: python scripts/runlog/fanout_merge.py 32272854468
     python scripts/runlog/fanout_merge.py 32261793515 32272854468   # compare
"""

from __future__ import annotations

import re
from collections import Counter

from run_logs import Run, common_args, runs_from

# --- embed_merge ---------------------------------------------------------------------------
MERGE_PRIOR = re.compile(
    r"\[embed_merge\] prior store: (\d+) vectors; (\d+) fragment\(s\)"
)
MERGE_FRAG = re.compile(r"\[embed_merge\] \+(\d+) from (\S+) \(running total (\d+)\)")
MERGE_DONE = re.compile(
    r"\[embed_merge\] merged (\d+) new vectors — store now holds (\d+)"
)
MERGE_UPGRADE_DROP = re.compile(
    r"\[embed_merge\] upgrades: dropped (\d+) stale rows for (\d+) ids"
)
MERGE_UPGRADE_HOLD = re.compile(r"\[embed_merge\] upgrades: holding (\d+) id\(s\)")
# A shortfall against the plan's own shard count. Before it existed, a missing shard showed
# up only as a smaller-than-expected vector total — i.e. as a mystery.
MERGE_SHORTFALL = re.compile(r"only (\d+) of (\d+) shard fragment\(s\) arrived")
# Fires only when --expect-shards was never passed (an unknown expectation) — the healthy
# nothing-was-planned case is a separate, differently-worded info line and must not be conflated.
MERGE_NO_FRAGS = re.compile(r"no fragments under (\S+) — nothing to merge")

# --- update_meta -----------------------------------------------------------------------------
META_LINE = re.compile(
    r"\[update_meta\] derivations v(\d+) stored, v(\d+) in code — (SWEEPING|no sweep); "
    r"corpus facts for (\d+) Jobs; (\d+) queued to re-derive"
)
META_REFRESHED = re.compile(
    r"\[update_meta\] refreshed (\d+) rows: (\d+) with changed facts, "
    r"(\d+) with changed derivations, (\d+) given a has_description"
)
# Which *direction* the sweep moved things (ADR-0066). "N with changed derivations" above is
# the same number whether a version bump won 4,000 answers or stripped them.
META_DIRECTION = re.compile(
    r"(experience|salary) derivations: (\d+) gained, (\d+) lost, (\d+) retiered, "
    r"(\d+) moved"
)
META_WATERMARK = re.compile(r"\[update_meta\] watermark -> v(\d+)")

# --- index sync ------------------------------------------------------------------------------
SCOPE_OUTCOME = re.compile(
    r"scrape outcome: (\d+) Board\(s\) returned a list that is not authoritative"
)
SCOPE_EXCLUDED = re.compile(r"scope-excluded Board: (\S+) — (.+)")
COLLAPSE_GUARD = re.compile(
    r"collapse guard: withheld (\d+) evictions across (\d+) Boards"
)
WITHHELD_BOARD = re.compile(r"withheld (\d+) evictions on (\S+)")
# ADR-0083's per-Job grace period — distinct from the two above (ADR-0053 scope exclusion is
# per-Board; ADR-0046's collapse guard was a per-Board cap, removed by ADR-0101). Since that
# removal it is the only mechanism that withholds an eviction on a Board still in scope.
GRACE = re.compile(
    r"grace period: (\d+) id\(s\) unconfirmed.*?of the (\d+) carried in, (\d+) reappeared "
    r"in this scrape and (\d+) are unconfirmed again"
)
# What ADR-0053 exclusion withholds, in ROWS. The collapse guard always reported rows, which is
# the only reason its 267 -> 953 ratchet was ever caught; this one reported only a Board count
# until PR #280.
SCOPE_ROWS = re.compile(
    r"scope exclusion keeps (\d+) eviction-candidate row\(s\) out of scope across (\d+) Board"
)
SCOPE_ROW_BOARD = re.compile(
    r"(\d+) eviction-candidate row\(s\) kept out of scope on (\S+)"
)
SYNC_PLAN = re.compile(
    r"\[index\] plan: add (\d+) \((\d+) new listings \+ (\d+) re-embedded\), "
    r"evict (\d+) -> net ([+-]\d+) rows"
)
SYNC_DONE = re.compile(r"\[index\] done: table '\S+' now holds (\d+) rows")
ID_BATCH = re.compile(r"\[index\] (add|evict) \[(\d+)-(\d+) of \d+\]: (.+)")

# --- index prune -----------------------------------------------------------------------------
KEEP_SET = re.compile(r"\[index\] keep-set: (\d+) live Boards")
PRUNE_SUMMARY = re.compile(
    r"\[index\] index: (\d+) rows \| evict (\d+) \((\d+) off-Board \+ (\d+) duplicate\) "
    r"-> (\d+) remain"
)
PRUNE_BREAKDOWN = re.compile(
    r"\[index\] evict (off-Board|duplicate): (\d+) rows across (\d+) ATSes \(([^)]*)\)"
)
PRUNE_DONE = re.compile(
    r"\[index\] done: pruned (\d+) rows; table '\S+' now holds (\d+)"
)

# A closed set (headstart.scrapers.registry.SCRAPERS keys, 2026-08-23 — update if a new ATS
# ships). `_log_ids` joins ids with plain spaces and some Workday ids ARE a raw location string
# ("workday:gianteagle/GEExternalcareers:0018 - Shaler - Supermarket") or a full address with
# embedded spaces/commas — `str.split()` on the batch line shreds a single id into several
# fake ones. Splitting instead on a lookahead for a known ATS-prefix boundary recovers the real
# ids, verified against two real batch lines from run 32621581881: a `[1-16 of 16]` add batch
# splits correctly into 16 (a blind `.split()` gives 46), and a `[1-50 of 50]` evict batch splits
# correctly into 50 (a blind `.split()` gives 143).
_ATS = (
    "ashby",
    "darwinbox",
    "eightfold",
    "freshteam",
    "greenhouse",
    "join",
    "keka",
    "lever",
    "oracle",
    "personio",
    "recruitee",
    "ripplehire",
    "rippling",
    "sensehq",
    "smartrecruiters",
    "successfactors",
    "teamtailor",
    "trakstar",
    "workable",
    "workday",
    "zoho",
)
_ID_BOUNDARY = re.compile(r"(?=\b(?:" + "|".join(_ATS) + r"):)")

# --- role_trends -----------------------------------------------------------------------------
TRENDS_ASSIGNING = re.compile(
    r"\[role_trends\] assigning (\d+) served rows to (\d+) families via (\d+) clusters"
)
TRENDS_APPENDED = re.compile(
    r"\[role_trends\] appended (\d+) rows @ \S+ -> \S+ \| top: (.+) \| new in (\d+)d: (\d+)"
)
TRENDS_NONTECH = re.compile(
    r"\[role_trends\] non-tech: (\d+) of (\d+) served rows \(([\d.]+)%"
)
TRENDS_ASSIGNMENTS = re.compile(
    r"\[role_trends\] assignments: (\d+) of (\d+) rows changed family \(([\d.]+)%\), "
    r"(\d+) transition rows(?: \| top: (.+))?"
)
# The other real, non-error shape `assignments:` takes: no PREVIOUS snapshot to diff against
# (first run ever, or a centroid refit discarded it) — reachable on any run, not just the first.
TRENDS_FIRST_SNAPSHOT = re.compile(
    r"\[role_trends\] assignments: (.+) — wrote (\d+) rows to \S+; transitions start next run"
)
# The reassignment diff is `except Exception` (never fails the run) — a real, reachable warning
# distinct from the three pre-run skip paths below, which abort BEFORE assigning anything.
TRENDS_DIFF_SKIPPED = re.compile(r"\[role_trends\] assignment diff skipped: (.+)")
# role_trends' three distinct skip paths — matched on its own line, never on a substring of the
# whole job log, which `update_meta` can also emit "missing"/"empty" text into.
TRENDS_SKIP_MISSING = re.compile(
    r"\[role_trends\] skipping trends this run — missing (.+)"
)
TRENDS_SKIP_EMPTY = re.compile(r"\[role_trends\] served table '\S+' is empty")
TRENDS_SKIP_TAXONOMY = re.compile(
    r"\[role_trends\] role taxonomy unusable, no trends this run"
)

# --- storage (workflow-embedded, not the Python package) -------------------------------------
STORAGE_LINE = re.compile(r"usedStorage ([\d.]+) GB · live ([\d.]+) GB · (\d+) commits")
SQUASHED = re.compile(r"squashed; live ([\d.]+) GB")
NOTHING_TO_RECLAIM = re.compile(r"under (\d+) GB — nothing to reclaim")


def embed_merge_report(text: str) -> None:
    print("-- embed_merge --", flush=True)
    p = MERGE_PRIOR.search(text)
    if p:
        print(
            f"  prior store: {p.group(1)} vectors; {p.group(2)} fragment(s)", flush=True
        )
    frags = MERGE_FRAG.findall(text)
    for n, name, total in frags:
        print(f"    +{n} from {name} (running total {total})", flush=True)
    d = MERGE_DONE.search(text)
    if d:
        print(
            f"  merged {d.group(1)} new vectors — store now holds {d.group(2)}",
            flush=True,
        )
    drop = MERGE_UPGRADE_DROP.search(text)
    if drop:
        print(
            f"  upgrades: dropped {drop.group(1)} stale rows for {drop.group(2)} ids",
            flush=True,
        )
    hold = MERGE_UPGRADE_HOLD.search(text)
    if hold:
        print(
            f"  NB: upgrades holding {hold.group(1)} id(s) whose replacement did not arrive "
            "this run — check whether an embed shard failed",
            flush=True,
        )
    sf = MERGE_SHORTFALL.search(text)
    if sf:
        arrived, expected = sf.groups()
        print(
            f"  NB: only {arrived} of {expected} shard fragment(s) arrived — the missing "
            "shard(s)' Docs are absent from this merge until a later run re-plans them",
            flush=True,
        )
    nf = MERGE_NO_FRAGS.search(text)
    if nf:
        print(
            f"  NB: no fragments at all under {nf.group(1)} — unexpected unless the embed "
            "stage produced nothing, was skipped, or its artifacts did not download",
            flush=True,
        )


def update_meta_report(text: str) -> None:
    print("-- update_meta --", flush=True)
    m = META_LINE.search(text)
    if not m:
        print("  no derivations line found (store may not exist yet)", flush=True)
        return
    stored, code, mode, facts, queued = m.groups()
    print(
        f"  derivations v{stored} stored, v{code} in code — {mode}; corpus facts for "
        f"{facts} Jobs; {queued} queued to re-derive",
        flush=True,
    )
    if mode == "SWEEPING":
        print(
            "  NB: a full sweep re-derives every stored row — a real, one-time wall-clock cost "
            "for THIS merge job. Not a regression if the derivations version just bumped.",
            flush=True,
        )
    r = META_REFRESHED.search(text)
    if r:
        rows, facts_c, deriv_c, backfilled = r.groups()
        print(
            f"  refreshed {rows} rows: {facts_c} changed facts, {deriv_c} changed derivations, "
            f"{backfilled} backfilled has_description",
            flush=True,
        )
    for label, gained, lost, retiered, moved in META_DIRECTION.findall(text):
        print(
            f"  {label} derivations: {gained} gained, {lost} lost, {retiered} retiered, "
            f"{moved} moved — `lost` is the one worth alarm; `retiered` is direction-blind "
            '(a demotion counts the same as a promotion) so read it as "go look", not a '
            "verdict (ADR-0066)",
            flush=True,
        )

    w = META_WATERMARK.search(text)
    if w:
        print(f"  watermark -> v{w.group(1)}", flush=True)


def _id_churn_by_ats(text: str) -> dict[str, Counter]:
    """Bucket `[index] add`/`evict` id batches by ATS only — never by full board. `ats:tenant:id`
    is ambiguous past the first `:` (a Workday id can itself contain both), and this is exactly
    the key-splitting mistake that mis-attributed prune's own duplicate rows before (CLAUDE.md)."""
    out: dict[str, Counter] = {"add": Counter(), "evict": Counter()}
    for label, start, end, ids in ID_BATCH.findall(text):
        expected = int(end) - int(start) + 1
        parts = [p.strip() for p in _ID_BOUNDARY.split(ids) if p.strip()]
        if len(parts) != expected:
            # The boundary regex missed or over-split this line — trust the line's own [start-end]
            # count over a wrong per-ATS breakdown; skip rather than silently misattribute.
            continue
        for jid in parts:
            out[label][jid.split(":", 1)[0]] += 1
    return out


def index_sync_report(text: str) -> None:
    print("-- index sync --", flush=True)
    so = SCOPE_OUTCOME.search(text)
    if so:
        print(
            f"  ADR-0053 scope exclusion: {so.group(1)} Board(s) unauthoritative this run "
            "(truncated or raised) — excluded from eviction scope, not evicted",
            flush=True,
        )
        excluded = SCOPE_EXCLUDED.findall(text)
        for board, why in excluded[:5]:
            print(f"    {board} — {why[:100]}", flush=True)
        if len(excluded) > 5:
            print(f"    +{len(excluded) - 5} more", flush=True)
        sr = SCOPE_ROWS.search(text)
        if sr:
            rows, n_boards = sr.groups()
            print(
                f"  ADR-0053 is keeping {rows} eviction-candidate row(s) out of scope across "
                f"{n_boards} Board(s) — no drain exists, so watch this ACROSS runs, not within "
                "one; a Board short on every run never re-enters scope",
                flush=True,
            )
            for count, board in SCOPE_ROW_BOARD.findall(text)[:5]:
                print(f"    {count} kept out of scope on {board}", flush=True)

    g = GRACE.search(text)
    if g:
        unconfirmed, carried_in, reappeared, still_waiting = g.groups()
        print(
            f"  ADR-0083 grace period: {unconfirmed} id(s) unconfirmed; of {carried_in} carried "
            f"in from last run, {reappeared} reappeared (evicted nothing) and {still_waiting} "
            "are unconfirmed again",
            flush=True,
        )

    cg = COLLAPSE_GUARD.search(text)
    if cg:
        withheld, n_boards = cg.groups()
        print(
            f"  ADR-0046 collapse guard: withheld {withheld} evictions across {n_boards} "
            "Board(s) that lost too much in one run (truncated scrape, not a delisting)",
            flush=True,
        )
        for count, board in WITHHELD_BOARD.findall(text)[:5]:
            print(f"    withheld {count} on {board}", flush=True)

    sp = SYNC_PLAN.search(text)
    if sp:
        add, listings, reembedded, evict, net = sp.groups()
        print(
            f"  plan: add {add} ({listings} new listings + {reembedded} re-embedded), "
            f"evict {evict} -> net {net} rows",
            flush=True,
        )
    sd = SYNC_DONE.search(text)
    if sd:
        print(f"  done: table now holds {sd.group(1)} rows", flush=True)

    churn = _id_churn_by_ats(text)
    if churn["add"] or churn["evict"]:
        print("  churn by ATS (from the add/evict id batches):", flush=True)
        ats_all = sorted(set(churn["add"]) | set(churn["evict"]))
        for ats in ats_all[:10]:
            a, e = churn["add"].get(ats, 0), churn["evict"].get(ats, 0)
            flag = "  <- both sides active" if a and e else ""
            print(f"    {ats:16} add {a:6}  evict {e:6}{flag}", flush=True)
        if len(ats_all) > 10:
            print(f"    +{len(ats_all) - 10} more ATSes", flush=True)
        print(
            "    NB: an ATS with both add>0 and evict>0 in the SAME run is the flapping shape "
            "docs/pipeline/2026-08-13_five-run-log-review.md found by hand — compare against the "
            "next run's numbers for the same ATS before calling it churn (one run is an anecdote).",
            flush=True,
        )


def index_prune_report(text: str) -> None:
    print("-- index prune --", flush=True)
    ks = KEEP_SET.search(text)
    if ks:
        print(f"  keep-set: {ks.group(1)} live Boards", flush=True)
    ps = PRUNE_SUMMARY.search(text)
    if ps:
        rows, evict, off_board, dup, remain = ps.groups()
        print(
            f"  {rows} rows | evict {evict} ({off_board} off-Board + {dup} duplicate) "
            f"-> {remain} remain",
            flush=True,
        )
    for label, n, n_ats, ranked in PRUNE_BREAKDOWN.findall(text):
        print(
            f"    evict {label}: {n} rows across {n_ats} ATSes ({ranked})", flush=True
        )
    pd = PRUNE_DONE.search(text)
    if pd:
        print(
            f"  done: pruned {pd.group(1)} rows; table now holds {pd.group(2)}",
            flush=True,
        )


def role_trends_report(text: str) -> None:
    print("-- role_trends --", flush=True)
    a = TRENDS_ASSIGNING.search(text)
    if a:
        n, families, clusters = a.groups()
        print(
            f"  assigning {n} served rows to {families} families via {clusters} clusters",
            flush=True,
        )
    ap = TRENDS_APPENDED.search(text)
    if ap:
        written, top, days, fresh = ap.groups()
        print(f"  appended {written} rows | top: {top}", flush=True)
        print(f"  new in {days}d: {fresh}", flush=True)
    nt = TRENDS_NONTECH.search(text)
    if nt:
        n, total, pct = nt.groups()
        print(
            f"  non-tech: {n} of {total} served rows ({pct}% — ADR-0017 filter creep)",
            flush=True,
        )
    asn = TRENDS_ASSIGNMENTS.search(text)
    fs = TRENDS_FIRST_SNAPSHOT.search(text)
    ds = TRENDS_DIFF_SKIPPED.search(text)
    if asn:
        moved, total, pct, rows, top = asn.groups()
        print(
            f"  reassignments: {moved} of {total} rows changed family ({pct}%), "
            f"{rows} transition rows" + (f" | top: {top}" if top else ""),
            flush=True,
        )
    elif fs:
        why, rows = fs.groups()
        print(
            f"  no previous snapshot to diff — {why}, wrote {rows} rows; "
            "transitions start next run",
            flush=True,
        )
    elif ds:
        print(f"  reassignment diff skipped: {ds.group(1)}", flush=True)
    elif m := TRENDS_SKIP_MISSING.search(text):
        print(f"  skipped this run — missing {m.group(1)}", flush=True)
    elif TRENDS_SKIP_EMPTY.search(text):
        print("  skipped this run — served table is empty", flush=True)
    elif TRENDS_SKIP_TAXONOMY.search(text):
        print(
            "  skipped this run — role taxonomy unusable (see the job log for the exception)",
            flush=True,
        )


def storage_report(text: str) -> None:
    print("-- storage / LFS --", flush=True)
    s = STORAGE_LINE.search(text)
    if not s:
        print("  no storage line found (reclaim step may not have run)", flush=True)
        return
    used, live, commits = s.groups()
    print(f"  usedStorage {used} GB · live {live} GB · {commits} commits", flush=True)
    sq = SQUASHED.search(text)
    ntr = NOTHING_TO_RECLAIM.search(text)
    if sq:
        print(f"  squashed this run -> live {sq.group(1)} GB", flush=True)
    elif ntr:
        print(
            f"  under the {ntr.group(1)} GB threshold — no reclaim needed", flush=True
        )


def report(run: Run) -> None:
    jobs = run.stage_jobs("merge")
    if not jobs:
        print("  no merge job in this run", flush=True)
        return
    text = run.log(jobs[0])
    embed_merge_report(text)
    update_meta_report(text)
    index_sync_report(text)
    index_prune_report(text)
    role_trends_report(text)
    storage_report(text)


def main() -> None:
    args = common_args(__doc__.split("\n")[0]).parse_args()
    runs = runs_from(args)
    for run in runs:
        print(f"\n===== run {run.id} head={run.head} — merge =====", flush=True)
        report(run)
    if len(runs) > 1:
        print(
            "\n  NB: compare `live` GB (storage) and per-ATS churn (index sync) across these "
            "runs directly — both are cumulative/trend signals, not single-run verdicts.",
            flush=True,
        )


if __name__ == "__main__":
    main()
