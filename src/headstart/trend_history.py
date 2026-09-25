"""Trends' one owner of its stored history (ADR-0230): :func:`record_tick` writes it, and
:class:`TrendHistory` answers ``/trends`` and the company picker from it. It lives in
``headstart`` proper, not ``ingest``, so the Space and the pipeline can both import it.

The history is the Board-delta ledger (ADR-0143): one file per **Tick** under
``data/state/role_trend_board_deltas/``, written even when nothing moved. A file holds every
**Board delta** of its tick as ``(board, metric, family, band, delta)``, then the tick's turnover
(ADR-0227), and carries the tick's ``ts`` and **Methodology** in its metadata. A Board's ATS is
its board_key's prefix. Summed in order, the files give every group's count at every tick, for
the whole index or for any set of Boards. A new classifier head is one more delta, so the history
has no series versions: a tick whose Methodology differs from the tick before it is a
**Counting change**, and that is all a re-base now is.

The ticks before per-Board counting began on 2026-09-13 exist only index-wide, in the
**archive** ``role_trend_index_deltas_before_board_deltas.parquet``: ``(ts, metric, family, band,
ats, delta)`` with the Methodology they were counted under in its metadata.

Until ``scripts/state/migrate_trends_to_one_delta_history.py`` has rewritten the stored files
(ADR-0230 step 6), the dataset holds them in the layout before this one: re-bases stored as
baselines, the Methodology of older ticks in ``trends_epochs.csv``, and the archive only inside
the aggregate ledger ``role_trends.parquet``. The reader and the writer read that layout through
:mod:`headstart.trend_history_migration`, which rewrites it in memory exactly as the script
rewrites it on disk.

Netting happens in :meth:`TrendHistory.answer` (step 4), and the Hot tab ranks companies off
:meth:`TrendHistory.company_moves`, which reads the same answers (step 5). ``trend_reading``
reads the answer before netting (:meth:`TrendHistory.unnetted_answer`) into reconciled line
readings (ADR-0233).
"""

from __future__ import annotations

import csv
import json
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from headstart import company_match, trend_netting
from headstart.board_identity import ats_of
from headstart.roles import BAND_LABELS, NON_TECH, WATCH_PREFIX

# The `new` flow window (ADR-0051), in days: how long a found Board's backlog is held out of
# `new`, and how far a `new` view's counting changes echo. The pipeline counts `new` over the
# same week (`ingest.role_trends.NEW_WINDOW_DAYS`).
NEW_WINDOW_DAYS = 7

# A tick file's level metrics, in the order the aggregate writes them: a Board's `stock` and `new`
# counts, whose deltas sum to a level. Since ADR-0227 a file also carries the tick's turnover,
# which is a count of jobs, not a change in a level, and its Unauthoritative-Board markers. They
# mirror ingest.job_turnover.METRICS and ingest.job_turnover.UNSCOPED.
LEVEL_METRICS = ("new", "stock")
_TURNOVER_METRICS = ("opened", "closed", "recounted_in", "recounted_out")
_UNSCOPED = "unscoped"
# What /trends serves per line: recounted as in less out, so opened − closed + recounted is the
# line's change in openings on every run.
_TURNOVER_KINDS = ("opened", "closed", "recounted")
_TURNOVER_KIND_OF = {
    "opened": ("opened", 1),
    "closed": ("closed", 1),
    "recounted_in": ("recounted", 1),
    "recounted_out": ("recounted", -1),
}

DELTAS = "role_trend_board_deltas"
ARCHIVE = "role_trend_index_deltas_before_board_deltas.parquet"
# A tick file's columns, and the archive's. The archive keeps `ats`: an index-wide row has no
# Board to read it from.
TICK_COLUMNS = ("board", "metric", "family", "band", "delta")
ARCHIVE_COLUMNS = ("ts", "metric", "family", "band", "ats", "delta")
_DEDUP_EVICTIONS = "dedup_evictions.csv"
_DIRECTORY = "company_directory.json"

# Each Methodology field under the name the payload's `epochs[].fields` gives it, which is the
# retired epoch ledger's column name (ADR-0164), and what a chart says when it moves.
_EPOCH_LABELS = (
    ("family_map_fingerprint", "role family map edited"),
    ("tech_filter_version", "tech filter changed"),
    ("derivations_version", "experience/salary extraction changed"),
    ("dedup_version", "duplicate removal changed"),
    ("family_classifier_version", "role family assignment changed"),
)
_METHODOLOGY_COLUMNS = (
    ("family_map_fingerprint", "family_list_fingerprint"),
    ("tech_filter_version", "tech_filter_version"),
    ("derivations_version", "derivations_version"),
    ("dedup_version", "dedup_version"),
    ("family_classifier_version", "family_classifier_version"),
)


class TrendsUnavailable(LookupError):
    """What a question needs is not on this deployment yet: no history, or no company directory.
    The Space answers 503, so the tab stays dark rather than broken."""


@dataclass(frozen=True)
class TrendQuestion:
    """One ``/trends`` request, as the page sends it. Values are the raw query strings;
    :meth:`TrendHistory.answer` validates them."""

    metric: str = "stock"
    coverage: str = "all"
    family: str | None = None
    split: str = "bands"
    companies: tuple[str, ...] = ()
    since: str | None = None
    until: str | None = None
    base: str | None = None
    ats: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompanyMove:
    """One company's week, read off its own netted Trends line (:meth:`TrendHistory.company_moves`).

    ``net`` is the change in its tech openings since the window's base with the steps that are
    not hiring taken out; ``opened`` and ``closed`` are the jobs it opened and closed over the runs
    that change counts (ADR-0227); ``counted_since`` is the first tick that counted any of its
    Boards."""

    net: int
    opened: int
    closed: int
    counted_since: str


@dataclass(frozen=True)
class CompanyMoves:
    """Hot's figures (ADR-0230): every company asked about, and the window they cover as
    ``{base, from, to, turnover_from}``. ``base`` is the tick each change is measured from, where
    a Hot row's "See trend" opens; ``from`` and ``to`` are the first and last ticks measured after
    it; ``turnover_from`` is the first of them with turnover booked, None before ADR-0227's data."""

    window: dict[str, str | None]
    moves: dict[str, CompanyMove]


class _Names:
    """A list of names, each with a stable code, shared by every table the history encodes.
    ``dtype`` holds every code: Boards run to tens of thousands, families and bands to tens."""

    def __init__(self, dtype=np.int16) -> None:
        self.names: list[str] = []
        self._codes: dict[str, int] = {}
        self._dtype = dtype

    def get(self, name: str) -> int | None:
        """``name``'s code, or None when no table holds it."""
        return self._codes.get(name)

    def code(self, name: str) -> int:
        """``name``'s code, given one if it has none yet."""
        if name not in self._codes:
            self._codes[name] = len(self.names)
            self.names.append(name)
        return self._codes[name]

    def encode(self, column) -> np.ndarray:
        """``column`` (an Arrow string array or column, plain or dictionary-encoded) as
        codes."""
        import pyarrow as pa

        parts = [np.zeros(0, dtype=self._dtype)]
        for chunk in getattr(column, "chunks", [column]):
            if not pa.types.is_dictionary(chunk.type):
                chunk = chunk.dictionary_encode()
            remap = np.array(
                [self.code(name) for name in chunk.dictionary.to_pylist()],
                dtype=self._dtype,
            )
            parts.append(remap[chunk.indices.to_numpy(zero_copy_only=False)])
        return np.concatenate(parts)

    def ranks(self) -> np.ndarray:
        """Each code's position in name order."""
        order = sorted(range(len(self.names)), key=self.names.__getitem__)
        ranks = np.empty(len(order), dtype=np.int64)
        ranks[order] = np.arange(len(order))
        return ranks


def family_successors(path: Path) -> dict[str, str]:
    """Each retired family's v3 successor (ADR-0220), from `retired` in the curated map: the
    data carries the old names until the new classifier's series lands, and links made before
    carry them after."""
    if not path.exists():
        return {}
    spec = json.loads(path.read_text(encoding="utf-8"))
    return {
        f["name"]: f["successor"] for f in spec.get("retired", []) if f.get("successor")
    }


def predecessors(family: str, successors: dict[str, str]) -> list[str]:
    """The retired families whose successor is ``family`` (ADR-0220): one step, as the Trends
    rename takes it, so Search's category and the Trends line for it sum the same names."""
    return [old for old, new in successors.items() if new == family]


def watched_roles(path: Path) -> dict[str, dict[str, str]]:
    """``{watch:name: {label, parent, match}}`` from the curated watchlist under config/
    (ADR-0051), like the family map. Missing file means no watch roles — older deploys."""
    if not path.exists():
        return {}
    spec = json.loads(path.read_text(encoding="utf-8"))
    return {
        WATCH_PREFIX + r["name"]: {
            "label": r.get("label", r["name"]),
            "parent": r["parent"],
            # the title patterns a role is counted by, so its jobs can be handed to Search
            "match": r.get("match", []),
        }
        for r in spec["roles"]
    }


def _family_labels(path: Path) -> dict[str, str]:
    """Display names, from the curated map under config/ (ADR-0040). The ledger stores slugs
    so a label can be reworded without breaking a series; this resolves them."""
    if not path.exists():
        return {}
    spec = json.loads(path.read_text(encoding="utf-8"))
    # `retired` names the families before ADR-0220, whose series the Space still serves until a
    # new head's title cache is warm and the first series under it lands.
    families = [
        *spec.get("retired", []),
        *spec["families"],
    ]  # a listed family's label wins
    return {f["name"]: f.get("label", f["name"]) for f in families}


