#!/usr/bin/env python3
"""Join scrape-shard outputs into one snapshot — the union step of ADR-0026 (ADR-0025 Phase 2).

Each scrape shard wrote its boards to its own ``{ats}.jsonl`` under a fragment dir. Eviction is
scoped to the Boards present in ``data/jobs/`` (ADR-0014), so before tech-filter/sync the shards
**must** be unioned into one snapshot — otherwise a Board scraped by shard 3 wouldn't have its
closed postings evicted. Boards are shard-disjoint (the planner partitions them), so the union is a
per-ATS concatenation; a duplicate line from an intra-board resume is deduped downstream by id
(``corpus.iter_jobs``), exactly as with the monolith's single-file output.

Streams line-by-line (never buffering a whole ATS), and a shard that timed out mid-scrape simply
contributes the boards it did finish — partial-harvest safety survives per shard.

The union is also the last stage that *needs* the full records, so it records the scope it defines:
``data/state/scraped_boards.json`` (ADR-0161). The merge job reads that set of Board keys instead
of re-deriving it from the whole pre-tech-filter snapshot, which is what kept ~9 GB of job text on
the critical path between the two jobs.

Run: python -m headstart.ingest.scrape_join [--shards DIR] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from headstart import log
from headstart.board_identity import board_key_of
from headstart.ingest import (
    REPO_ROOT,
    UNAUTHORITATIVE_BOARDS_PATH,
    observability,
    shard_speedup,
)
from headstart.ingest.index_plan import boards_by_canon, live_keep_set, resolve_board
from headstart.ingest.observability import ShardReport

_log = log.get(__name__, __spec__)

_SHARDS = (
    REPO_ROOT / "data" / "scrape" / "fragments"
)  # outside data/jobs, so the joined snapshot stays clean
_OUT = REPO_ROOT / "data" / "jobs"
# Under data/state because that is what rides the corpus-state artifact to the job running
# `index sync` — the shard fragments themselves stop at this stage (ADR-0053).
_UNAUTHORITATIVE = UNAUTHORITATIVE_BOARDS_PATH
_SCRAPED_BOARDS = REPO_ROOT / "data" / "state" / "scraped_boards.json"
_LEDGER = REPO_ROOT / "data" / "validate" / "liveness"
_SPEEDUP = REPO_ROOT / "data" / "state" / "shard_speedup.csv"
_HEALTH = REPO_ROOT / "data" / "state" / "scrape_health.json"


def _fragment_dirs(root: Path) -> list[Path]:
    """Fragment dirs under ``root`` (each holding one or more ``{ats}.jsonl``), sorted."""
    return sorted(d for d in root.iterdir() if d.is_dir() and any(d.glob("*.jsonl")))


def write_unauthoritative_boards(
    reports: list[ShardReport], path: Path
) -> dict[str, str]:
    """Persist the Boards whose scraped list is not authoritative, keyed the way the index keys
    Boards.

    This is the hop ADR-0046 named as the honest fix and deferred. A Board whose scrape came back
    short still emits the pages it did get, so ``index sync`` — which infers "this Board was
    scraped" from the presence of any job line — evicts everything the truncated list is missing.
    The outcome was known all along: ``harvest.scrape_all`` records it per Board and the shard
    report carries it here. Nothing carried it any further, and whole Boards flapped out of search
    for a cycle at a time.

    The shard reports key errors ``{ats}:{slug}`` — the *scrape* list's key — while eviction scope
    is keyed by ``board_key()`` (ADR-0049). Those differ wherever a slug is not the Board tail:
    Workday's slug is the whole careers URL, so ``workday:https://x.wd1.myworkdayjobs.com/Site``
    has to become ``workday:x/Site`` or the lookup silently never matches. Rows that will not
    resolve (:func:`headstart.board_identity.board_key_of` returns ``None``) are dropped with a
    warning rather than written through unconverted, which would look like protection while
    providing none.

    Always writes, even with nothing to record: ``data/state`` round-trips through the HF dataset,
    so a run that skipped the write would leave the *previous* run's Boards in place and protect
    Boards that scraped cleanly this time.
    """
    unauthoritative: dict[str, str] = {}
    unresolved: list[str] = []
    for report in reports:
        # `errors` (the Board raised) and `truncated` (it returned a list it knows is short) are
        # kept apart upstream because they read differently in a log — but they mean one thing
        # here: this Board's list is not authoritative, so sync must not evict against it. The
        # truncations are the ones that actually flap; a raising Board writes no lines at all and
        # was never in the eviction scope to begin with.
        outcomes = {**report.errors, **report.truncated}
        for key, why in outcomes.items():
            board = board_key_of(key)
            if board is None:
                unresolved.append(str(key))
                continue
            unauthoritative[board] = str(why)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(unauthoritative, indent=1, sort_keys=True), encoding="utf-8"
    )
    if unresolved:
        _log.warning(
            f"{len(unresolved)} unauthoritative Board(s) could not be resolved to a board_key and "
            f"are NOT protected from eviction this run: {log.named_sample(sorted(unresolved))}"
        )
    _log.info(f"recorded {len(unauthoritative)} unauthoritative Board(s) -> {path}")
    return unauthoritative


def write_scraped_boards(boards: set[str], path: Path) -> None:
    """Persist the Boards this run's union covered — the eviction scope itself (ADR-0161).

    ``index sync`` needs a *set of Board keys*, and the only place that set is defined is the full
    pre-tech-filter scrape: a Board that emitted jobs but no *tech* jobs is in scope, and a scope
    read off the tech corpus would leave its closed postings serving forever. Until this, the merge
    job answered that by re-reading the whole of ``data/jobs/`` — which is why ~9 GB of job records
    rode the corpus-state artifact for a question worth a few hundred KB. The join already has
    every record in hand, so it derives the answer once, here, and ships that instead.

    Keys are :func:`~headstart.ingest.index_plan.resolve_board`'s, in the id's own casing, because
    that is exactly what the scope is compared against (ADR-0049) — the ledger both halves resolve
    against is committed to git, so the join and the merge read the same one. A Board that scraped
    clean with zero jobs has no id to resolve, so it is added from the shard reports' ``boards_ok``
    through :func:`~headstart.board_identity.board_key_of` — the ``board_key()`` its ids would
    carry, so the two sources agree.

    Always writes, even when the union covered nothing: ``data/state`` round-trips through the HF
    dataset, so a run that skipped the write would leave the *previous* run's Boards in place and
    scope this run's eviction on a scrape that never happened.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(boards), indent=1), encoding="utf-8")
    _log.info(f"recorded {len(boards)} scraped Board(s) -> {path}")


