"""Decide when each Job version counted, under today's rules: the second step of a Restatement
(ADR-0330).

A Restatement answers what Trends would have shown had today's rules always applied, so a rule
applies to the whole past alike:

* **the keep-set**: a version on a Board outside today's keep-set (parked, excluded, aliased,
  disabled, dead) never counts, so removing a Board removes it from the past too;
* **the tech filter**: a version whose raw fields today's filter rejects never counts;
* **the English gate**: a version whose title and description today's gate reads as not English
  never counts, since ``embed_plan`` embeds only English Jobs and the index serves only those.
  The facts keep no text, so it is judged on the description the store holds now;
* **the grace period** (ADR-0083): a Job the scrape stopped returning still counts until its
  Board's next authoritative read, or until it is listed again if that comes first, as ``index
  sync`` serves it. A version that ended ``changed`` or ``off_board`` stops counting when it ended;
* **Dormant Boards** (ADR-0250): while every Job listed on a Board is dated and the newest is more
  than two years older than the run, none of its Jobs counts. Judged over every listed Job, tech
  or not, at each authoritative read of the Board, as ``scrape_join`` judges it. A Job first
  listed while its Board is Dormant never counts; one already counting keeps counting until the
  Board's next authoritative read, as ``index sync`` evicts it after its grace period. An
  interval Dormancy cuts ends ``dormant``; one that resumes when the Board posts again starts
  ``revived``.

Each counted version becomes a **served interval**, ``[served_from, served_to)`` in runs, with
``served_to`` None while it still counts. Last, :func:`fold_duplicates` folds each duplicate group
``index prune`` would collapse into one Job over time (``dedup_group``), counting only the member
the index serves at each moment.

Runs inside a Restatement; it is not a pipeline stage.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Collection, Mapping
from datetime import UTC, date, datetime, timedelta

from headstart.boards.board_identity import lower_key
from headstart.ingest import board_dormancy
from headstart.ingest.index_plan import resolve_board

#: How a version's raw fields read as tech: ``tech_filter.is_tech`` in production.
TechTest = Callable[[str | None, str | None], bool]

#: Whether a title and description read as English: ``doc_prep.is_english`` in production.
EnglishTest = Callable[[str, str], bool]


def _in_scope_reads(reads) -> dict[str, list[str]]:
    """``{case-folded Board: the runs, in order, whose read of it was authoritative}``."""
    by_board: dict[str, list[str]] = {}
    if reads is None:
        return by_board
    for board, run, in_scope in zip(
        reads["board"].to_pylist(),
        reads["run"].to_pylist(),
        reads["in_scope"].to_pylist(),
        strict=True,
    ):
        if board is not None and in_scope:
            by_board.setdefault(lower_key(board), []).append(run)
    for runs in by_board.values():
        runs.sort()
    return by_board


def _next_after(runs: list[str], run: str) -> str | None:
    at = bisect_right(runs, run)
    return runs[at] if at < len(runs) else None


def served_intervals(
    versions,
    reads,
    *,
    is_tech: TechTest,
    live: Mapping[str, str],
    keep_set: Collection[str] | None,
):
    """The versions that count under today's rules, each with ``served_from`` and ``served_to``,
    and its Board re-resolved through ``live`` (``index_plan.boards_by_canon`` of today's
    keep-set). ``keep_set`` holds today's case-folded kept Boards; None keeps every Board, for a
    Restatement without a ledger to trust."""
    import pyarrow as pa

    ids = versions["id"].to_pylist()
    valid_from = versions["valid_from"].to_pylist()
    valid_to = versions["valid_to"].to_pylist()
    ended_as = versions["ended_as"].to_pylist()
    titles = versions["title"].to_pylist()
    departments = versions["department"].to_pylist()

    boards = [resolve_board(job_id, live) for job_id in ids]
    reads_of = _in_scope_reads(reads)
    # The same Job's next version, if any: listed again ends the grace period at once.
    next_from: list[str | None] = [None] * len(ids)
    for i in range(len(ids) - 1):
        if ids[i + 1] == ids[i]:
            next_from[i] = valid_from[i + 1]

    keep = []
    served_to: list[str | None] = []
    for i, board in enumerate(boards):
        if keep_set is not None and lower_key(board) not in keep_set:
            continue
        if not is_tech(titles[i], departments[i]):
            continue
        end = valid_to[i]
        if ended_as[i] == "unlisted":
            second_absence = _next_after(reads_of.get(lower_key(board), []), end)
            candidates = [r for r in (second_absence, next_from[i]) if r is not None]
            end = min(candidates) if candidates else None
        keep.append(i)
        served_to.append(end)

    served = versions.take(pa.array(keep, pa.int64()))
    served = served.set_column(
        served.schema.get_field_index("board"),
        "board",
        pa.array([boards[i] for i in keep], pa.string()),
    )
    return served.append_column("served_from", served["valid_from"]).append_column(
        "served_to", pa.array(served_to, pa.string())
    )


def _dormant_from(newest: str, reads: list[str], since: str) -> str | None:
    """The first of a Board's authoritative ``reads`` at or after ``since`` that judges it Dormant
    when its newest posting is ``newest``, as ``PostedDates.dormant`` judges it on the run's day."""
    first_day = (
        date.fromisoformat(newest) + board_dormancy.DORMANT_AFTER + timedelta(days=1)
    )
    threshold = max(
        since,
        datetime(
            first_day.year, first_day.month, first_day.day, tzinfo=UTC
        ).isoformat(),
    )
    at = bisect_left(reads, threshold)
    return reads[at] if at < len(reads) else None