def _counting_changes(stamps: list[dict]) -> list[dict]:
    """Methodology boundaries (ADR-0164): every stamp after the first names what changed since
    the one before it, so a chart can mark the point and a reader isn't left decoding raw
    version integers. The first stamp is a baseline, not a boundary — there is nothing before it
    to contrast against, so it names nothing and is dropped rather than emitted empty.
    """
    out = []
    for previous, row in zip([None, *stamps], stamps):
        if previous is None:
            continue
        moved = [
            (key, label)
            for key, label in _EPOCH_LABELS
            if row.get(key) != previous.get(key)
        ]
        if moved:
            # `fields` beside the labels, so code can key on what moved (the Trends tab asks
            # whether duplicate removal did) without matching prose someone may reword.
            out.append(
                {
                    "ts": row["ts"],
                    "changed": [label for _, label in moved],
                    "fields": [key for key, _ in moved],
                }
            )
    return out


def _methodology_stamps(stamped: list[tuple[str, dict]]) -> list[dict]:
    """Every tick's Methodology, oldest first, under the field names the payload gives them."""
    return [
        {
            "ts": ts,
            **{
                column: str(methodology.get(key))
                for column, key in _METHODOLOGY_COLUMNS
            },
        }
        for ts, methodology in stamped
    ]


def _load_evictions(path: Path) -> dict[str, list[tuple[str, int]]]:
    """``board -> [(ts, rows removed)]`` from the duplicate-removal ledger (#649), or empty.

    Its ``ts`` is the run's own stamp, the one role_trends writes, so a removal lands exactly
    on a charted run. Rows removed as duplicates are not closures, and a company's line leaves
    them out; the rule that removed them does not matter to that, so it is summed away."""
    if not path.exists():
        return {}
    out: dict[str, Counter] = defaultdict(Counter)
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                out[row["board"]][row["ts"]] += int(row["count"])
    except (OSError, ValueError, KeyError, TypeError, csv.Error) as exc:
        print(f"dedup evictions unreadable ({exc}); none left out", flush=True)
        return {}
    return {board: sorted(by_ts.items()) for board, by_ts in out.items()}


def _load_directory(path: Path) -> dict[str, dict]:
    """The company directory (ADR-0185) as ``{company key: {name, boards, operator}}``, or ``{}``.

    A company's key is its first board_key, and any of its Boards resolves to it, so a Hot-tab
    row or a search result links to its company by the Board it already carries. Absent or
    half-written means no picker, never a failed boot.
    """
    if not path.exists():
        return {}
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))["companies"]
        return {entry["boards"][0]: entry for entry in entries}
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return {}


def _held_at_zero(values: list[int | None], metric: str | None) -> list[int | None]:
    """A stock series at 0, not unmeasured, at every charted run after it first appears.

    Every charted run measured stock, and the ledgers write only non-empty groups, so a series
    absent from a run held none there. Left as gaps, a category a refit emptied showed its last
    count as its latest and never booked the drop — Syms' systems engineering read 46 in the
    table beside 0 in the legend. `new` keeps its own rule (``value_at``), and so does the
    index chart with no pick (``metric`` None): a family a version stops writing there is a
    taxonomy change the chart marks, not a fall to zero."""
    if metric != "stock":
        return values
    out, seen = [], False
    for value in values:
        seen = seen or value is not None
        out.append(0 if seen and value is None else value)
    return out


def _family_weights(rows: list[dict]) -> Counter[str]:
    """Openings per family over ``rows`` — how much of the data each name holds."""
    weights: Counter[str] = Counter()
    for row in rows:
        if row["metric"] in LEVEL_METRICS:
            weights[row["family"]] += row["count"]
    return weights


def _resolve_family(
    family: str | None, present: Counter[str], successors: dict[str, str]
) -> str | None:
    """``family`` as the data holds it: itself, its successor, or its largest predecessor there
    (``present`` counts rows per family) — "AI, ML & Data Science" before its data lands reads
    as "AI / Machine Learning", not as its smaller half, Data Science."""
    if not family or family in present:
        return family
    successor = successors.get(family)
    if successor in present:
        return successor
    older = [old for old in predecessors(family, successors) if old in present]
    return max(older, key=lambda old: present[old]) if older else family


def _in_ats_scope(board: str, ats: list[str]) -> bool:
    """Whether ``board`` is inside a Trends request's ATS selection (ADR-0075); no selection
    means every ATS."""
    return not ats or ats_of(board) in ats


def _index_turnover(by_board: dict[str, list[dict]], touched_of) -> list[dict]:
    """Every Board's turnover summed per tick, metric, family, band, ATS and whether duplicate
    removal can move it (ADR-0227): what the Trends view with no company picked draws. A few
    hundred rows a tick where the per-Board rows run to thousands, so a request sums the index
    without walking every Board. ``touched_of(board)`` says whether duplicate removal can move the
    company holding ``board``, the rule a company's own view leaves runs out by."""
    summed: Counter[tuple[str, str, str, str, str, bool]] = Counter()
    for board, rows in by_board.items():
        touched = touched_of(board)
        for r in rows:
            key = (r["ts"], r["metric"], r["family"], r["band"], r["ats"], touched)
            summed[key] += r["delta"]
    fields = ("ts", "metric", "family", "band", "ats", "touched")
    return [{**dict(zip(fields, key)), "delta": n} for key, n in summed.items()]


def _norm_stamp(raw: str) -> str:
    """``raw`` re-shaped to exactly how the ledger stores ``ts`` (``+00:00``, whole seconds). A
    naive string compare against the browser's ``Date.toISOString()`` (milliseconds, a ``Z``
    suffix) would misorder a value naming the exact same instant as a stamp, since ``'.'`` and
    ``'+'`` sort differently. ``fromisoformat`` already parses a trailing ``Z`` natively (3.11+),
    and a naive value is read as UTC, matching the ledger."""
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds")


# Each column the history holds, and its type: a metric code is 0 for `new` and 1 for `stock`.
_INDEX_TYPES = {
    "tick": np.int32,
    "metric": np.int8,
    "family": np.int16,
    "band": np.int16,
    "ats": np.int16,
    "count": np.int32,
}
_DELTA_TYPES = {
    "tick": np.int32,
    "board": np.int32,
    "metric": np.int8,
    "family": np.int16,
    "band": np.int16,
    "ats": np.int16,
    "delta": np.int32,
}


def _columns(types: dict, **values: np.ndarray) -> dict[str, np.ndarray]:
    """``values`` cast to ``types``' columns; a column not given is empty."""
    return {
        name: np.asarray(values.get(name, ()), dtype=kind)
        for name, kind in types.items()
    }


def _metric_codes(column) -> np.ndarray:
    """``column``'s metrics as codes: 0 for `new`, 1 for `stock`, and -1 for any other (a
    tick's turnover and markers)."""
    names = _Names()
    codes = names.encode(column)
    remap = np.array(
        [LEVEL_METRICS.index(n) if n in LEVEL_METRICS else -1 for n in names.names],
        dtype=np.int8,
    )
    return remap[codes] if len(remap) else codes.astype(np.int8)


# ---- the stored history: its files, and writing a tick ---------------------------------------


@dataclass(frozen=True)
class Methodology:
    """How one tick was counted (ADR-0164, ADR-0230), stored in the tick's own file. A tick whose
    Methodology differs from the tick before it is a counting change. Ticks from before the
    classifier head (ADR-0220) carry text as its version: ``none`` in the centroid era, and the
    title rules' fingerprint under them."""

    family_list_fingerprint: str
    family_classifier_version: int | str
    tech_filter_version: int
    derivations_version: int
    dedup_version: int


def tick_path(directory: Path, ts: str) -> Path:
    """The file of the tick stamped ``ts``: ``:`` is spelled ``-``, so ``+00:00`` ends
    ``+00-00``."""
    return directory / f"{ts.replace(':', '-')}.parquet"


def _tick_stamp(table) -> str:
    return (table.schema.metadata or {})[b"ts"].decode()


def _tick_tables(state_dir: Path) -> list:
    """Every tick file under ``state_dir`` as a table in this module's layout, oldest first. A
    history still in the layout before ADR-0230 step 6 is rewritten in memory, as the one-off
    migration rewrites it on disk."""
    import pyarrow.parquet as pq

    from headstart import trend_history_migration as migration

    paths = sorted((state_dir / DELTAS).glob("*.parquet"))
    tables = [pq.read_table(path) for path in paths]
    if any(migration.is_old_layout(table.schema) for table in tables):
        tables, _ = migration.rewritten_ticks(tables, state_dir / migration.EPOCHS)
    return sorted(tables, key=_tick_stamp)


def _archive_table(state_dir: Path, before: str | None):
    """The archive: its file, or, while the history is still in the older layout, the aggregate
    ledger's ticks before ``before`` (the first tick file's) as the migration writes them. None
    when neither exists."""
    import pyarrow.parquet as pq

    from headstart import trend_history_migration as migration

    if (state_dir / ARCHIVE).exists():
        return pq.read_table(state_dir / ARCHIVE)
    if not (state_dir / migration.AGGREGATE).exists():
        return None
    return migration.archive_from_aggregate(
        state_dir / migration.AGGREGATE, before, state_dir / migration.EPOCHS
    )


def board_levels(
    state_dir: Path,
) -> tuple[str | None, dict[tuple[str, str, str, str], int]]:
    """The newest tick's stamp, and every ``(board, metric, family, band)`` group's level at it:
    the tick files' level deltas summed. ``(None, {})`` before the first tick."""
    import pyarrow as pa
    import pyarrow.compute as pc

    tables = _tick_tables(state_dir)
    if not tables:
        return None, {}
    rows = pa.concat_tables([table.select(list(TICK_COLUMNS)) for table in tables])
    rows = rows.filter(pc.is_in(rows["metric"], pa.array(LEVEL_METRICS)))
    key = list(TICK_COLUMNS[:-1])
    summed = rows.group_by(key).aggregate([("delta", "sum")])
    columns = [summed[name].to_pylist() for name in (*key, "delta_sum")]
    levels = {tuple(k): n for *k, n in zip(*columns, strict=True) if n}
    return _tick_stamp(tables[-1]), levels


