"""Pure planners for the search index — what to add, evict, and prune (ADR-0014, ADR-0023) —
and the pure derivations that report on a finished plan.

Everything here computes row sets without touching LanceDB, so the scoping invariants stay
unit-testable on CI's base-deps-only install. :mod:`headstart.ingest.index` is the CLI that opens
the table and executes these plans; ``apply_sync`` is the one mutation helper, shared by its
``sync`` and ``prune`` steps.

Two layers, because they reach different rows:

**Freshness sync** (ADR-0014) — after a scrape, an index row is stale once its Board has been
scraped **twice running** without re-emitting its id (ADR-0083; a single absence only marks it
**Unconfirmed**, because a scrape that came back short is indistinguishable from a closure). New
ids are added; re-seen ids are left untouched (id-only, v1). Crucially the delete is *scoped to
the Boards actually scraped*, so a partial harvest never evicts Boards it didn't touch — and a
dead Board (scraped, yields nothing) has all its rows drop out for free. That scope is per-Board
but all-or-nothing, so a Board whose scrape came back *truncated* still looks fully covered; the
**collapse guard** (ADR-0046, bounded by ADR-0055) caps what a Board may lose in one run at a
quarter of its rows, so a truncation cannot collapse it and a persistently missing row still
drains.



**Prune sweep** (ADR-0023) — because the sync is board-scoped it can't reach rows on Boards that left
the scrape list, nor case-variant duplicates of one job (Workday sites like ``.../External`` vs
``.../external``). These planners compute what to drop in those two cases.

Both layers ask "which Board owns this id", and both answer it through :func:`resolve_board`, whose
docstring says why they must agree (ADR-0049).
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from headstart import log
from headstart.config import load_active_companies
from headstart.corpus import board_of
from headstart.scrapers.registry import get_scraper

_log = log.get(__name__, __spec__)


# A Board losing more than this share of its indexed rows in one run is presumed truncated, not
# delisted; below the floor a large ratio is a handful of rows, which is ordinary churn.
COLLAPSE_RATIO = 0.25
COLLAPSE_FLOOR = 20


@dataclass(frozen=True, slots=True)
class SyncPlan:
    """Ids to add to the index and ids to evict from it, plus what was withheld and why.

    ``held`` pairs a Board key with the number of evictions withheld from it — not its row count.

    ``unconfirmed`` is the grace period's own output (ADR-0083) and is deliberately a *separate*
    field from ``held``: they withhold evictions for unrelated reasons and at different
    granularities. ``held`` is a **Board**-level cap — "this Board may not shed more than a
    quarter of its rows in one run". ``unconfirmed`` is **per-id** evidence — "this id has been
    absent for one scrape of its Board, and needs a second before it is believed". Folding them
    into one number would answer neither question when a Board is being diagnosed.

    The caller persists ``unconfirmed`` and hands it back next run as ``was_unconfirmed``.
    """

    add: frozenset[str]
    delete: frozenset[str]
    held: tuple[tuple[str, int], ...] = ()
    unconfirmed: frozenset[str] = frozenset()


def grace_period_counts(
    was_unconfirmed: AbstractSet[str], fresh: AbstractSet[str], plan: SyncPlan
) -> tuple[int, int]:
    """``(reappeared, still_waiting)`` for the ids the last run left unconfirmed (ADR-0083).

    Public and separate from :func:`sync` so a test can pin the derivation against real
    ``plan_sync`` output. Inlined in ``sync``, the only way to test it was to restate the same
    expressions in the test, which passes just as happily when they are wrong.

    ``reappeared`` is measured against ``fresh`` — "is this id in the scrape we just took?" —
    rather than inferred as the remainder after subtracting the other buckets. Subtraction
    over-counts, and ADR-0083 names exactly why: "An id that reappeared, was pruned, or sat on a
    board that left the ledger is simply not written again." Only the first of those three is a
    reappearance; the other two are rows on their way out via ``plan_prune``'s off-Board sweep,
    and folding them in would report churn that never happened.

    ``still_waiting`` is the carried-in ids that are unconfirmed *again* after this run — they
    neither came back nor were evicted. **This is the accretion signal**, and it has two distinct
    causes that this single number deliberately does not separate: the id's Board was not in this
    run's slice at all (only ~20,000 of ~66,000 live Boards are, so this dominates a healthy set
    and is entirely benign — the streak simply did not advance), or its Board *was* scraped, the
    id was absent again, and the ADR-0046 collapse guard capped its Board's evictions before
    reaching it. The second is the one worth watching: a number that only ever grows is a queue
    nothing looks at, the shape ADR-0055 had to unwind for the guard's own ``held``. Read it
    against the ``collapse guard:`` line in the same log, which names the capped Boards — do not
    read this number alone as "waiting for their Board's next turn".

    Deliberately does *not* return an evicted count. With the grace period on, ``plan.delete`` is
    a subset of ``was_unconfirmed`` by construction (``eligible = absent & previously``), so such
    a number would be exactly the ``evict`` figure the plan line already prints — an intersection
    implying a distinction that cannot exist.
    """
    reappeared = was_unconfirmed & fresh
    # Subtract `fresh`: the two buckets must be disjoint or the line can print more than it
    # carried in. `fresh` is not filtered by `boards`, and ADR-0053 drops an Unauthoritative
    # Board *from* `boards` — so a carried-in id on a scope-excluded Board that genuinely came
    # back is both in `fresh` and re-added by the carry-forward loop, which asks only whether its
    # Board went unscraped. Counting it in each bucket inflates the accretion signal with an id
    # that demonstrably returned.
    return len(reappeared), len(was_unconfirmed & plan.unconfirmed - reappeared)


def plan_sync(
    index_ids: Iterable[str],
    fresh_ids: Iterable[str],
    scraped_boards: Iterable[str],
    live: dict[str, str],
    first_seen: Mapping[str, str] | None = None,
    was_unconfirmed: Iterable[str] | None = None,
) -> SyncPlan:
    """Diff the current index against a scrape's fresh ids, scoped to the Boards it covered.

    ``add`` is the fresh ids not already indexed. ``delete`` is the indexed ids whose Board was
    scraped this run yet are missing from ``fresh_ids`` — a posting that has closed. An indexed id on
    a Board *not* in ``scraped_boards`` is never touched (the partial-harvest safety), and a re-seen
    id (present in both) is left as-is (id-only change detection).

    ``live`` is the :func:`boards_by_canon` lookup ids resolve through; pass an empty dict when
    there is no ledger to read, which degrades to :func:`~headstart.corpus.board_of`. Required
    rather than defaulted: omitting it silently restores the scoping ADR-0049 records as *worse*
    than the bug it fixes.

    **Collapse guard (ADR-0046).** The board-scope check above is all-or-nothing at the *line*
    level: a Board that emitted one job line is fully in scope, so a scrape truncated by a
    rate-limit looks exactly like a Board that delisted everything it didn't re-emit. When a Board
    would lose more than :data:`COLLAPSE_RATIO` of its indexed rows in a single run, this caps what
    it may lose and reports the remainder in ``held`` for the caller to log. Measured over
    five production runs, ordinary turnover is under 10% for 754 of 817 board-runs, while the
    Boards that flapped sat at 35–82% — so the threshold separates them with room to spare.
    Boards under :data:`COLLAPSE_FLOOR` rows are exempt: there a large ratio is a handful of rows.

    The guard is now the **backstop**, not the only line: the scrape reports its per-Board outcomes
    and ``index sync`` keeps the Boards :func:`read_unauthoritative_boards` names out of
    ``scraped_boards`` altogether (ADR-0053). What is left to the ratio is a scraper that cannot
    detect its own truncation and a shard killed before it writes a report. It stays deliberately
    blunt: a Board that genuinely sheds more than a quarter of its postings at once sheds them
    over several runs instead of one.

    **The hold is bounded (ADR-0055).** Refusing a tripped Board's evictions outright made the
    guard a ratchet: nothing else can reach those rows — ``prune`` only evicts ids whose Board is
    absent from the ledger, and a held Board is still Live — so a Board the scrape can never fully
    re-fetch accreted rows forever (production went 267 to 953 withheld across 22 hours, with
    three Boards withholding an identical count every run). A tripped Board now drains its oldest
    missing rows up to :data:`COLLAPSE_RATIO` of its size, so it still cannot collapse in one run
    but the backlog is not permanent. ``held`` reports what was *withheld*, not what was missing.

    ``first_seen`` maps id to its ISO stamp and orders that drain; pass ``None`` and the drain
    falls back to id order, which is stable but carries no age signal. It is a plain mapping so
    this module stays free of any table dependency.

    **The grace period (ADR-0083).** ``was_unconfirmed`` is the ``unconfirmed`` set this function
    returned on the previous run. An id missing from ``fresh_ids`` is only *eligible* for deletion
    if it was already in that set — i.e. it has now been absent for **two consecutive scrapes of
    its own Board**. A first absence is returned in ``unconfirmed`` instead and nothing is deleted
    for it.

    The unit is *scrapes of that Board*, not runs, and that distinction is the whole point: only
    ~20,000 of ~66,000 live Boards are in any run's slice, and ``index sync`` already keeps
    Unauthoritative Boards out of ``scraped_boards`` (ADR-0053) — so a Board this run did not
    read is no evidence either way. Its ids keep their previous state rather than being counted
    as confirmed-present (which would reset the streak and make the grace period unreachable) or
    as absent (which would evict on a Board nobody looked at).

    Measured basis for two rather than three: every false eviction in
    ``docs/pipeline/2026-08-23_false-board-eviction-root-cause.md`` was a *single isolated* miss.
    The one id evicted twice (``successfactors:careers.hcltech.com:1364226855``) was verified
    present in the scrape between its two evictions — and the mechanics force that, since a
    second eviction requires a re-add, which requires reappearing in ``fresh_ids``.

    ``None`` and an empty set are **opposites** here, and the distinction is load-bearing.
    ``None`` disables the grace period entirely, restoring the previous evict-on-first-absence
    behaviour; it exists for callers written before this and is not what the pipeline passes. An
    empty set means the grace period is *on* and nothing is owed a second look yet — which is the
    cold-start path, since ``index sync`` reads a missing state file as an empty set. Cold start
    is therefore safe by construction: nothing is eligible, so that run deletes nothing and merely
    records what was absent.
    """
    index = set(index_ids)
    fresh = set(fresh_ids)
    boards = set(scraped_boards)
    add = fresh - index
    # None means "no grace period" (the pre-ADR-0083 behaviour); an empty set means "the grace
    # period is on and nothing is owed a second look yet". Those are different, so the None check
    # cannot collapse into `or set()`.
    grace_on = was_unconfirmed is not None
    previously = set(was_unconfirmed or ())

    indexed_by_board: dict[str, set[str]] = defaultdict(set)
    for job_id in index:
        board = resolve_board(job_id, live)
        if board in boards:
            indexed_by_board[board].add(job_id)

    stamps = first_seen or {}
    delete: set[str] = set()
    held: list[tuple[str, int]] = []
    unconfirmed: set[str] = set()
    for board, ids in indexed_by_board.items():
        absent = {i for i in ids if i not in fresh}
        # Eligible = absent now *and* absent at this Board's previous scrape. A first absence is
        # not eligible; it lands in `unconfirmed` below and gets one more look. Named apart from
        # `absent` on purpose — they differ by exactly the grace period, and reusing one word for
        # both is how this reads as "missing" twice and means two things.
        eligible = absent & previously if grace_on else absent
        evicted_here: set[str] = set()
        if len(ids) >= COLLAPSE_FLOOR and len(eligible) > COLLAPSE_RATIO * len(ids):
            # Drain at the guard's own threshold rather than refusing outright (ADR-0055). A
            # Board still cannot collapse in one run — which is all ADR-0046 set out to stop —
            # but a row that stays missing does eventually leave, so a Board the scrape can
            # never fully re-fetch stops accreting rows nothing will ever evict.
            # Oldest first, by the `first_seen` the table already carries: a row we have not
            # been able to confirm for longest is the likeliest to be genuinely closed. A
            # missing stamp sorts first — those rows predate the column, so they are the oldest
            # of all — and the id breaks ties so the plan is deterministic.
            # max(1, ...) binds the invariant rather than leaving it to arithmetic: the drain
            # must always take at least one row, or a Board would hold forever again — the exact
            # ratchet this replaces. Unreachable while COLLAPSE_FLOOR is 20 (cap >= 5), but the
            # two constants are declared far apart and nothing else couples them.
            cap = max(1, int(COLLAPSE_RATIO * len(ids)))
            drain = sorted(eligible, key=lambda i: (stamps.get(i) or "", i))[:cap]
            evicted_here = set(drain)
            held.append((board, len(eligible) - len(drain)))
        else:
            evicted_here = eligible
        delete |= evicted_here
        # Everything still absent and still in the table is unconfirmed — first absences *and*
        # the ids the collapse guard just withheld. Carrying the withheld ones matters: they are
        # already past the grace period, and dropping them here would make each read as a fresh
        # first absence next run, resetting the streak so the Board evicts only every other run.
        # Measured on a 200-row Board short by 140 every scrape, that alternated 0, 50, 0, 37 —
        # halving ADR-0055's drain, the ratchet it exists to prevent.
        if grace_on:
            unconfirmed |= absent - evicted_here

    if grace_on:
        # An id whose Board this run did not scrape keeps the state it had: no evidence arrived,
        # so its streak neither advances nor resets. Without this the set would be rebuilt from
        # the slice alone and a Board's ids would silently reset every run it sat out — with
        # ~20,000 of ~66,000 Boards scraped per run, most ids would never reach a second absence
        # and the grace period would never evict anything.
        #
        # Carried forward only while the Board is *still live*, which bounds the set. A Board
        # that leaves the ledger is never scraped again, so its entries would otherwise persist
        # for good — the same slow accretion ADR-0055 had to unwind for `held`. Those rows leave
        # the index through `plan_prune`'s off-Board sweep, and their entries leave here with
        # them. The check is per-Board, not per-id, so it costs a handful of lookups rather than
        # an intersection against the whole index.
        for job_id in previously:
            board = resolve_board(job_id, live)
            if board not in boards and board.lower() in live:
                unconfirmed.add(job_id)

    return SyncPlan(
        add=frozenset(add),
        delete=frozenset(delete),
        held=tuple(sorted(held)),
        unconfirmed=frozenset(unconfirmed),
    )


def _quote(value: str) -> str:
    return (
        "'" + value.replace("'", "''") + "'"
    )  # SQL string literal for the delete predicate


def in_predicate(column: str, values: Iterable[str]) -> str:
    """``column IN ('a', 'b')`` over quoted literals, sorted so the predicate is stable.

    Lives here beside :func:`_quote`, which it uses, rather than being rebuilt by each caller —
    the escaping is the part worth having exactly one of.
    """
    return f"{column} IN ({', '.join(_quote(v) for v in sorted(values))})"


def apply_sync(
    table: Any,
    add_rows: list[dict],
    delete_ids: Iterable[str],
    *,
    chunk: int = 2048,
) -> None:
    """Execute a plan on a LanceDB ``table``: delete evicted ids by predicate, then add new rows.

    Deletes are chunked so the ``id IN (...)`` predicate can't grow unbounded. ``add_rows`` must
    already carry the table's schema (vector + metadata); embedding them is the caller's job.

    **The chunk is 2048, not 512, because each call is far more expensive than the predicate it
    carries.** Lance writes one deletion file per *fragment* a delete touches, and the ids in any
    chunk are scattered across every fragment — so the files written by a run are
    ``ceil(deleted / chunk) x fragments``, and quartering the call count quarters the files. At
    ~40 characters per id (``smartrecruiters:Nagarro1:744000144258659``) the predicate goes from
    ~21 KB to ~86 KB, which is a string DataFusion parses once against a scan of every fragment
    that the call was going to perform anyway — so fewer, larger calls are also fewer full scans.

    This only divides the constant. The term that actually grows is the fragment count, which
    climbs every run and is reset only by ``index compact``; 512 -> 2048 turns "the 10,000-file
    directory ceiling in ~3 days" into "~12 days", it does not remove the ceiling.
    """
    ids = list(delete_ids)
    for start in range(0, len(ids), chunk):
        batch = ids[start : start + chunk]
        table.delete(in_predicate("id", batch))
    if add_rows:
        table.add(add_rows)


def live_keep_set(ledger_dir: str | Path) -> set[str]:
    """Board keys that should survive: every live ledger Board on an enabled ATS, each key exactly
    as its scraper's ``board_key()`` builds it — the real keys ids carry, which is what makes
    prefix-matching them exact (ADR-0049). ``load_active_companies`` already drops dead Boards and
    ``DISABLED_ATS``; ``min_jobs=0`` keeps currently-empty live Boards.

    Kept in the ledger's **own casing**, not lowercased: :func:`plan_prune` matches Boards
    case-insensitively but needs the exact live casing to pick which duplicate row to keep."""
    keep: set[str] = set()
    for company in load_active_companies(ledger_dir, min_jobs=0):
        try:
            keep.add(get_scraper(company.ats, company.slug, company.name).board_key())
        except Exception:  # noqa: BLE001, S112 - a malformed ledger row shouldn't sink the whole set
            continue
    return keep


def _live_board_end(job_id: str, live: dict[str, str]) -> int | None:
    """Index of the colon separating a live Board prefix from the native id, or None if the id is
    on no live Board.

    Returns a **position in the original string**, not a length: ``live``'s keys are lowercased and
    ``str.lower()`` is not length-preserving for every character (``'İ'.lower()`` is two chars), so
    slicing the original id by the lowercased key's length would silently eat a character of the
    native id — on the eviction path, that is a live row deleted as someone else's duplicate.

    Longest match wins as defence in depth rather than because anything needs it today: no two live
    Board keys currently nest at a colon, so first-match would give the same answer. (Workday's
    ``co/site`` tenants nest at a *slash*, which is never a candidate position.) Taking the longest
    keeps the answer right if a Board key ever gains a colon.
    """
    best: int | None = None
    for pos, char in enumerate(job_id):
        if char == ":" and job_id[:pos].lower() in live:
            best = pos
    return best


def boards_by_canon(keep: Iterable[str]) -> dict[str, str]:
    """``{canonical (lowercased) Board: the live casing}`` — the lookup both planners match ids
    against.

    The lex-min tie-break is defensive, not the decision: a production ``keep`` already holds one
    casing per Board, because ``live_keep_set`` reads the list ``config._dedupe_boards`` has
    collapsed — the same list the scrape works from, which is *why* the casing prune keeps is the
    casing a scrape emits. It matters only for a caller assembling ``keep`` some other way, where an
    arbitrary set order must not be able to change the plan.
    """
    live: dict[str, str] = {}
    for board in sorted(keep):  # sorted so a caller's set order can't change the plan
        live.setdefault(board.lower(), board)
    return live


def read_unauthoritative_boards(path: str | Path) -> dict[str, str]:
    """Boards whose scraped list is not authoritative this run — it came back truncated, or the
    scrape raised — lowercased for matching, mapped to *why* (ADR-0053).

    Written by ``scrape_join.write_unauthoritative_boards``; ``index sync`` drops these from the
    eviction scope so a truncated scrape cannot read as a delisting. Lives here rather than beside
    its caller so the scoping invariants stay unit-testable on CI's base-deps-only install.

    Returns the reason, not just the Board, because the exclusion warning is the only place the
    outcome surfaces: without it a Board reads as excluded for an unknown one of two very different
    causes, and telling a 429 apart from a short page needs the log line to say which. Callers that
    only want membership can still use ``in``.

    Fails **open** — an unreadable or wrong-shaped file yields an empty mapping, restoring the old
    infer-from-lines behaviour. Failing the other way would freeze eviction across the whole index
    on a bad file. The shape check is not paranoia: JSON's top level may legally be a list or a
    string, and iterating either yields items that are not Board keys — ``"abc"`` would quietly
    protect Boards ``a``, ``b`` and ``c``, and ``[1, 2]`` would raise on ``.lower()``.
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - telemetry must never stop the index updating
        _log.warning(
            f"unreadable {p}: {exc} — no Board is protected from eviction this run"
        )
        return {}
    if not isinstance(data, dict):
        _log.warning(
            f"{p} holds {type(data).__name__}, expected an object of Board -> reason — "
            "no Board is protected from eviction this run"
        )
        return {}
    return {str(k).lower(): str(v) for k, v in data.items()}