def main() -> int:
    log.setup()
    log.context("scrape_join")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--shards",
        default=str(_SHARDS),
        help="dir of scrape fragment dirs (default: data/scrape/fragments)",
    )
    ap.add_argument(
        "--out", default=str(_OUT), help="unioned snapshot dir (default: data/jobs)"
    )
    ap.add_argument(
        "--unauthoritative-boards",
        default=str(_UNAUTHORITATIVE),
        help="where to record the Boards whose scraped list is not authoritative, for `index "
        "sync` to exclude from the eviction scope "
        "(default: data/state/unauthoritative_boards.json)",
    )
    ap.add_argument(
        "--scraped-boards",
        default=str(_SCRAPED_BOARDS),
        help="where to record the Boards this union covered — the eviction scope `index sync` "
        "would otherwise re-derive from the whole full scrape "
        "(default: data/state/scraped_boards.json)",
    )
    ap.add_argument(
        "--ledger",
        default=str(_LEDGER),
        help="liveness ledger dir, for resolving ids to live Boards the way `index sync` does "
        "(default: data/validate/liveness)",
    )
    ap.add_argument(
        "--speedup-ledger",
        default=str(_SPEEDUP),
        help="shard_speedup.csv to blend this run's measured fan-out speedup into (ADR-0054)",
    )
    ap.add_argument(
        "--scrape-health",
        default=str(_HEALTH),
        help="small coverage/loss verdict carried to the publication summary",
    )
    ap.add_argument(
        "--expected-shards",
        type=int,
        default=0,
        help="planner's shard count; missing reports make coverage degraded",
    )
    args = ap.parse_args()

    shards_root = Path(args.shards)
    frags = _fragment_dirs(shards_root) if shards_root.exists() else []
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # group source files by ATS filename ({ats}.jsonl), across all shards
    per_ats: dict[str, list[Path]] = {}
    for frag in frags:
        for f in sorted(frag.glob("*.jsonl")):
            per_ats.setdefault(f.name, []).append(f)
    _log.info(f"{len(frags)} shard(s), {len(per_ats)} ATS file(s)")

    # Resolved against the live ledger as the union streams, because this is the one stage that
    # holds every scraped record. `index sync` reads the set instead of the records (ADR-0161);
    # the ledger is committed, so both halves resolve ids through the same lookup.
    live = boards_by_canon(live_keep_set(args.ledger))
    boards: set[str] = set()

    total = 0
    for ats_file, sources in sorted(per_ats.items()):
        n = 0
        with (out / ats_file).open("w", encoding="utf-8") as dst:
            for src in sources:
                with src.open(encoding="utf-8") as s:
                    for lineno, line in enumerate(s, 1):
                        if line.strip():
                            dst.write(line if line.endswith("\n") else line + "\n")
                            try:
                                job_id = json.loads(line)["id"]
                            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                                # Still fatal — a torn fragment must not join — but named, so
                                # the abort says which shard's file and line to open.
                                log.fail(_log, f"{src} line {lineno}: {exc!r}")
                            boards.add(resolve_board(job_id, live))
                            n += 1
        total += n
        _log.info(f"{ats_file}: {n} lines from {len(sources)} shard(s)")

    _log.info(f"wrote {total} lines across {len(per_ats)} ATS files -> {out}")
    reports = observability.read_shards(shards_root)
    # A Board scraped clean with zero jobs writes no line above, so it would never enter the
    # scope and its closed postings would be served forever. `boards_ok` is that evidence; keyed
    # through `board_key_of`, it is the prefix the Board's own ids carry. A truncated Board is in
    # `boards_ok` too, and `index sync` drops it again as unauthoritative (ADR-0053).
    # A Board that answered 404 raised, so it is in `errors`, not `boards_ok`, and stays out.
    unresolved: list[str] = []
    for report in reports:
        for key in report.boards_ok:
            board = board_key_of(key)
            if board is None:
                unresolved.append(key)
            else:
                boards.add(board)
    if unresolved:
        # Named: these Boards stay out of the eviction scope, so their closed postings are
        # served until the key resolves — a count alone gives nothing to go and fix.
        _log.info(
            f"{len(unresolved)} boards_ok key(s) did not resolve to a board_key and were not "
            f"added to the scraped-Board scope: {log.named_sample(sorted(unresolved))}"
        )
    # Before the telemetry below, like the unauthoritative-Board write: this is the eviction
    # signal, and an empty file is the honest record of a run that joined nothing.
    write_scraped_boards(boards, Path(args.scraped_boards))
    # Written unconditionally, before the summary: an empty file is the honest record of "every
    # Board's list is authoritative", and the summary below is telemetry that must never gate the
    # eviction signal.
    write_unauthoritative_boards(reports, Path(args.unauthoritative_boards))
    _update_speedup(reports, Path(args.speedup_ledger))
    health = observability.ScrapeHealth.from_reports(
        reports, expected_reports=args.expected_shards or None
    )
    observability.write_scrape_health(Path(args.scrape_health), health)
    _report_shards(reports, total, len(per_ats), health)
    return 0