#: A stretch a Board was Dormant: the read that judged it so, the next authoritative read (when
#: the Jobs it already served leave the index), and the run it posted again (None while it has
#: not).
DormantPeriod = tuple[str, str | None, str | None]


def dormant_periods(
    versions, reads, live: Mapping[str, str]
) -> dict[str, list[DormantPeriod]]:
    """``{case-folded Board: [its Dormant periods]}`` under today's rule, judged at each
    authoritative read over every version listed on the Board then."""
    import pyarrow.compute as pc

    # Dormancy is Board-local. Partition this narrow all-Job input, including non-tech;
    # served intervals remain global for cross-ATS duplicate folding afterwards.
    ats_keys = pc.unique(
        pc.list_element(pc.split_pattern(versions["id"], pattern=":"), 0)
    ).to_pylist()
    periods = {}
    for ats in ats_keys:
        subset = versions.filter(pc.starts_with(versions["id"], ats + ":"))
        periods.update(_dormant_periods(subset, reads, live))
    return periods


def _dormant_periods(versions, reads, live):
    reads_of = _in_scope_reads(reads)
    by_board: dict[str, list[tuple[str, str | None, str, object]]] = {}
    for batch in versions.select(
        ["id", "valid_from", "valid_to", "posted_at"]
    ).to_batches(max_chunksize=8192):
        for row in batch.to_pylist():
            board = lower_key(resolve_board(row["id"], live))
            by_board.setdefault(board, []).append(
                (row["valid_from"], row["valid_to"], row["id"], row["posted_at"])
            )

    periods: dict[str, list[DormantPeriod]] = {}
    for board, listed in by_board.items():
        board_reads = reads_of.get(board, [])
        bounds = sorted({v[0] for v in listed} | {v[1] for v in listed if v[1]})
        found: list[DormantPeriod] = []
        for k, start in enumerate(bounds):
            end = bounds[k + 1] if k + 1 < len(bounds) else None
            dates = board_dormancy.PostedDates()
            for vfrom, vto, _, posted in listed:
                if vfrom <= start and (vto is None or vto > start):
                    dates.see(board, posted)
            newest = dates.newest(board)
            if newest is None:
                continue
            onset = _dormant_from(newest, board_reads, start)
            if onset is not None and (end is None or onset < end):
                if found and found[-1][2] == onset:
                    found[-1] = (*found[-1][:2], end)
                else:
                    found.append((onset, _next_after(board_reads, onset), end))
        if found:
            periods[board] = found
    return periods


def clip_dormant(served, periods: Mapping[str, list[DormantPeriod]]):
    """``served`` with each interval's Dormant stretches cut out: a cut interval ends
    ``dormant``, and the part after a Dormant stretch starts ``revived``. An interval that began
    before its Board was judged Dormant is cut from the Board's next authoritative read."""
    import pyarrow as pa

    batches = [
        _clip_dormant_batch(pa.Table.from_batches([batch]), periods)
        for batch in served.to_batches(max_chunksize=8192)
    ]
    if not batches:
        return served.append_column("starts_as", pa.nulls(0, pa.string()))
    return pa.concat_tables(batches)


def _clip_dormant_batch(served, periods):
    import pyarrow as pa

    rows = served.to_pylist()
    out = []
    for row in rows:
        row.setdefault("starts_as", None)
        pieces = [row]
        for onset, evicted, gone_to in periods.get(lower_key(row["board"]), []):
            kept = []
            for piece in pieces:
                start, end = piece["served_from"], piece["served_to"]
                gone_from = onset if start >= onset else evicted
                if (
                    gone_from is None
                    or (gone_to is not None and gone_from >= gone_to)
                    or (end is not None and end <= gone_from)
                    or (gone_to is not None and start >= gone_to)
                ):
                    kept.append(piece)
                    continue
                if start < gone_from:
                    kept.append(piece | {"served_to": gone_from, "ended_as": "dormant"})
                if gone_to is not None and (end is None or end > gone_to):
                    kept.append(
                        piece | {"served_from": gone_to, "starts_as": "revived"}
                    )
            pieces = kept
        out.extend(pieces)
    if not out:
        return served.slice(0, 0).append_column("starts_as", pa.nulls(0, pa.string()))
    return pa.Table.from_pylist(
        out, schema=served.schema.append(pa.field("starts_as", pa.string()))
    )