def record_tick(
    state_dir: Path,
    ts: str,
    levels: Mapping[tuple[str, str, str, str], int],
    turnover: Mapping[tuple[str, str, str, str], int],
    methodology: Methodology,
) -> int:
    """Write the tick stamped ``ts`` as one file under ``state_dir``: its level changes against
    the history replayed to its newest tick, then its ``turnover`` and markers (ADR-0227) as
    counts of the tick, with ``methodology`` in the file's metadata. Returns the rows written.

    ``levels`` is every ``(board, metric, family, band)`` group's count now, for the level
    metrics; a group it leaves out is at 0. The file is written even when nothing moved, so the
    history holds one file per tick, and a new classifier head writes a delta like any other tick,
    never a baseline. Raises ValueError when ``ts`` is not newer than the newest tick; an OSError
    propagates. Written beside its path and renamed over it, so a killed run leaves no half."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    newest, current = board_levels(state_dir)
    if newest is not None and ts <= newest:
        raise ValueError(f"tick {ts} is not newer than the history's newest, {newest}")
    rows = [
        (*key, levels.get(key, 0) - current.get(key, 0))
        for key in sorted(levels.keys() | current.keys())
        if levels.get(key, 0) != current.get(key, 0)
    ] + sorted((*key, n) for key, n in turnover.items())
    columns = list(zip(*rows, strict=True)) if rows else [()] * len(TICK_COLUMNS)
    schema = pa.schema(
        [
            (name, pa.int64() if name == "delta" else pa.string())
            for name in TICK_COLUMNS
        ],
        metadata={
            b"ts": ts.encode(),
            b"methodology": json.dumps(asdict(methodology), sort_keys=True).encode(),
        },
    )
    table = pa.table(
        {
            name: list(column)
            for name, column in zip(TICK_COLUMNS, columns, strict=True)
        },
        schema=schema,
    )
    path = tick_path(state_dir / DELTAS, ts)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(".parquet.tmp")
    pq.write_table(table, staged, compression="zstd")
    staged.replace(path)
    return len(rows)


class TrendHistory:
    """Every Trends fact the Space serves, read once from ``data/state`` and ``config``.

    Built by :meth:`load`; the rest of the interface answers from what it read."""

    def __init__(self) -> None:
        # Every tick, oldest first: the archive's, then the delta ledger's from `_first_delta`.
        self._ticks: list[str] = []
        self._first_delta = 0
        self._families = _Names()
        self._bands = _Names()
        self._atses = _Names()
        self._boards = _Names(np.int32)
        # The index's group counts at every tick, as the aggregate ledger held them: the
        # archive's replay, then the tick files' (`_index_levels`). Codes and counts in the
        # narrowest type that holds them: 6.6M rows on 2026-09-25.
        self._index = _columns(_INDEX_TYPES)
        # Every Board delta of a level metric, in file order.
        self._deltas = _columns(_DELTA_TYPES)
        self._new_measured: set[str] = set()
        self._openings: Counter[str] = Counter()
        self._board_arrivals: dict[str, tuple[str, int]] = {}
        self._new_hold: dict[str, str] = {}
        self._ledger_start: str | None = None
        self._turnover: dict[str, list[dict]] = {}
        self._unscoped_markers: dict[str, list[dict]] = {}
        self._index_turnover: list[dict] = []
        self._turnover_since: str | None = None
        self._new_inflow_from: str | None = None
        self._epochs: list[dict] = []
        self._evictions: dict[str, list[tuple[str, int]]] = {}
        self._companies: dict[str, dict] = {}
        self._company_of: dict[str, str] = {}
        self._candidates: list[company_match.Candidate] = []
        self._watch: dict[str, dict[str, str]] = {}
        self._family_labels: dict[str, str] = {}
        self._family_successor: dict[str, str] = {}

    # ---- loading -------------------------------------------------------------------------

    @classmethod
    def load(cls, state_dir: Path, config_dir: Path) -> TrendHistory:
        """Read the history under ``state_dir`` (``data/state``) and the taxonomy under
        ``config_dir``. A missing or unreadable ledger is an empty history, never an error: the
        tab stays dark rather than taking the Space down at boot. The company directory and the
        dedup evictions degrade the same way on their own."""
        history = cls()
        try:
            stamped = history._read_ledgers(state_dir)
        except Exception as exc:  # noqa: BLE001 - an unreadable history darkens the tab only
            print(f"trend history unreadable ({type(exc).__name__}: {exc})", flush=True)
            history, stamped = cls(), []
        history._watch = watched_roles(config_dir / "role_watchlist.json")
        history._family_labels = _family_labels(config_dir / "role_families.json")
        history._family_successor = family_successors(config_dir / "role_families.json")
        history._evictions = _load_evictions(state_dir / _DEDUP_EVICTIONS)
        history._companies = _load_directory(state_dir / _DIRECTORY)
        history._company_of = {
            board: key
            for key, entry in history._companies.items()
            for board in entry["boards"]
        }
        history._epochs = _counting_changes(_methodology_stamps(stamped))
        history._derive_board_facts()
        return history

    def _read_ledgers(self, state_dir: Path) -> list[tuple[str, dict]]:
        """Read the archive and the tick files; returns every tick's Methodology, oldest first."""
        import pyarrow as pa

        files = [self._read_tick(table) for table in _tick_tables(state_dir)]
        archive = _archive_table(state_dir, files[0][0] if files else None)
        archive_ticks, archived, archive_deltas = self._read_archive(archive)
        self._ticks = [*archive_ticks, *(ts for ts, *_ in files)]
        self._first_delta = len(archive_ticks)
        levels = [columns for _, _, columns, _ in files]
        self._deltas = _columns(
            _DELTA_TYPES,
            tick=np.repeat(
                np.arange(self._first_delta, len(self._ticks)),
                [len(columns["board"]) for columns in levels],
            ),
            **{
                name: np.concatenate([columns[name] for columns in levels])
                for name in _DELTA_TYPES
                if name not in ("tick", "ats") and levels
            },
        )
        # A Board's ATS is its board_key's prefix: decoded once a Board, not once a row.
        board_ats = np.array(
            [self._atses.code(ats_of(board)) for board in self._boards.names],
            dtype=_DELTA_TYPES["ats"],
        )
        self._deltas["ats"] = board_ats[self._deltas["board"]]
        turnover: dict[str, list[dict]] = defaultdict(list)
        unscoped: dict[str, list[dict]] = defaultdict(list)
        for *_, others in files:
            for row in others:
                if row["metric"] in _TURNOVER_METRICS:
                    turnover[row["board"]].append(row)
                elif row["metric"] == _UNSCOPED:
                    unscoped[row["board"]].append(row)
        self._turnover = dict(turnover)
        self._unscoped_markers = dict(unscoped)
        parts = (
            self._index_levels(archive_deltas, 0, self._first_delta),
            self._index_levels(self._deltas, self._first_delta, len(self._ticks)),
        )
        self._index = {
            name: np.concatenate([part[name] for part in parts])
            for name in _INDEX_TYPES
        }
        self._new_measured = {
            self._ticks[t]
            for t in np.unique(self._index["tick"][self._index["metric"] == 0]).tolist()
        }
        pa.default_memory_pool().release_unused()
        return [
            *((ts, archived) for ts in archive_ticks),
            *((ts, methodology) for ts, methodology, *_ in files),
        ]

    def _read_tick(self, table) -> tuple[str, dict, dict[str, np.ndarray], list[dict]]:
        """One tick's file as ``(ts, methodology, level columns, other rows)``: its level deltas
        encoded, and its turnover and markers (ADR-0227) as rows carrying their tick and ATS."""
        ts = _tick_stamp(table)
        metric = _metric_codes(table["metric"])
        levels = metric >= 0
        columns = {
            "board": self._boards.encode(table["board"])[levels],
            "metric": metric[levels],
            "family": self._families.encode(table["family"])[levels],
            "band": self._bands.encode(table["band"])[levels],
            "delta": table["delta"].to_numpy()[levels],
        }
        others = [
            {**row, "ts": ts, "ats": ats_of(row["board"])}
            for row in (table.filter(~levels).to_pylist() if not levels.all() else [])
        ]
        methodology = json.loads((table.schema.metadata or {})[b"methodology"])
        return ts, methodology, columns, others

    def _read_archive(self, table) -> tuple[list[str], dict, dict[str, np.ndarray]]:
        """The archive as ``(its ticks, the Methodology they were counted under, its index-wide
        deltas encoded)``, each delta's tick an index into its ticks. Its ticks are in its
        metadata, since a tick where nothing moved has no rows. Empty without an archive."""
        if table is None:
            return [], {}, _columns(_DELTA_TYPES)
        metadata = table.schema.metadata or {}
        ticks = json.loads(metadata[b"ticks"])
        position = {ts: i for i, ts in enumerate(ticks)}
        deltas = _columns(
            _DELTA_TYPES,
            tick=[position[ts] for ts in table["ts"].to_pylist()],
            metric=_metric_codes(table["metric"]),
            family=self._families.encode(table["family"]),
            band=self._bands.encode(table["band"]),
            ats=self._atses.encode(table["ats"]),
            delta=table["delta"].to_numpy(),
        )
        return ticks, json.loads(metadata[b"methodology"]), deltas

    def _index_levels(
        self, d: dict[str, np.ndarray], start: int, end: int
    ) -> dict[str, np.ndarray]:
        """The index's group counts at ticks ``[start, end)``, from the deltas ``d`` holds for
        them, as the aggregate ledger wrote them: each ``(metric, family, band, ats)`` group
        holding any rows, in name order, then non-tech as one ``(stock, non-tech, all, all)`` row,
        written even at 0."""
        if end <= start:
            return _columns(_INDEX_TYPES)
        non_tech = self._families.code(NON_TECH)
        every = self._bands.code("all"), self._atses.code("all")
        sizes = (
            2,
            len(self._families.names),
            len(self._bands.names),
            len(self._atses.names),
        )
        ranks = (
            np.arange(2),
            self._families.ranks(),
            self._bands.ranks(),
            self._atses.ranks(),
        )
        # Tech groups sort by name; non-tech, one row summed over every Board and ATS, sorts after
        # all of them, as the writer appended it.
        last = int(np.prod(sizes))
        ranked = np.ravel_multi_index(
            tuple(
                rank[codes]
                for rank, codes in zip(
                    ranks, (d["metric"], d["family"], d["band"], d["ats"])
                )
            ),
            sizes,
        )
        ranked[d["family"] == non_tech] = last
        keys, inverse = np.unique(np.append(ranked, last), return_inverse=True)
        level = np.zeros((end - start, len(keys)), dtype=np.int32)
        np.add.at(level, (d["tick"] - start, inverse[:-1]), d["delta"])
        np.cumsum(level, axis=0, out=level)
        tick, key = np.nonzero((level > 0) | (keys == last))
        # each group's own codes, decoded once per group rather than once per row
        tech = keys < last
        codes = [
            np.argsort(rank)[part]
            for rank, part in zip(
                ranks, np.unravel_index(np.where(tech, keys, 0), sizes)
            )
        ]
        fixed = (1, non_tech, *every)
        codes = [np.where(tech, c, value) for c, value in zip(codes, fixed)]
        return _columns(
            _INDEX_TYPES,
            tick=tick + start,
            metric=codes[0][key],
            family=codes[1][key],
            band=codes[2][key],
            ats=codes[3][key],
            count=level[tick, key],
        )

    def _derive_board_facts(self) -> None:
        """Each Board's openings, arrival and `new` hold, the index's turnover, and the picker's
        candidates: everything per Board a request reads, derived once."""
        d = self._deltas
        boards = self._boards.names
        tech_stock = (
            (d["metric"] == 1)
            & (d["family"] != self._families.code(NON_TECH))
            & ~np.isin(d["family"], self._watch_codes())
        )
        # Each Board's current tech openings: its `stock` deltas summed. The directory carries no
        # counts on purpose (ADR-0185); the delta ledger already holds them. `non-tech` is not an
        # opening a picker should count, and `watch:` rows re-count Jobs already counted in their
        # family (ADR-0051).
        sums = np.bincount(d["board"][tech_stock], d["delta"][tech_stock], len(boards))
        self._openings = Counter(
            {
                boards[b]: int(sums[b])
                for b in np.unique(d["board"][tech_stock]).tolist()
            }
        )
        # Each Board's first tick in the ledger, and the tech openings it arrived with. A Board's
        # first delta is its whole stock at once (ADR-0143), so a Board found after a company's
        # line began lands in that line as one step (ADR-0185).
        stock = d["metric"] == 1
        first = np.full(len(boards), len(self._ticks), dtype=np.int64)
        np.minimum.at(first, d["board"][stock], d["tick"][stock])
        arrived_rows = tech_stock & (d["tick"] == first[d["board"]])
        arrived = np.bincount(
            d["board"][arrived_rows], d["delta"][arrived_rows], len(boards)
        )
        self._board_arrivals = {
            boards[b]: (self._ticks[first[b]], int(arrived[b]))
            for b in np.nonzero(first < len(self._ticks))[0].tolist()
        }
        # When each Board may count toward `new`: its first tick plus the flow window (ADR-0185).
        # Every Board, the first tick's baseline included: the ledger's first week reads a
        # Board's whole backlog as new wherever the Board was found.
        self._new_hold = {
            board: (
                datetime.fromisoformat(ts) + timedelta(days=NEW_WINDOW_DAYS)
            ).isoformat(timespec="seconds")
            for board, (ts, _) in self._board_arrivals.items()
        }
        # The first tick of the Board-delta ledger, before which no per-Board count exists.
        self._ledger_start = min(
            (ts for ts, _ in self._board_arrivals.values()), default=None
        )
        # The index's turnover summed over every Board, and the first tick that booked any: a
        # run before it measured none, which is a gap, not a zero.
        self._index_turnover = _index_turnover(
            self._turnover, self._company_dedup_touched
        )
        self._turnover_since = min(
            (r["ts"] for r in self._index_turnover), default=None
        )
        # The first tick whose whole trailing week has Opened facts (ADR-0227), where `new`
        # becomes that week's Opened jobs (ADR-0230 decision 5). They cannot be backfilled, so
        # before it `new` stays the level it always was; a partial week would read as a ramp.
        if self._turnover_since:
            full = (
                datetime.fromisoformat(self._turnover_since)
                + timedelta(days=NEW_WINDOW_DAYS)
            ).isoformat(timespec="seconds")
            at = bisect_left(self._ticks, full)
            self._new_inflow_from = self._ticks[at] if at < len(self._ticks) else None
        self._candidates = [
            company_match.Candidate(
                key=key,
                name=entry["name"],
                words=tuple(company_match.normalize(entry["name"])),
                openings=self._company_openings(entry),
            )
            for key, entry in self._companies.items()
        ]

    def _watch_codes(self) -> np.ndarray:
        return np.array(
            [
                code
                for code, name in enumerate(self._families.names)
                if name.startswith(WATCH_PREFIX)
            ],
            dtype=np.int64,
        )

    # ---- what the history answers ----------------------------------------------------------

    @property
    def ticks(self) -> tuple[str, ...]:
        """Every tick the history holds, oldest first."""
        return tuple(self._ticks)

    @property
    def companies(self) -> dict[str, dict]:
        """The Company directory (ADR-0185), ``{company key: {name, boards, operator}}``; empty
        until the pipeline writes one."""
        return self._companies

    def openings(self) -> dict[str, int]:
        """Every Board the history has counted tech openings on, with its tech openings now (0
        once closed)."""
        return dict(self._openings)

    def index_counts(self, ts: str) -> dict[tuple[str, str, str, str], int]:
        """The index-wide group counts at tick ``ts``, ``(metric, family, band, ats) -> count``,
        as the aggregate ledger holds them: the archive's own rows, or the replay's."""
        if ts not in self._ticks:
            return {}
        rows = self._index["tick"] == self._ticks.index(ts)
        names = (
            np.array(LEVEL_METRICS, dtype=object),
            np.array(self._families.names, dtype=object),
            np.array(self._bands.names, dtype=object),
            np.array(self._atses.names, dtype=object),
        )
        keys = zip(
            *(
                names[i][self._index[column][rows]].tolist()
                for i, column in enumerate(("metric", "family", "band", "ats"))
            )
        )
        return dict(zip(keys, self._index["count"][rows].tolist()))

    def suggest_companies(self, query: str, limit: int) -> list[dict]:
        """Directory companies matching ``query`` for the Trends company picker (ADR-0185), best
        first, each with its tech openings now and Board count, labelled apart from any other
        of the same name among them."""
        found = company_match.suggest(query, self._candidates, limit)
        labels = self._company_labels([candidate.key for candidate in found])
        return [self._company_json(c.key, labels[c.key]) for c in found]

    def company_moves(self, keys: Iterable[str]) -> CompanyMoves:
        """Hot's figures for each directory company in ``keys``, over the trailing
        ``NEW_WINDOW_DAYS`` (ADR-0230).

        Each is read off the company's own whole-company line, netted, from the window's base:
        the answer its Hot row's "See trend" opens. So a row's ``net`` is what that trend moves
        by, by construction rather than by a second copy of the netting rule (the design's
        invariant 4). The base is the last tick before the week began, so the first change
        measured is the week's own.

        One answer per company, not one answer split by company: measured on 2026-09-25's
        state, the split view netted 3 of 2,478 companies differently (a partial read is judged
        per line, and a company's line there is not its categories' sum)."""
        if not self._ticks:
            return CompanyMoves(
                {"base": None, "from": None, "to": None, "turnover_from": None}, {}
            )
        newest = self._ticks[-1]
        week_began = (
            datetime.fromisoformat(newest) - timedelta(days=NEW_WINDOW_DAYS)
        ).isoformat(timespec="seconds")
        at = max(bisect_left(self._ticks, week_began) - 1, 0)
        base = self._ticks[at]
        first = self._ticks[min(at + 1, len(self._ticks) - 1)]
        moves = {}
        for key in keys:
            answer = self.answer(TrendQuestion(companies=(key,), since=base))
            line = answer["series_sum"]
            netted = [v for v in line["net"]["count"] if v is not None]
            turnover = line["hiring_turnover"] or {"opened": 0, "closed": 0}
            moves[key] = CompanyMove(
                net=trend_netting.js_round(netted[-1] - netted[0]) if netted else 0,
                opened=turnover["opened"],
                closed=turnover["closed"],
                counted_since=answer["counted_since"][key],
            )
        turnover_from = (
            max(self._turnover_since, first) if self._turnover_since else None
        )
        window = {
            "base": base,
            "from": first,
            "to": newest,
            "turnover_from": turnover_from,
        }
        return CompanyMoves(window, moves)

    def answer(self, question: TrendQuestion) -> dict:
        """The ``/trends`` payload: :meth:`unnetted_answer` with every line netted, once
        (ADR-0230 decision 3), so the page draws what it is given."""
        return trend_netting.net_answer(self.unnetted_answer(question))

    def unnetted_answer(self, question: TrendQuestion) -> dict:
        """Role counts over time (ADR-0040, ADR-0051), before any line is netted: what
        :meth:`answer` nets and ``trend_reading`` reads (ADR-0233).

        ``metric`` ``stock`` (default) is live openings; ``new`` is those first seen inside the
        flow window. Default view: one series per family, each point the family's total across
        bands. ``family`` splits that family by seniority band, and ``split=roles`` swaps the
        bands for the family's watched roles (ADR-0051) instead. ``since`` / ``until``
        (ISO-8601, inclusive) narrow the window to runs whose stamp falls in range; a malformed
        one is a ValueError, not a silent no-op, and an out-of-data range answers empty series.

        ``coverage=comparable`` with ``base`` (ADR-0143) selects every Board first observed at or
        before the requested base measurement, then replays only that cohort through later
        measurements. The Board-delta ledger starts with this feature, so an earlier base returns
        no fabricated history.

        ``ats`` (ADR-0075) narrows to the named ATSes; empty means every ATS, which is the only
        spelling of "no filter" — naming all of them explicitly would exclude every migrated
        pre-ADR-0075 row (they carry ``ats='all'``, matching no real name).

        ``companies`` (ADR-0185) narrows to picked companies from the company directory, each
        named by **any** of its Boards' board_keys. A company's counts exist only per Board, so a
        pick replays the Board-delta ledger and its history starts at that ledger's first tick.
        ``split=company`` draws one series per picked company (with ``family``, within that
        family), and ``companies`` echoes the picks with their labels. Under a pick, ``totals`` is
        the picks' combined total and ``company_totals`` each pick's own, so a line split by
        company can be a share of that company. ``counted_since`` maps each pick to its first
        counted tick, and ``ledger_start`` is the Board-delta ledger's first tick. Under a pick, a
        Board counts toward ``new`` only once the flow window has passed since its first tick;
        ``new_counted_from`` maps each pick to the first run its ``new`` can count.
        ``discovered`` lists ``{ts, company, boards, openings}``: Boards of a pick found after its
        line began, a step of openings that were already open, not hiring.

        ``totals`` carries the served table per stamp, narrowed by ``ats`` exactly like every
        other row, so the page can plot a share of what is in view. Watched roles are left out of
        it: they re-count Jobs already counted in their family. The reserved ``non-tech`` family
        is never a series; it rides along as ``non_tech``, the tech filter's health number.

        ``epochs`` (ADR-0164) lists methodology boundaries within the requested window, not
        narrowed by ``ats``: a change of how we count did not happen "for" one ATS.

        Raises ValueError on a bad question, and TrendsUnavailable when the history, or a pick's
        company directory, is not on this deployment yet."""
        if not self._ticks:
            raise TrendsUnavailable("no trend data yet")
        metric = question.metric
        if metric not in ("stock", "new"):
            raise ValueError("metric must be 'stock' or 'new'")
        coverage = question.coverage
        if coverage not in ("all", "comparable"):
            raise ValueError("coverage must be 'all' or 'comparable'")
        family = question.family
        split = question.split
        if split not in ("bands", "roles", "company"):
            raise ValueError("split must be 'bands', 'roles' or 'company'")
        picked = list(question.companies)
        company_of: dict[str, str] | None = None
        if picked:
            if not self._companies:
                raise TrendsUnavailable("no company directory on this deployment yet")
            # Board keys compare case-blind, as the directory joins them: a hand-typed
            # `company=GOOGLE:careers.google.com` was "not in the company directory".
            if any(board not in self._company_of for board in picked):
                folded = {board.lower(): board for board in self._company_of}
                picked = [
                    board
                    if board in self._company_of
                    else folded.get(board.lower(), board)
                    for board in picked
                ]
            unknown = [board for board in picked if board not in self._company_of]
            if unknown:
                raise ValueError(f"unknown company: {', '.join(unknown)}")
            company_of = {
                board: key
                for key in {self._company_of[board] for board in picked}
                for board in self._companies[key]["boards"]
            }
        if split == "company" and not picked:
            raise ValueError("split=company needs at least one company")
        try:
            since = _norm_stamp(question.since) if question.since is not None else None
            until = _norm_stamp(question.until) if question.until is not None else None
            base = _norm_stamp(question.base) if question.base is not None else None
        except ValueError:
            raise ValueError("since/until/base must be ISO-8601") from None
        ats = list(question.ats)

        base_stamp = None
        if coverage == "comparable" or company_of is not None:
            # A company's counts exist only per Board, so a pick replays the delta ledger too;
            # its history therefore starts at that ledger's first tick, 2026-09-13 (ADR-0185).
            trends_rows, first_charted = self._replay_rows(
                base, coverage == "comparable", company_of, ats, since, until
            )
            if coverage == "comparable":
                base_stamp = first_charted
        else:
            trends_rows = self._index_rows(ats, since, until)
        # `new` is the jobs Opened over the trailing week from `_new_inflow_from` on (ADR-0230
        # decision 5); before it, the level it always was. The switch is a counting change.
        # Watched roles have no Opened facts (turnover is booked per family, ADR-0227), so the
        # roles drill keeps the level.
        inflow_from = (
            self._new_inflow_from
            if metric == "new" and not (family and split == "roles")
            else None
        )
        if inflow_from:
            trends_rows = self._with_new_inflow(
                trends_rows, inflow_from, company_of, base_stamp, ats
            )

        # Epochs (ADR-0164) are their own timeline, independent of the series version — a refit
        # is itself one of the things that can produce a boundary, so filtering by the live
        # version would hide the exact event most worth marking. Only the window narrows it.
        epochs = self._epochs
        if since:
            # Under New a change a week before the window still echoes inside it (its openings
            # age out of "new" there), so its epoch comes along for the page to mark that echo.
            earliest = since
            if metric == "new":
                earliest = (
                    datetime.fromisoformat(since) - timedelta(days=NEW_WINDOW_DAYS)
                ).isoformat(timespec="seconds")
            epochs = [e for e in epochs if e["ts"] >= earliest]
        if until:
            epochs = [e for e in epochs if e["ts"] <= until]
        if (
            inflow_from
            and (not since or since <= inflow_from)
            and (not until or inflow_from <= until)
        ):
            epochs = sorted(
                [
                    *epochs,
                    {
                        "ts": inflow_from,
                        "changed": ["new openings became the jobs opened in the week"],
                        "fields": [trend_netting.NEW_BECAME_INFLOW],
                    },
                ],
                key=lambda e: e["ts"],
            )

        # Stamps and the share denominator come from `trends_rows` (since/until/ats-narrowed,
        # but not the family/metric drill): total(ts) is every family + non-tech IN THAT SCOPE,
        # since the counts assign every row exactly once, which is what makes share
        # coverage-immune (ADR-0051, scope extended to ATS by ADR-0075).
        stock = [r for r in trends_rows if r["metric"] == "stock"]
        stamps = sorted({r["ts"] for r in stock})
        totals: dict[str, int] = {}
        for r in stock:
            if not r["family"].startswith(
                WATCH_PREFIX
            ):  # watch rows re-count family rows
                totals[r["ts"]] = totals.get(r["ts"], 0) + r["count"]

        # Families by the names the data holds (ADR-0220). A retired family reads as its v3
        # successor wherever the successor has data in this scope, so a window spanning the
        # switch draws one line, not two that stop and start. Weighed in openings, so "the
        # larger" means more jobs, not more rows.
        successors = self._family_successor
        present = _family_weights(trends_rows)
        rename = {old: new for old, new in successors.items() if new in present}
        # A v3 name asked for before its data lands reads as all of its predecessors together:
        # "AI, ML & Data Science" is AI / Machine Learning and Data Science.
        if family and family not in present:
            rename.update(
                {
                    old: family
                    for old in predecessors(family, successors)
                    if old in present
                }
            )
        if rename.keys() & present.keys():
            trends_rows = [
                {**r, "family": rename[r["family"]]} if r["family"] in rename else r
                for r in trends_rows
            ]
            present = _family_weights(trends_rows)
        family = _resolve_family(family, present, successors)

        # A watched role's parent as the data holds it: its v3 parent, or while that has no
        # data, the retired family it resolves to.
        def parent_of(meta: dict) -> str | None:
            return _resolve_family(meta["parent"], present, successors)

        rows = [
            r for r in trends_rows if r["metric"] == metric and r["family"] != NON_TECH
        ]
        if split == "company":
            # One series per picked company (ADR-0185): its whole tech total, or one family.
            rows = [
                r
                for r in rows
                if (
                    r["family"] == family
                    if family
                    else not r["family"].startswith(WATCH_PREFIX)
                )
            ]
            key = "company"
        elif family and split == "roles":
            # The family's watched sub-roles (ADR-0051), each its own series.
            wanted = {n for n, meta in self._watch.items() if parent_of(meta) == family}
            rows = [r for r in rows if r["family"] in wanted]
            key = "family"
        elif family:
            rows = [r for r in rows if r["family"] == family]
            key = "band"
        else:
            rows = [r for r in rows if not r["family"].startswith(WATCH_PREFIX)]
            key = "family"

        # Stamps where the `new` metric was recorded at all. The counts write only non-empty
        # groups, so on such a stamp a series with no row genuinely saw zero fresh openings,
        # whereas a stamp with no `new` rows anywhere is one this metric did not yet exist for.
        # A pick's own rows cannot answer it: one company can go a whole run with nothing new,
        # which is a 0, not a gap. So under a pick the whole index says which runs measured it.
        measured = (
            self._new_measured
            if company_of
            else {r["ts"] for r in trends_rows if r["metric"] == "new"}
        ) | ({ts for ts in stamps if ts >= inflow_from} if inflow_from else set())

        def value_at(
            points: dict[str, int], ts: str, counts_from: str | None = None
        ) -> int | None:
            """A series' value at one stamp — 0 where the metric ran and found none, else None.

            ``counts_from`` is when a pick's ``new`` first counts (the `new` hold): before it
            every Board of the series is held, so the run measured nothing for it, which is a
            gap — a 0 there drew a week of nothing and then a leap that read as a surge."""
            if (
                counts_from is not None
                and ts < counts_from
                and not (inflow_from and ts >= inflow_from)
            ):
                return None
            return points.get(ts, 0 if metric == "new" and ts in measured else None)

        picked_keys = sorted(set(company_of.values())) if company_of else []
        # A pick with rows in scope gets a line even when this metric has none of them — for
        # `new`, "nothing opened this week" is a line at 0. A pick with no rows at all (outside
        # a comparable cohort, the ATS selection or the window) gets none, and is named in
        # `uncounted`.
        in_scope = {r["company"] for r in trends_rows} if company_of else set()
        series: dict[str, dict[str, int]] = {
            k: {} for k in picked_keys if key == "company" and k in in_scope
        }
        for r in rows:  # sum over the other axis, so a family point is its total
            series.setdefault(r[key], {})
            at = series[r[key]]
            at[r["ts"]] = at.get(r["ts"], 0) + r["count"]
        company_labels = self._company_labels(picked_keys)
        # Each pick's own first counted tick (over the Boards in scope), which is where its line
        # starts: the ledger's first tick for most, later for companies first counted after it.
        counted = {
            board: pick
            for board, pick in (company_of or {}).items()
            if board in self._board_arrivals and _in_ats_scope(board, ats)
        }
        began: dict[str, str] = {}
        new_from: dict[str, str] = {}  # when each pick's `new` first counts
        for board, pick in counted.items():
            ts = self._board_arrivals[board][0]
            began[pick] = min(began.get(pick, ts), ts)
            release = self._new_hold.get(board, ts)
            # Opened holds no backlog, so from the inflow's first run nothing is held.
            if inflow_from and release > inflow_from:
                release = max(ts, inflow_from)
            new_from[pick] = min(new_from.get(pick, release), release)

        def _series_label(name: str) -> str:
            if key == "company":
                return company_labels[name]
            if key == "band":
                return BAND_LABELS.get(name, name)
            if name in self._watch:
                return self._watch[name]["label"]
            return self._family_labels.get(name, name)

        # Under `new`, where a series' first counted run is: a company line's own pick's
        # release, and for a line summing several picks the earliest, after which each later
        # one joins the sum as a marked step (trend_netting's notes).
        def counts_from(name: str) -> str | None:
            if metric != "new" or not company_of:
                return None
            if key == "company":
                return new_from.get(name)
            return min(new_from.values(), default=None)

        out = [
            {
                "name": name,
                "label": _series_label(name),
                # None (not 0) where a run has no row for this series: a gap is "not
                # measured", and plotting it as zero would invent a crash that never happened.
                "points": values,
                "latest": values[-1] if stamps else None,
            }
            for name, values in (
                (
                    name,
                    _held_at_zero(
                        [value_at(points, ts, counts_from(name)) for ts in stamps],
                        metric if company_of else None,
                    ),
                )
                for name, points in series.items()
            )
        ]
        out.sort(key=lambda s: -(s["latest"] or 0))
        # Each pick's own line under a view that sums several, so the page takes a company's
        # steps out of that company's part of the sum only.
        pick_series: dict[str, list[int | None]] = {}
        # And each pick's part of every category or level line, so a company's steps and
        # duplicate removals come out of its own part of a category too: without them NVIDIA and
        # Micron's categories summed +73 against their Total of +40 (review of #690).
        pick_parts: dict[str, dict[str, list[int | None]]] = {}
        if (
            len(picked_keys) > 1
            and key != "company"
            and not (family and split == "roles")
        ):

            def pick_line(points: dict[str, int], k: str) -> list[int | None]:
                return _held_at_zero(
                    [
                        value_at(
                            points, ts, new_from.get(k) if metric == "new" else None
                        )
                        for ts in stamps
                    ],
                    metric,
                )

            per: dict[str, dict[str, int]] = {}
            per_part: dict[str, dict[str, dict[str, int]]] = {}
            for r in rows:
                at = per.setdefault(r["company"], {})
                at[r["ts"]] = at.get(r["ts"], 0) + r["count"]
                at = per_part.setdefault(r[key], {}).setdefault(r["company"], {})
                at[r["ts"]] = at.get(r["ts"], 0) + r["count"]
            pick_series = {k: pick_line(points, k) for k, points in per.items()}
            # A part is 0, not unmeasured, wherever its company is counted: a company's first
            # AI/ML opening is hiring, where a company's own first run is a join.
            pick_parts = {
                name: {
                    k: [
                        0 if v is None and whole is not None else v
                        for v, whole in zip(pick_line(points, k), pick_series[k])
                    ]
                    for k, points in parts.items()
                }
                for name, parts in per_part.items()
            }
        non_tech: dict[str, int] = {}
        for row in stock:
            if row["family"] == NON_TECH:
                non_tech[row["ts"]] = non_tech.get(row["ts"], 0) + row["count"]
        # Each pick's own denominator, so a line split by company is a share of *that* company.
        company_totals: dict[str, dict[str, int]] = {k: {} for k in picked_keys}
        for row in stock:
            if company_of and not row["family"].startswith(WATCH_PREFIX):
                at = company_totals[row["company"]]
                at[row["ts"]] = at.get(row["ts"], 0) + row["count"]
        # Boards of a pick found after its line began: each lands its tech openings at once,
        # openings that were already open, so the chart marks the step rather than let it read
        # as hiring. A Board that lands on the charted point where its company's line begins
        # starts that line and is not a step, nor is one that brought no tech openings. None
        # under comparable coverage, which leaves every such Board out of the cohort. Under
        # `new` a found Board steps the line when its hold ends, not when it arrived.
        found: dict[tuple[str, str], list[int]] = {}
        if coverage != "comparable" and stamps:
            for board, pick in counted.items():
                ts, openings = self._board_arrivals[board]
                if metric == "new":
                    ts = self._new_hold.get(board, ts)
                    # A found Board's backlog is Recounted, never Opened: no step in the inflow.
                    if inflow_from and ts >= inflow_from:
                        continue
                at = bisect_left(stamps, ts)
                if (
                    openings <= 0
                    or at == len(stamps)
                    # the pick's line begins at its own first counted run: under `new`, where
                    # its first Board's hold ends, not where it arrived
                    or at
                    <= bisect_left(
                        stamps, new_from[pick] if metric == "new" else began[pick]
                    )
                ):
                    continue
                bucket = found.setdefault((stamps[at], pick), [0, 0])
                bucket[0] += 1
                bucket[1] += openings
        # Each line's turnover (ADR-0227): the jobs opened and closed that its net change is made
        # of. On every line of every view on stock, the index's included, and summed from the
        # same rows, so the index's is exactly the sum of every company's. Not on the roles
        # drill, whose watched roles re-count their family's jobs.
        with_turnover = metric == "stock" and not (family and split == "roles")
        # The Boards in scope, by pick ("" for the index): a pick's Boards, else under comparable
        # coverage the cohort's, else every Board through the index's own summed rows.
        scope: dict[str, str] | None = None
        if company_of is not None:
            scope = counted
        elif coverage == "comparable":
            scope = {
                board: ""
                for board in self._turnover.keys() | self._unscoped_markers.keys()
                if _in_ats_scope(board, ats)
            }
        if scope is not None and base_stamp is not None:
            scope = {
                board: pick
                for board, pick in scope.items()
                if self._in_cohort(board, base_stamp)
            }
        # With no pick the lines keep a counting change's jump, marked, but its turnover is not
        # hiring. The index leaves out, Board by Board, the runs each company's own line leaves
        # out, so the index's opened and closed are the sum of what every company's view shows.
        left_out: tuple[set[int], set[int]] = (
            trend_netting.left_out_runs(epochs, stamps, key == "band")
            if company_of is None
            else (set(), set())
        )
        if scope is None:
            turnover_rows = [
                (row, "")
                for row in self._index_turnover
                if not ats or row["ats"] in ats
            ]
            unscoped = {
                board: ""
                for board in self._unscoped_markers
                if _in_ats_scope(board, ats)
            }
        else:
            # A comparable cohort with no pick is still the index: each row says whether
            # duplicate removal can move its Board's company, as the index's summed rows do.
            if company_of is None:
                touched = {board: self._company_dedup_touched(board) for board in scope}
                turnover_rows = [
                    ({**row, "touched": touched[board]}, pick)
                    for board, pick in scope.items()
                    for row in self._turnover.get(board, ())
                ]
            else:
                turnover_rows = [
                    (row, pick)
                    for board, pick in scope.items()
                    for row in self._turnover.get(board, ())
                ]
            unscoped = scope

        def line_of(row: dict, pick: str) -> str | None:
            held = rename.get(row["family"], row["family"])
            if split == "company":
                return pick if not family or held == family else None
            if family:
                return row["band"] if held == family else None
            return held

        pick_turnover: dict[str, dict[str, list[int | None]]] = {}
        if with_turnover:
            by_line = self._turnover_series(
                turnover_rows, stamps, line_of, [line["name"] for line in out], left_out
            )
            for line in out:
                line["turnover"] = by_line[line["name"]]
            # Each pick's own turnover where its own line is served (`pick_series`), so a line
            # summing several picks counts each pick's turnover over the runs its line counts.
            pick_turnover = self._turnover_series(
                turnover_rows,
                stamps,
                lambda row, pick: pick if line_of(row, pick) is not None else None,
                list(pick_series),
            )
        # Which families have watched sub-roles, so the page can offer the roles drill only
        # there, under the names the data holds as well as the config's.
        watch_parents = sorted(
            {parent for meta in self._watch.values() if (parent := parent_of(meta))}
        )
        payload = {
            "coverage": coverage,
            # How long a posting counts as new: the page says it, and netting under New takes a
            # tech-filter change out again a week on, when the openings it let in age out.
            "new_window_days": NEW_WINDOW_DAYS,
            # The first run whose `new` is the jobs opened in the week, not the level of jobs
            # first seen in it and still open (ADR-0230 decision 5); None until then.
            "new_inflow_from": inflow_from,
            "base": base_stamp,
            "metric": metric,
            "stamps": stamps,
            "series": out,
            "totals": [totals.get(ts) for ts in stamps],
            "non_tech": [non_tech.get(ts) for ts in stamps],
            "split_by": key,
            # The drilled family's display name, so a cold link into a drill can name it.
            "family": family,  # as resolved (_resolve_family), which the page adopts
            # Whether HeadStart has that family at all, so an unknown name reads as unknown
            # rather than as "no openings counted" at the company. Not "holds rows in scope":
            # `present` is already narrowed to the picks and the window.
            "family_known": bool(family)
            and (family in present or family in self._family_labels),
            "family_label": self._family_labels.get(family, family) if family else None,
            "watch_parents": watch_parents,
            "epochs": epochs,
            # With its Board keys, so the chart can hand a pick to Search by Board (ADR-0185).
            "companies": [
                {
                    **self._company_json(k, company_labels[k]),
                    "board_keys": self._companies[k]["boards"],
                }
                for k in picked_keys
            ],
            "pick_series": pick_series,
            "pick_parts": pick_parts,
            "pick_turnover": pick_turnover,
            "company_totals": {
                k: [company_totals[k].get(ts) for ts in stamps] for k in picked_keys
            },
            "counted_since": began,
            # Picks with nothing in this scope, so the page names them rather than charting
            # fewer companies than the chips show.
            "uncounted": [k for k in picked_keys if k not in in_scope],
            "ledger_start": self._ledger_start,
            "new_counted_from": new_from,
            "discovered": [
                {"ts": ts, "company": pick, "boards": n, "openings": openings}
                for (ts, pick), (n, openings) in sorted(found.items())
            ],
            # Duplicate rows removed from each pick's Boards, per charted run (#649). Under
            # comparable coverage, from the cohort's Boards only: a Board found later is out of
            # the cohort, but a removal on a cohort Board still halves what it counted (ADR-0233;
            # serving none, Micron read +160 under Comparable and +83 under All). The ledger
            # counts every removed row, `non-tech` among them.
            "evicted": self._picks_evicted(
                {
                    board: pick
                    for board, pick in counted.items()
                    if self._in_cohort(board, base_stamp)
                },
                stamps,
            ),
            # When turnover began (ADR-0227). A window that starts earlier has lines whose
            # opened and closed cover only part of it, and the page says from when.
            "turnover_since": self._turnover_since if with_turnover else None,
            # The runs the index's turnover leaves out for a counting change. Empty under a
            # pick, whose lines' netting decides (`hiring_turnover`).
            "turnover_left_out": [stamps[k] for k in sorted(left_out[0] | left_out[1])],
            # Per pick ("" for the index), its Boards whose closures went uncounted on some run
            # in the window (ADR-0053).
            "closures_unseen": self._closures_unseen(unscoped, stamps)
            if with_turnover
            else {},
        }
        return payload

    # ---- the rows a question reads -----------------------------------------------------------

    def _window(self, since: str | None, until: str | None) -> tuple[int, int]:
        """The ticks inside ``[since, until]`` as a ``[first, end)`` index range."""
        lo = bisect_left(self._ticks, since) if since else 0
        hi = bisect_left(self._ticks, until) if until else len(self._ticks)
        if until and hi < len(self._ticks) and self._ticks[hi] == until:
            hi += 1
        return lo, hi

    def _ats_codes(self, ats: list[str]) -> np.ndarray:
        return np.array(
            [code for name in ats if (code := self._atses.get(name)) is not None],
            dtype=np.int64,
        )

    def _index_rows(
        self, ats: list[str], since: str | None, until: str | None
    ) -> list[dict]:
        """The index's rows in the window and the ATS selection, summed over ATS: every
        ``(ts, metric, family, band)`` group holding a row, each tick's in name order, as the
        aggregate ledger lists them."""
        lo, hi = self._window(since, until)
        index = self._index
        rows = (index["tick"] >= lo) & (index["tick"] < hi)
        if ats:
            rows &= np.isin(index["ats"], self._ats_codes(ats))
        sizes = (max(hi - lo, 1), 2, len(self._families.names), len(self._bands.names))
        ranks = self._families.ranks(), self._bands.ranks()
        keys = np.ravel_multi_index(
            (
                index["tick"][rows] - lo,
                index["metric"][rows],
                ranks[0][index["family"][rows]],
                ranks[1][index["band"][rows]],
            ),
            sizes,
        )
        held = np.bincount(keys, minlength=int(np.prod(sizes)))
        # summed as floats, exact for any count below 2**53
        counts = np.bincount(keys, index["count"][rows], len(held)).astype(np.int64)
        present = np.nonzero(held)[0]
        tick, metric, family, band = np.unravel_index(present, sizes)
        families = np.array(self._families.names, dtype=object)[np.argsort(ranks[0])]
        bands = np.array(self._bands.names, dtype=object)[np.argsort(ranks[1])]
        return [
            {
                "ts": self._ticks[t + lo],
                "metric": LEVEL_METRICS[m],
                "family": f,
                "band": b,
                "count": n,
            }
            for t, m, f, b, n in zip(
                tick.tolist(),
                metric.tolist(),
                families[family].tolist(),
                bands[band].tolist(),
                counts[present].tolist(),
            )
        ]

    def _replay_rows(
        self,
        base: str | None,
        comparable: bool,
        company_of: dict[str, str] | None,
        ats: list[str],
        since: str | None,
        until: str | None,
    ) -> tuple[list[dict], str | None]:
        """Rebuild counts from the Board-delta ledger for a chosen set of Boards.

        ``comparable`` keeps only Boards first observed by ``base`` (ADR-0143). ``company_of``
        keeps only the picked companies' Boards and tags every row with its company key, so the
        answer can split by company (ADR-0185); None means every Board. The two combine: picked
        companies, counted only over the Boards already known at the base.

        Returns the rows in the window and the ATS selection, summed over ATS, and the first
        measurement charted (the base, when ``comparable``)."""
        if not self._ticks or self._first_delta == len(self._ticks):
            return [], None
        stamps = self._ticks
        first_delta = stamps[self._first_delta]
        eligible: set[str] | None = None
        if comparable:
            if base is None:
                base = first_delta
            # A base before per-Board counting began starts the cohort at the first run that
            # counted by Board: nothing earlier can be told apart, and answering "nothing" left
            # a 30-day window blank for a reader who only asked to hold coverage fixed. The
            # first run at or after the asked start, as All coverage starts its window.
            at = bisect_left(stamps, max(base, first_delta))
            base_stamp = stamps[at] if at < len(stamps) else stamps[-1]
            eligible = {
                board
                for board, (seen, _) in self._board_arrivals.items()
                if seen <= base_stamp
            }
        else:
            base_stamp = first_delta
        d = self._deltas
        boards = self._boards.names
        rows = np.ones(len(d["tick"]), dtype=bool)
        company = np.zeros(len(d["tick"]), dtype=np.int64)
        companies = [""]
        if company_of is not None:
            companies = sorted(set(company_of.values()))
            code_of = {key: i for i, key in enumerate(companies)}
            of_board = np.full(len(boards), -1, dtype=np.int64)
            for board, key in company_of.items():
                if (code := self._boards.get(board)) is not None:
                    of_board[code] = code_of[key]
            company = of_board[d["board"]]
            rows &= company >= 0
        if eligible is not None:
            allowed = np.zeros(len(boards), dtype=bool)
            allowed[
                [code for b in eligible if (code := self._boards.get(b)) is not None]
            ] = True
            rows &= allowed[d["board"]]
        if ats:
            rows &= np.isin(d["ats"], self._ats_codes(ats))
        # A Board's first week in the ledger reads its whole backlog as `new`, so its `new`
        # deltas wait out the flow window and are applied at the first run after it, when the
        # backlog has aged out and what lands is real inflow (ADR-0185).
        hold = np.full(len(boards), -1, dtype=np.int64)
        for board, ts in self._new_hold.items():
            if (code := self._boards.get(board)) is not None:
                hold[code] = bisect_left(stamps, ts)
        lo, hi = self._window(since, until)
        first = max(bisect_left(stamps, base_stamp), lo)
        start, end = self._first_delta, len(self._ticks)
        charted = range(max(start, first), min(end, hi))
        out = self._counts_at_charted_ticks(
            np.nonzero(rows)[0], company, companies, hold, charted
        )
        return out, base_stamp

    def _counts_at_charted_ticks(
        self,
        rows: np.ndarray,
        company: np.ndarray,
        companies: list[str],
        hold: np.ndarray,
        charted: range,
    ) -> list[dict]:
        """The groups the delta ``rows`` replay to, at the ``charted`` ticks.

        Each group lists in the order its first delta was applied, which is the order the old
        per-row replay listed it in (so ties between lines sort the same): a tick's held `new`
        deltas first, Board by Board, then its own deltas in file order."""
        if not len(rows) or not len(charted):
            return []
        start, end = self._first_delta, len(self._ticks)
        d = self._deltas
        board = d["board"][rows]
        tick = d["tick"][rows]
        held = (d["metric"][rows] == 0) & (tick < hold[board])
        applied = np.where(held, hold[board], tick)
        kept = applied < end  # a hold that ends after the newest tick is never applied
        rows, board, tick, held, applied = (
            a[kept] for a in (rows, board, tick, held, applied)
        )
        # the order each delta is applied in: by tick; held deltas first, each Board's after the
        # Board first held before it; then file order
        first_held = np.full(len(self._boards.names), len(d["tick"]), dtype=np.int64)
        np.minimum.at(first_held, board[held], rows[held])
        order = np.lexsort(
            (rows, np.where(held, first_held[board], rows), ~held, applied)
        )
        rank = np.empty(len(rows), dtype=np.int64)
        rank[order] = np.arange(len(rows))
        sizes = (
            len(companies),
            2,
            len(self._families.names),
            len(self._bands.names),
        )
        keys, inverse = np.unique(
            np.ravel_multi_index(
                (company[rows], d["metric"][rows], d["family"][rows], d["band"][rows]),
                sizes,
            ),
            return_inverse=True,
        )
        first_touch = np.full(len(keys), len(rows), dtype=np.int64)
        np.minimum.at(first_touch, inverse, rank)
        level = np.zeros((end - start, len(keys)), dtype=np.int32)
        touched = np.zeros((end - start, len(keys)), dtype=np.int32)
        np.add.at(level, (applied - start, inverse), d["delta"][rows])
        np.add.at(touched, (applied - start, inverse), 1)
        np.cumsum(level, axis=0, out=level)
        touched = np.cumsum(touched, axis=0, out=touched) > 0
        listed = np.argsort(first_touch)
        c, m, f, b = (a.tolist() for a in np.unravel_index(keys[listed], sizes))
        families, bands = self._families.names, self._bands.names
        out = []
        for t in charted:
            here = touched[t - start, listed].tolist()
            counts = level[t - start, listed].tolist()
            ts = self._ticks[t]
            out.extend(
                {
                    "ts": ts,
                    "company": companies[c[k]],
                    "metric": LEVEL_METRICS[m[k]],
                    "family": families[f[k]],
                    "band": bands[b[k]],
                    "count": counts[k],
                }
                for k in range(len(listed))
                if here[k]
            )
        return out

    def _with_new_inflow(
        self,
        rows: list[dict],
        inflow_from: str,
        company_of: dict[str, str] | None,
        base_stamp: str | None,
        ats: list[str],
    ) -> list[dict]:
        """``rows`` with each `new` row from ``inflow_from`` on replaced by the jobs Opened over
        the trailing ``NEW_WINDOW_DAYS`` (ADR-0227), in the same Boards' scope: the picks', a
        comparable cohort's, or the index's. Opened already leaves out a found Board's backlog,
        duplicates and reclassified jobs (Recounted), so it is summed as it is."""
        charted = sorted({r["ts"] for r in rows if r["metric"] == "stock"})
        with_company = bool(rows) and "company" in rows[0]
        if company_of is None and base_stamp is None:
            opened = [
                (r, None)
                for r in self._index_turnover
                if r["metric"] == "opened" and (not ats or r["ats"] in ats)
            ]
        else:
            boards = (
                {b: pick for b, pick in company_of.items() if _in_ats_scope(b, ats)}
                if company_of is not None
                else {b: "" for b in self._turnover if _in_ats_scope(b, ats)}
            )
            if base_stamp is not None:
                boards = {
                    b: pick
                    for b, pick in boards.items()
                    if self._in_cohort(b, base_stamp)
                }
            opened = [
                (r, pick)
                for b, pick in boards.items()
                for r in self._turnover.get(b, ())
                if r["metric"] == "opened"
            ]
        opened.sort(key=lambda pair: pair[0]["ts"])
        inflow = [
            r for r in rows if not (r["metric"] == "new" and r["ts"] >= inflow_from)
        ]
        window: Counter[tuple[str | None, str, str]] = Counter()
        entered = left = 0
        for ts in charted:
            if ts < inflow_from:
                continue
            start = (
                datetime.fromisoformat(ts) - timedelta(days=NEW_WINDOW_DAYS)
            ).isoformat(timespec="seconds")
            while entered < len(opened) and opened[entered][0]["ts"] <= ts:
                r, pick = opened[entered]
                window[(pick, r["family"], r["band"])] += r["delta"]
                entered += 1
            while left < entered and opened[left][0]["ts"] <= start:
                r, pick = opened[left]
                window[(pick, r["family"], r["band"])] -= r["delta"]
                left += 1
            for (pick, family, band), count in window.items():
                if count:
                    row = {"ts": ts, "metric": "new", "family": family, "band": band}
                    if with_company:
                        row["company"] = pick
                    inflow.append({**row, "count": count})
        return inflow

    # ---- turnover, removals and companies ----------------------------------------------------

    def _turnover_series(
        self,
        rows: list[tuple[dict, str]],
        stamps: list[str],
        line_of,
        names,
        left_out: tuple[set[int], set[int]] = (set(), set()),
    ) -> dict[str, dict[str, list[int | None]]]:
        """The turnover of each line in ``names`` at each charted run (ADR-0227): ``{line:
        {opened, closed, recounted}}``, each list aligned to ``stamps``. ``recounted`` is in less
        out, so on every run ``opened − closed + recounted`` is the line's change in openings.
        ``rows`` pairs each turnover row in scope with its pick. ``line_of(row, pick)`` names the
        line a row belongs to, or returns None to leave the row out.

        A tick's turnover lands on the first charted run at or after it, because it counts what
        happened since the run before. The first charted run is None: what landed there happened
        before the window. So is every run before turnover began, since nothing measured it.

        ``left_out`` is :func:`trend_netting.left_out_runs`' pair: runs None on every line, and runs where a
        row duplicate removal can move (``touched``) is not counted.
        """
        every, touched = left_out
        first = max(
            bisect_left(stamps, self._turnover_since)
            if self._turnover_since
            else len(stamps),
            1,
        )
        blank = [None] * first + [0] * (len(stamps) - first)
        lines = {name: {m: list(blank) for m in _TURNOVER_KINDS} for name in names}
        for row, pick in rows:
            k = bisect_left(stamps, row["ts"])
            line = lines.get(line_of(row, pick))
            if not 0 < k < len(stamps) or line is None:
                continue
            if k in every or (row.get("touched") and k in touched):
                continue
            kind, sign = _TURNOVER_KIND_OF[row["metric"]]
            line[kind][k] = (line[kind][k] or 0) + sign * row["delta"]
        for line in lines.values():
            for values in line.values():
                for k in every:
                    values[k] = None
        return lines

    def _closures_unseen(
        self, boards: dict[str, str], stamps: list[str]
    ) -> dict[str, int]:
        """Per pick, how many of its Boards had a run inside the window whose scrape could not
        show an absence (ADR-0053), so the closures on it went uncounted that run (ADR-0227)."""
        seen: dict[str, set[str]] = defaultdict(set)
        if not stamps:
            return {}
        for board, pick in boards.items():
            if any(
                stamps[0] < r["ts"] <= stamps[-1]
                for r in self._unscoped_markers.get(board, ())
            ):
                seen[pick].add(board)
        return {pick: len(found) for pick, found in seen.items()}

    def _in_cohort(self, board: str, base_stamp: str | None) -> bool:
        """Whether a comparable cohort based at ``base_stamp`` (ADR-0143) holds ``board``: a
        Board first counted at or before the base. With no base, every Board is in scope."""
        return base_stamp is None or (
            board in self._board_arrivals
            and self._board_arrivals[board][0] <= base_stamp
        )

    def _picks_evicted(self, counted: dict[str, str], stamps: list[str]) -> list[dict]:
        """``[{ts, company, count}]``: each pick's duplicate removals at the charted run that
        shows them — the first at or after the removal's own stamp, normally that stamp."""
        if not stamps:
            return []
        at: Counter = Counter()
        for board, pick in counted.items():
            for ts, count in self._evictions.get(board, ()):
                k = bisect_left(stamps, ts)
                if 0 < k < len(stamps) and count:
                    at[(stamps[k], pick)] += count
        return [
            {"ts": ts, "company": pick, "count": n}
            for (ts, pick), n in sorted(at.items())
        ]

    def _company_dedup_touched(self, board: str) -> bool:
        """Whether duplicate removal can move the directory company holding ``board``."""
        boards = (
            self._companies[self._company_of[board]]["boards"]
            if board in self._company_of
            else [board]
        )
        return trend_netting.dedup_touched(boards)

    def _company_openings(self, entry: dict) -> int:
        return sum(self._openings[board] for board in entry["boards"])

    def _company_json(self, key: str, label: str) -> dict:
        """A directory company as the picker and the chart show it."""
        entry = self._companies[key]
        return {
            "key": key,
            "name": entry["name"],
            "label": label,
            "atses": sorted({ats_of(board) for board in entry["boards"]}),
            "boards": len(entry["boards"]),
            "openings": self._company_openings(entry),
        }

    def _company_labels(self, keys: list[str]) -> dict[str, str]:
        """Each company's name, told apart from any other in ``keys`` that shares it.

        The directory keeps same-named employers apart when nothing proves them one (ADR-0185),
        so "Citi" on Workday and "Citi" on Eightfold both appear, labelled by ATS. Two on the
        *same* ATS (220 name pairs measured) are labelled by their key, the one thing they
        cannot share.
        """
        names = Counter(self._companies[key]["name"] for key in keys)
        with_ats = {
            key: f"{self._companies[key]['name']} ("
            f"{', '.join(sorted({ats_of(b) for b in self._companies[key]['boards']}))})"
            for key in keys
        }
        still_shared = Counter(with_ats.values())
        labels = {}
        for key in keys:
            name = self._companies[key]["name"]
            if names[name] == 1:
                labels[key] = name
            elif still_shared[with_ats[key]] == 1:
                labels[key] = with_ats[key]
            else:
                labels[key] = f"{name} ({key})"
        return labels