def _update_speedup(reports: list[ShardReport], path: Path) -> None:
    """Blend this run's measured fan-out speedup into the ledger the next plan divides by.

    Written here rather than in ``update_ledgers`` because the shard reports are read here and
    nowhere else. Never fatal: a missing speedup costs one run of prediction accuracy, and the
    join is un-bankable — losing the run's scrape to a telemetry write would be a far worse
    trade (the same reasoning that keeps a malformed shard report tolerated rather than fatal
    throughout this module — see ``ShardReport.from_json``, ADR-0154).
    """
    try:
        ratios = shard_speedup.ratios_from_reports(reports)
        if not ratios:
            _log.info(
                "no usable shard timings — leaving the speedup ledger as it stands"
            )
            return
        stored = shard_speedup.load(path)
        blended = shard_speedup.blend(stored.ratio, ratios)
        shard_speedup.save(path, blended, len(ratios))
        # `blend` drops ratios under MIN_RATIO; count what it kept, or an all-dropped run
        # reads as a real update
        usable = sum(r >= shard_speedup.MIN_RATIO for r in ratios)
        if not usable:
            _log.info(
                f"fan-out speedup: unchanged at {stored.ratio:.2f}x — all {len(ratios)} "
                f"shard ratio(s) this run fell below {shard_speedup.MIN_RATIO}x and were dropped"
            )
        else:
            _log.info(
                f"fan-out speedup: {blended:.2f}x (was {stored.ratio:.2f}x, {usable} usable "
                f"shard(s) this run, {len(ratios) - usable} below "
                f"{shard_speedup.MIN_RATIO}x dropped)"
            )
    except Exception as exc:  # noqa: BLE001 - telemetry must never sink the join
        _log.warning(f"could not update the speedup ledger: {exc}", exc_info=True)


