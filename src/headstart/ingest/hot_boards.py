"""Rank the Boards that are hiring hardest right now, for the Hot tab.

Reads what ``role_trends`` already wrote — the ADR-0143 Board-count snapshot and its append-only
delta ledger — and emits a small ranked artifact the Space serves as a static file. Nothing is
computed at query time: the ledgers are tens of megabytes and the answer changes once per run,
so ranking here and serving JSON is the shape the storage and free-tier budget can afford.

Runs after ``role_trends`` in the back-to-back run, because it consumes that stage's output.

## Three lenses, because "actively hiring" is three questions

``expansion`` — net change in open roles across the trailing ``WINDOW_DAYS``. *Who is actually
growing.*
The default: over the 7 days to 2026-09-21 Amazon opened **1,396** roles at a net change of
**+20** — churn at a near-constant size rather than growth, which only this lens says.

``volume`` — the rolling 7-day count of roles first seen inside the window. *Where the most
opportunity is right now.* Always led by the largest employers.

``rate`` — that same count as a share of the Board's open roles. *Who is moving fast for their
size*, which is the only lens that surfaces a small company a user would never otherwise find.

## Four traps, each of which silently produces a plausible wrong list

**The first delta tick is a baseline dump, not a change.** The ledger began with no prior
snapshot, so its first file emits every Board's whole stock as a delta — 196,824 rows against
the next tick's 127. Summing from it reports every Board on the index as brand new; it did, on
the first run of the prototype behind this module.

**A Board whose entire stock arrives inside the window was newly *discovered*, not newly
hiring.** Over the whole ledger and an 8-day span that was 6,996 Boards, a fifth of it; scoped
as this stage scopes it — the trailing 7 days, Boards at or above ``MIN_STOCK`` — it was 108 on
2026-09-21. Either way they would own every lens. ADR-0143 exists for this confound; the
exclusion here is its Hot-tab-shaped equivalent, and its count ships in the artifact rather than
being quietly applied.

**``watch:`` families double-count** against centroid families (ADR-0051), so they are dropped
from every total.

**``new`` is a rolling 7-day level, not per-tick inflow** (ADR-0051). It is read as a level and
never summed across ticks. ``stock`` deltas *are* per-tick changes and are summed.

## What this cannot answer yet

"Which company *started* hiring recently" — acceleration — needs a before and an after, and the
delta ledger only began on 2026-09-13. Do not approximate it from ``first_seen``: that records
when **we** indexed a row, not when the employer opened it, so a newly scraped Board would read
as a hiring surge. The ledger accrues every run; the question becomes answerable by waiting.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from headstart import log
from headstart.ingest.board_operator import classify

# `__spec__` as well as `__name__`, like every other module that doubles as a `python -m`
# entry point: run that way `__name__` is "__main__", which falls outside the `headstart`
# root that `setup()` configures, and every line is discarded. Missing both this and
# `setup()` below, the stage ran to completion in total silence.
_log = log.get(__name__, __spec__)

REPO_ROOT = Path(__file__).resolve().parents[3]
_BOARD_COUNTS = REPO_ROOT / "data" / "state" / "role_trend_board_counts.parquet"
_BOARD_DELTAS = REPO_ROOT / "data" / "state" / "role_trend_board_deltas"
_OUT = REPO_ROOT / "data" / "state" / "hot_boards.json"
_DB = REPO_ROOT / "data" / "lancedb"

#: Rows kept per lens. Enough to scroll, small enough that the artifact stays a few tens of KB
#: and that the Boards on it could be adjudicated by hand — which is the stated way to improve
#: `board_operator` beyond its curated head (see that module's docstring).
TOP_N = 100

#: A Board below this many open roles is not ranked. Its lens values are dominated by noise —
#: one posting on a three-posting Board is a 33% hiring rate — and the Rate lens in particular
#: becomes a list of tiny Boards that happened to post once.
MIN_STOCK = 25

#: A Board is treated as newly discovered, not newly hiring, when this share of its current
#: stock arrived inside the delta window. Not 1.0: a Board first seen mid-window usually also
#: closes a posting or two before the window ends, which would put its ratio just under a
#: strict equality and let it back onto every lens.
NEWLY_FOUND_SHARE = 0.9

#: The trailing window Expansion is measured over. Seven days, to match the `new` metric's own
#: rolling window (ADR-0051's NEW_WINDOW_DAYS) — the two are printed on the same row, so they
#: have to describe the same length of time or the row compares a week against a month.
WINDOW_DAYS = 7

_WATCH = "watch:"  # headstart.roles.WATCH_PREFIX; double-counts (ADR-0051)


def read_levels(path: Path) -> tuple[collections.Counter, collections.Counter]:
    """Current per-Board ``new`` (a rolling 7-day level) and ``stock`` totals."""
    import pyarrow.parquet as pq

    table = pq.read_table(path).to_pydict()
    new: collections.Counter = collections.Counter()
    stock: collections.Counter = collections.Counter()
    for board, metric, family, count in zip(
        table["board"], table["metric"], table["family"], table["count"], strict=True
    ):
        if family.startswith(_WATCH):
            continue
        (new if metric == "new" else stock)[board] += count
    return new, stock


def read_stock_change(delta_dir: Path) -> tuple[collections.Counter, list[str]]:
    """Net per-Board stock change over the trailing window, and the tick stamps it covers.

    **The window is bounded to the same span as ``new``, and that is the point.** An unbounded
    sum grows by one run every run, so Expansion would quietly measure a longer period each
    time while Volume stayed a rolling 7-day level — and a row prints the two side by side.
    Within a day of shipping, "+1,010 net roles" and "924 opened this week" would have described
    different lengths of time under one heading.

    The ledger's first tick is dropped as well. It is a baseline dump of every Board's whole
    stock rather than a change (196,824 rows against the next tick's 127), and while the ledger
    is younger than the window it would otherwise fall inside it. It is identified by position,
    which is safe *here*: this stage runs on the merge VM immediately after the stage that
    writes the directory, so the directory is complete. A caller reading a partially fetched
    copy would mistake its oldest present tick for the baseline and lose one real measurement.

    **Only the newest tick's ``centroid_version`` is summed** (ADR-0040/ADR-0143). A refit finds no
    Board counts at its own version, so `role_trends` writes that version a fresh baseline of
    every Board's stock; summed across versions it made every Board "newly discovered" for a week.
    The same by-position rule drops the current version's first tick as its baseline.

    The returned stamps describe the window actually measured, never the window intended — a tab
    claiming a week over two days of data would be a lie the data can already tell.
    """
    import pyarrow.parquet as pq

    # (path, first stamp) built as pairs, so a tick that somehow holds no rows drops out of
    # both lists together. Reading the two separately and zipping them `strict=True` was only
    # correct because `role_trends` never writes an empty tick — a coupling to another module's
    # behaviour that nothing here would have shown.
    ticks = []
    for path in sorted(delta_dir.glob("*.parquet")):
        table = pq.read_table(path, columns=["ts"])
        stamps_in_file = table.column("ts").to_pylist()
        if stamps_in_file:
            version = (table.schema.metadata or {}).get(b"centroid_version")
            ticks.append((path, stamps_in_file[0], version))
    if not ticks:
        return collections.Counter(), []
    ticks = [t[:2] for t in ticks if t[2] == ticks[-1][2]]
    newest = max(ts for _, ts in ticks)
    cutoff = (datetime.fromisoformat(newest) - timedelta(days=WINDOW_DAYS)).isoformat()

    moved: collections.Counter = collections.Counter()
    stamps: list[str] = []
    for path, first_ts in ticks[1:]:
        if first_ts < cutoff:
            continue
        table = pq.read_table(path).to_pydict()
        for board, metric, family, delta, ts in zip(
            table["board"],
            table["metric"],
            table["family"],
            table["delta"],
            table["ts"],
            strict=True,
        ):
            if metric == "stock" and not family.startswith(_WATCH):
                moved[board] += delta
                stamps.append(ts)
    return moved, stamps


def board_names(db: Path, table_name: str) -> dict[str, str]:
    """``board_key -> the company name its rows carry``, or an empty map if unreadable.

    A display name is the whole point of a company leaderboard, but it is missing more often
    than not: only ~20% of served rows sit on an ATS that resolves one (ADR-0114), so most
    Boards fall back to their slug. Unreadable rather than fatal — a ranking of slugs is worse
    than a ranking of names and better than no tab.

    This does scan the whole table to name at most a few hundred displayed rows, which is worth
    stating rather than hiding. It is affordable because of where it runs: `role_trends`, the
    stage immediately before this one on the same merge VM, already reads every row *including
    the 768-d vector column*. Two string columns over the same rows is strictly cheaper than a
    step the run has just paid for. If this ever moves off that VM, revisit it.
    """
    try:
        import lancedb

        handle = lancedb.connect(str(db)).open_table(table_name)
        rows = (
            handle.search()
            .select(["id", "company"])
            .limit(handle.count_rows())
            .to_arrow()
        )
    except Exception as exc:  # noqa: BLE001 - a missing or half-written table must not be fatal
        _log.warning(
            f"no company names for the hot list ({exc}); falling back to slugs"
        )
        return {}
    names: dict[str, str] = {}
    for job_id, company in zip(
        rows["id"].to_pylist(), rows["company"].to_pylist(), strict=True
    ):
        if company:
            names.setdefault(job_id.rsplit(":", 1)[0], company)
    return names


#: Host labels that name the *board* rather than the company, and so are never the answer.
#: Vendor labels and TLDs sit here too: `micron.wd5.myworkdayjobs.com` and
#: `lockheed.jobs.hr.cloud.sap` both have to reduce to their first real word.
_LABEL_NOISE = frozenset(
    {
        "www",
        "careers",
        "career",
        "jobs",
        "job",
        "apply",
        "hire",
        "hiring",
        "talent",
        "work",
        "working",
        "recruiting",
        "recruitment",
        "internal",
        "internaljobs",
        "external",
        "search",
        "inc",
        "ltd",
        "llc",
        "corp",
        "group",
        "global",
        "en",
        "us",
        # vendor hosts and the public suffixes behind them
        "myworkdayjobs",
        "icims",
        "eightfold",
        "zohorecruit",
        "openings",
        "taleo",
        "tbe",
        "oraclecloud",
        "ocs",
        "fa",
        "sap",
        "cloud",
        "hr",
        "wd",
        "smartrecruiters",
        "com",
        "net",
        "org",
        "io",
        "co",
        "ai",
        "in",
        "eu",
        "uk",
        "de",
        "ca",
    }
)
_WD_POD = re.compile(r"^wd\d+$")  # micron.wd5.myworkdayjobs.com


#: Hand-written names for Boards whose slug cannot produce one, keyed by board_key.
#:
#: Same principle as `board_operator`'s curated list and the same bounded scope: these are
#: Boards seen at the *head of a lens*, where a wrong name is read by everyone. The slug simply
#: does not carry the company — `workday:bah/BAH_Jobs` is Booz Allen Hamilton and
#: `workday:globalhr/REC_RTX_Ext_Gateway` is RTX — and no derivation recovers that.
#:
#: It also does the work cross-ATS identity would: Lockheed Martin reaches the Expansion lens
#: on **both** Eightfold and SuccessFactors, and before this map they ranked first and second
#: as "Lockheed Martin" and "Lockheed". Mapping both to one name lets `_collapse_same_company`
#: see them as one company. That is a display-level patch over a real gap — the index has no
#: cross-ATS Board identity (`index_plan.evict_duplicate` groups *within* a Board) — so a pair
#: not listed here still shows twice. Add pairs as the head of a lens surfaces them.
DISPLAY_ALIASES: Final[dict[str, str]] = {
    "eightfold:lockheedmartin.eightfold.ai": "Lockheed Martin",
    "successfactors:lockheed.jobs.hr.cloud.sap": "Lockheed Martin",
    "workday:globalhr/REC_RTX_Ext_Gateway": "RTX",
    "workday:bah/BAH_Jobs": "Booz Allen Hamilton",
    "workday:swa/external": "Southwest Airlines",
    "workday:caci/external": "CACI",
    "workday:gdit/External_Career_Site": "GDIT",
    "workday:ngc/Northrop_Grumman_External_Site": "Northrop Grumman",
    "successfactors:careers.hcltech.com": "HCLTech",
    "successfactors:careers.capgemini.com": "Capgemini",
    "successfactors:careers.wipro.com": "Wipro",
    "successfactors:careers-inc.nttdata.com": "NTT Data",
}


def display_name(company: str, board: str) -> str:
    """A name a person can read, without inventing one.

    Four rows in five carry an ATS slug rather than a resolved company name (ADR-0114), and a
    slug is very often a hostname — `careers.wipro.com`, `careers-inc.nttdata.com` — which on a
    company leaderboard reads as a bug. (`www.amazon.jobs` was the stock example until the eight
    Single source scrapers began declaring `BaseScraper.COMPANY`; they now arrive named, so this
    function no longer has to rescue them.) This drops the labels of a host that name the board or the
    vendor and keeps the first that names the company.

    Picking the *first non-noise label* rather than the registrable domain is deliberate, and
    both conventions appear in the data: `careers-inc.nttdata.com` puts the company second,
    while `lockheed.jobs.hr.cloud.sap` puts it first. Taking the label before the public suffix
    reads the latter as "Cloud"; taking the first label reads the former as "Careers-Inc".

    It stops at tidying. `swa.wd1.myworkdayjobs.com/external` becomes "Swa" and not "Southwest
    Airlines", because that expansion is not in the data and a leaderboard that guesses company
    names is worse than one that shows an honest slug. Anything already mixed-case is returned
    untouched, so `CI&T` and `HCLTech` survive.
    """
    alias = DISPLAY_ALIASES.get(board)
    if alias:
        return alias
    stated = (company or "").strip()
    if stated and stated != stated.lower():
        return stated  # a real, cased company name — never re-case or trim it
    # Everything below tidies a *slug*. The cased-name guard above must not reach it: a slug
    # carries capitals of its own (`micron/External`), and treating those as a company name
    # returned the raw slug, path and all.
    name = stated or board.split(":", 1)[-1]
    head = name.split("/", 1)[0]
    if "." in head:
        labels = [
            label
            for label in head.split(".")
            if label and label not in _LABEL_NOISE and not _WD_POD.match(label)
        ]
        # A prefixed label still carries the company after its noise word (`careers-inc` ->
        # `inc`, dropped above; `jobs-bylight` -> `bylight`).
        for label in labels:
            parts = [p for p in label.split("-") if p and p not in _LABEL_NOISE]
            if parts:
                head = "-".join(parts)
                break
        else:
            head = labels[0] if labels else head
    return head.replace("-", " ").replace("_", " ").strip().title() or name


def _collapse_same_company(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per company within a lens, keeping the best-ranked Board.

    An employer routinely holds several Boards — EWOR sits on Teamtailor *and* Recruitee, and
    both reach the Rate lens, so an uncollapsed list spends two of its top five rows on one
    company. Cross-ATS dedup does not exist in the index (``index_plan.evict_duplicate`` groups
    within a Board), so this is a display-level fix and is scoped to the display: it drops the
    weaker row rather than merging the two Boards' counts, because adding them would double-count
    any posting the company lists on both.

    Callers pass rows already sorted by the lens, so "first seen wins" is "best-ranked wins".
    """
    seen: set[str] = set()
    kept = []
    for row in rows:
        key = display_name(row["company"], row["board"]).lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return kept


def rank(
    new: collections.Counter,
    stock: collections.Counter,
    moved: collections.Counter,
    names: dict[str, str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """The three lenses, plus the counts of what was ranked and what each exclusion removed.

    Exclusions are counted and returned rather than silently applied: a tab that quietly drops a
    fifth of the ledger should say so, and the numbers are how anyone checks this stage is
    behaving. Every candidate Board is scored once and the three lenses sort the same rows, so a
    Board cannot appear as an employer on one lens and a services firm on another.
    """
    candidates, newly_found = [], 0
    for board, open_roles in stock.items():
        if open_roles < MIN_STOCK:
            continue
        if moved.get(board, 0) >= NEWLY_FOUND_SHARE * open_roles:
            newly_found += 1
            continue
        candidates.append(
            {
                "board": board,
                "ats": board.split(":", 1)[0],
                "company": display_name(names.get(board, ""), board),
                "stock": open_roles,
                "new7": new.get(board, 0),
                "net": moved.get(board, 0),
                # Percent rather than a fraction: it is a display value, and rounding it here
                # keeps every consumer from inventing its own precision.
                "rate": round(100 * new.get(board, 0) / open_roles),
                "operator": classify(board, names.get(board)),
            }
        )
    # Collapse *before* the cut, never after: trimming to TOP_N first would let a company's
    # second Board occupy a slot that then disappears, leaving a lens of fewer than TOP_N rows.
    lenses = {
        "expansion": _collapse_same_company(
            sorted((c for c in candidates if c["net"] > 0), key=lambda c: -c["net"])
        )[:TOP_N],
        "volume": _collapse_same_company(
            sorted((c for c in candidates if c["new7"] > 0), key=lambda c: -c["new7"])
        )[:TOP_N],
        "rate": _collapse_same_company(
            sorted((c for c in candidates if c["new7"] > 0), key=lambda c: -c["rate"])
        )[:TOP_N],
    }
    shown = {c["board"]: c for lens in lenses.values() for c in lens}
    counted = collections.Counter(c["operator"] for c in shown.values())
    # Named `counts`, not `excluded`: it holds what was *kept* as well as what was dropped, and
    # a key called `excluded["ranked"]` reads as the opposite of the number it carries.
    counts = {
        "ranked": len(candidates),
        "newly_discovered": newly_found,
        "below_min_stock": sum(1 for v in stock.values() if v < MIN_STOCK),
        # The threshold travels with the counts it explains. The UI prints "fewer than N open
        # roles", and with N hardcoded there, changing MIN_STOCK would leave that sentence
        # quietly stating a number the ranking no longer uses.
        "min_stock": MIN_STOCK,
        "services": counted["services"],
        "aggregator": counted["aggregator"],
    }
    return lenses, counts


def main() -> int:
    # `setup()` before `context()`: without it nothing this stage logs is ever emitted, so a
    # silently skipped Hot list looked exactly like a successful run in CI.
    log.setup()
    log.context("hot_boards")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--board-counts", type=Path, default=_BOARD_COUNTS)
    ap.add_argument("--board-deltas", type=Path, default=_BOARD_DELTAS)
    ap.add_argument("--db", type=Path, default=_DB)
    ap.add_argument("--out", type=Path, default=_OUT)
    args = ap.parse_args()

    # Both ledgers are role_trends' output, and that stage is `continue-on-error`: a run where
    # it skipped leaves them absent, which is a missing prerequisite rather than a defect here.
    if not args.board_counts.exists() or not any(args.board_deltas.glob("*.parquet")):
        _log.warning(
            f"skipping the hot list — {args.board_counts} or {args.board_deltas} is missing "
            "(role_trends writes both; it may have skipped this run)"
        )
        return 0

    from headstart.search import PROD_TABLE

    new, stock = read_levels(args.board_counts)
    moved, stamps = read_stock_change(args.board_deltas)
    if not stamps:
        # One delta file exists and it is the baseline. There is no measured change yet, and a
        # lens built on the baseline would rank every Board as newly created.
        _log.warning(
            "only the baseline delta tick exists — no measured window, no hot list"
        )
        return 0
    lenses, counts = rank(new, stock, moved, board_names(args.db, PROD_TABLE))

    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "window": {"from": min(stamps), "to": max(stamps)},
        "lenses": lenses,
        "counts": counts,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    _log.info(
        f"hot list: {counts['ranked']} Boards ranked, "
        f"{counts['newly_discovered']} newly discovered excluded, "
        f"{counts['services']} services and {counts['aggregator']} aggregators labelled "
        f"across the window {payload['window']['from']} -> {payload['window']['to']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