def english_only(
    served,
    descriptions: Mapping[str, str],
    is_english: EnglishTest,
    *,
    version_sources=None,
):
    """``served`` less the versions today's English gate holds out of the index, each judged on
    its title and its Job's stored description, as ``embed_plan`` judges a Job."""
    import pyarrow as pa

    version_sources = version_sources or {}
    judged = {}
    keep = []
    starts = (
        served["valid_from"].to_pylist()
        if "valid_from" in served.schema.names
        else [None] * served.num_rows
    )
    for job_id, title, start in zip(
        served["id"].to_pylist(), served["title"].to_pylist(), starts, strict=True
    ):
        text = (
            version_sources.get((job_id, start), (None, descriptions.get(job_id)))[1]
            or ""
        )
        # The version identifies its immutable text; caching whole descriptions would
        # retain the entire disk-backed baseline in RAM again.
        key = (job_id, title or "", start)
        if key not in judged:
            judged[key] = is_english(title or "", text)
        keep.append(judged[key])
    return served.filter(pa.array(keep, pa.bool_()))


def fold_duplicates(served, ranks: Mapping[str, tuple[tuple[str, str], tuple]]):
    """``served`` with each duplicate group folded into one Job over time, and a ``dedup_group``
    naming it on every row. ``ranks`` is ``index_plan.duplicate_ranks`` over the served ids: at every
    moment one served member counts, the one the index serves (:func:`_fold`). A cut
    piece ends ``superseded`` and the member taking over starts ``superseding``, so the group
    counts as one Job across the handover. An id on no live Board is a group of its own."""
    import pyarrow as pa

    rows = served.to_pylist()
    groups: dict[str, list[dict]] = {}
    for row in rows:
        group, _ = ranks.get(row["id"], ((row["id"], ""), ()))
        row["dedup_group"] = "#".join(group) if group[1] else group[0]
        groups.setdefault(row["dedup_group"], []).append(row)

    out: list[dict] = []
    for members in groups.values():
        if len({row["id"] for row in members}) == 1:
            out.extend(members)
            continue
        out.extend(_fold(members, ranks))
    schema = served.schema.append(pa.field("dedup_group", pa.string()))
    if not out:
        return served.slice(0, 0).append_column("dedup_group", pa.nulls(0, pa.string()))
    return pa.Table.from_pylist(out, schema=schema)


def _fold(members: list[dict], ranks) -> list[dict]:
    """One group's rows cut so that one served member counts at each moment, the one the index
    serves: ``index sync`` refuses a copy while an incumbent stands, unless the copy is of a
    better class (a public site before a non-public one, a backing Board before an Eightfold
    site), and a copy on the incumbent's own Board is its other casing, which ``index prune``
    settles by rank. With no incumbent, the best-ranked copy (``index_plan._other_site_copies``)."""
    bounds = sorted(
        {row["served_from"] for row in members}
        | {row["served_to"] for row in members if row["served_to"] is not None}
    )
    pieces: list[list] = []  # [row, start, end]
    holder: dict | None = None
    for k, start in enumerate(bounds):
        end = bounds[k + 1] if k + 1 < len(bounds) else None
        present = [
            row
            for row in members
            if row["served_from"] <= start
            and (row["served_to"] is None or row["served_to"] > start)
        ]
        if not present:
            holder = None
            continue

        def rank(row: dict) -> tuple:
            return ranks[row["id"]][1], row["served_from"]

        best = min(present, key=rank)
        if holder is None:
            inherited = [
                row
                for row in present
                if row.get("baseline_incumbent")
                and row["served_from"] == row["valid_from"]
            ]
            if inherited:
                best = min(inherited, key=rank)
        incumbent = [row for row in present if holder and row["id"] == holder["id"]]
        # The rank's first two keys are the copy's class (index_plan.duplicate_ranks).
        if incumbent and ranks[best["id"]][1][:2] >= ranks[holder["id"]][1][:2]:
            board = lower_key(holder["board"])
            best = min(
                (row for row in present if lower_key(row["board"]) == board), key=rank
            )
        holder = best
        if pieces and pieces[-1][0] is best and pieces[-1][2] == start:
            pieces[-1][2] = end
        else:
            pieces.append([best, start, end])
    folded = []
    for row, start, end in pieces:
        folded.append(
            row
            | {
                "served_from": start,
                "served_to": end,
                "ended_as": row["ended_as"]
                if end == row["served_to"]
                else "superseded",
                "starts_as": row.get("starts_as")
                if start == row["served_from"]
                else "superseding",
            }
        )
    return folded