def _report_shards(
    reports: list[ShardReport],
    lines: int,
    ats_files: int,
    health: observability.ScrapeHealth | None = None,
) -> None:
    """The whole fan-out's story in one place — the view no single shard job can give.

    A shard's numbers only ever existed in its own runner's log, so questions like "did any
    shard run out of time, and how much work did it defer?" needed fifteen job logs opened by
    hand. The reports ride the fragment artifacts here, so this is the first point where they
    can be added up; ``main`` reads them once and hands them to both consumers.
    """
    if not reports:
        if health is not None and health.expected_report_count:
            # The worst outcome this stage can have, and it used to leave only the INFO below:
            # no `boards_ok`, no unauthoritative Boards, and no verdict line on the run page.
            _log.warning(
                f"no shard reports arrived (0/{health.expected_report_count}) — fresh coverage "
                "unavailable; "
                + (
                    "no job lines either, so the eviction scope is empty and sync evicts nothing"
                    if not lines
                    else f"{lines} job lines joined, but no Board is marked unauthoritative, so "
                    "sync will evict against any truncated Board's partial list"
                )
            )
        _log.info(
            "no shard reports — nothing to aggregate (older shards, or a local run)"
        )
        return
    health = health or observability.ScrapeHealth.from_reports(reports)
    if health.report_count < health.expected_report_count:
        # The verdict line counts the shortfall; this names it. Shards are `shard-{k}`, k from 0.
        arrived = {r.shard for r in reports}
        missing = [
            str(k) for k in range(health.expected_report_count) if str(k) not in arrived
        ]
        _log.info(f"missing shard reports: {log.named_sample(missing)}")

    killed = [r for r in reports if r.killed_by_budget]
    deferred = sum(r.undone for r in reports)
    errors = sum(len(r.errors) for r in reports)
    retries: Counter[str] = Counter()
    for r in reports:
        retries.update(r.retries)
    # The cross-shard view a single shard cannot have: pool depth (ADR-0067 first measured 30 jobs
    # sharing just 11 WARP IPs; ADR-0081 corrected that to 11,007 distinct IPs across 150
    # shard-runs of real traffic) is a fact no shard's own count of its rotations can show.
    egress_ips: Counter[str] = Counter()
    for r in reports:
        egress_ips.update(r.egress_ips)
    distinct_ips = sorted(k[3:] for k in egress_ips if k.startswith("ip:"))
    distinct_colos = sorted(k[5:] for k in egress_ips if k.startswith("colo:"))
    # Every field below is typed and defaulted on `ShardReport` itself (ADR-0154), so a
    # truncated report reads as zero/empty rather than raising — the coercion this used to do
    # with `.get(...) or 0` now happens once, in `ShardReport.from_json`.
    slowest_seconds = max((r.seconds for r in reports), default=0.0)
    worst_board = max(
        (r.board_seconds.get("max", 0.0) for r in reports),
        default=0.0,
    )
    ratios = [
        r.seconds / 60 / r.predicted_minutes
        for r in reports
        if r.predicted_minutes and r.seconds
    ]
    ratio_span = f"{min(ratios):.2f}-{max(ratios):.2f}x" if ratios else ""

    coverage_line = health.coverage_line()
    loss_lines = health.loss_lines()

    if killed:
        # An annotation, not an info line: a shard that ran out of time silently deferred work,
        # and that is the single fact most worth seeing on the run page.
        # Which Boards, across the whole fan-out. The shard names its own, but the run page is
        # where a Board that keeps being deferred becomes visible as a pattern rather than as
        # one shard's bad luck — and a name is what turns "a shard was killed" into a fix.
        # Folded into the one annotation rather than a second: this step's budget is ten.
        lost = [b for r in killed for b in r.deferred]
        _log.warning(
            f"{len(killed)} shard(s) hit the time budget, deferring {deferred} boards: "
            + ", ".join(str(r.shard or "?") for r in killed)
            + (f"; deferred boards: {log.named_sample(lost)}" if lost else "")
        )
    if errors:
        # Classified and with a denominator, not a bare count. "N board errors across 15 shards"
        # cannot say whether the run met throttling, dead hosts, or a parse bug, nor on which
        # ATS — and answering that meant opening all 15 shard logs, because each shard classifies
        # only its own. The rate matters as much as the count: the same 400 errors against 20,000
        # Boards and against 2,000 are different runs.
        # `done` counts every Board a shard finished an *attempt* on: Progress.on_board appends
        # to `seconds` before it branches on error, so errored Boards are already inside it.
        # Adding `errors` on top would double-count them and understate the rate.
        attempted = sum(r.done for r in reports)
        rate = (
            f" ({errors / attempted:.1%} of {attempted} attempted)" if attempted else ""
        )
        _log.warning(
            f"{errors} board errors across {len(reports)} shards{rate}: "
            + observability.error_summary(
                {k: v for r in reports for k, v in r.errors.items()}
            )
        )
    if coverage_line:
        _log.info("Board coverage by ATS: " + coverage_line)
        report_verdict = _log.warning if health.degraded else _log.info
        report_verdict(health.verdict_line())
    for loss_line in loss_lines:
        _log.info(loss_line)
    _log.info(
        f"fan-out: {len(reports)} shards | slowest {slowest_seconds / 60:.1f} min "
        f"| worst single board {worst_board:.0f}s | retries {sum(retries.values())}"
        + (f" | actual/predicted {ratio_span}" if ratio_span else "")
    )
    if distinct_ips:
        _log.info(
            # Sampled: the full set ran to ~1,100 addresses, ~15 KB on one line every run.
            f"egress: {len(distinct_ips)} distinct address(es) across {len(reports)} shards, "
            f"colos {log.named_sample(distinct_colos) or '?'}; "
            f"e.g. {log.named_sample(distinct_ips)}"
        )
    observability.summary(
        "Scrape fan-out",
        [
            f"- {lines:,} job lines across {ats_files} ATS files",
            (
                f"- {len(reports)} shards, slowest **{slowest_seconds / 60:.1f} min**, "
                f"worst single board **{worst_board:.0f}s**"
            ),
            f"- {errors} board errors, {sum(retries.values())} retries"
            + (
                f" ({', '.join(f'{k} {v}' for k, v in sorted(retries.items()))})"
                if retries
                else ""
            ),
            f"- **{len(killed)} shard(s) hit the time budget**, deferring {deferred} boards"
            if killed
            else "- no shard hit its time budget",
        ]
        + (
            [
                f"- **{health.verdict_line()}**",
                f"- Board coverage by ATS: {coverage_line}",
            ]
            if coverage_line
            else []
        )
        + [f"- {line}" for line in loss_lines]
        + ([f"- actual/predicted **{ratio_span}**"] if ratio_span else []),
    )


if __name__ == "__main__":
    raise SystemExit(main())