def resolve_board(job_id: str, live: dict[str, str]) -> str:
    """The live Board that owns ``job_id``, falling back to :func:`board_of`'s guess when none
    matches (an id on a Board the ledger doesn't list — a fresh discovery, or a disabled ATS).

    Both planners resolve through this so they agree. They must: sync scopes eviction by Board and
    prune classifies by Board, and when the two disagreed about a colon-bearing id, a *closed*
    posting became unreachable by both — prune saw it on a live Board and left it, while sync's
    scope only ever held the phantom Board, which no fresh sibling recreates once the req closes.

    Returns the Board in the **id's own casing**, not the ledger's. Sync pairs indexed ids against
    the Boards a scrape emitted, and a scrape emits the live casing, so a fossil-cased row resolves
    to a Board absent from that scope and sync leaves it alone — which is the partial-harvest
    protection :func:`plan_prune` documents and relies on. Canonicalising to ``live[canon]`` here
    would fold fossils onto the live Board and let sync evict them as stale, quietly taking over
    the job prune does under its own keep-set guard.
    """
    end = _live_board_end(job_id, live)
    return job_id[:end] if end is not None else board_of(job_id)


def plan_prune(index_ids: Iterable[str], keep: set[str]) -> tuple[list[str], list[str]]:
    """Split index ids into ``(evict_off_board, evict_duplicate)``.

    ``evict_off_board``: Board not in ``keep`` (dead / dropped from the ledger / disabled ATS).
    ``evict_duplicate``: among the survivors, every id but one per ``(lowercased Board, native id)``
    group — the case-variant dupes of one job.

    The row kept is the one whose Board casing the **live ledger** produces, because that is the
    casing a future scrape emits. Keeping the lexicographically-smallest instead (the rule until
    2026-08-11) preserved whichever casing happened to sort first, which is often a *fossil* — a row
    under a casing nothing scrapes any more. Sync cannot evict a fossil (its Board is absent from
    ``scraped_boards``, so the partial-harvest guard protects it), so the fresh row was deleted as
    the fossil's duplicate on every run, forever, while the fossil itself went stale and immortal.
    Falls back to lex-min when no row carries the live casing (the group is all fossils).

    Ids are matched against ``keep`` by **prefix**, not by parsing (ADR-0049): a composite key is
    ``{ats}:{slug}:{native}`` and *both* the slug and the native id can contain ``:``, so splitting
    on the last colon attributes some rows to a Board that does not exist. ``keep`` is the set of
    real Boards, so the longest member that prefixes an id is the answer — unambiguous against
    every Board key in use today, with longest-match as the documented tie-break should one Board
    key ever nest inside another at a colon."""
    live = boards_by_canon(keep)
    off_board: list[str] = []
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for jid in index_ids:
        end = _live_board_end(jid, live)
        if end is None:
            off_board.append(jid)
            continue
        canon, native = jid[:end].lower(), jid[end + 1 :]
        groups[(canon, native)].append(jid)
    duplicate: list[str] = []
    for (canon, _), ids in groups.items():
        if len(ids) > 1:
            kept = next((i for i in ids if i.startswith(live[canon] + ":")), min(ids))
            duplicate.extend(i for i in ids if i != kept)
    return off_board, duplicate
