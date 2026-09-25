"""Tests for the scrape planner (headstart.ingest.scrape_plan, ADR-0026).

The per-Board cost weighting (detail-fetch ATSes cost more) and the partition invariant — every
selected Board lands in exactly one shard — are the logic worth locking down. ``main`` is run with a
monkeypatched active-list so the test doesn't couple to the liveness-ledger CSV format.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime, timedelta

import pytest

import headstart.ingest.scrape_plan as ps
from headstart.board_identity import board_identity
from headstart.ingest import board_failures as bf
from headstart.scrapable_boards import ScrapableBoard


def test_coldstart_cost_weights_detail_fetchers():
    assert (
        ps._coldstart_cost("workday", 10.0) == 10.0 * ps._DETAIL_WEIGHT
    )  # detail-fetch ATS
    assert ps._coldstart_cost("lever", 10.0) == 10.0  # list-only ATS, weight 1
    assert (
        ps._coldstart_cost("greenhouse", 0.0) == ps._EXPLORE_BASELINE
    )  # unscored -> baseline floor


def test_main_partitions_every_selected_board(tmp_path, monkeypatch):
    boards = [
        ScrapableBoard("workday", "big", "Big"),
        ScrapableBoard("lever", "acme", "Acme"),
        ScrapableBoard("greenhouse", "co", "Co"),
        ScrapableBoard("keka", "startup", "Startup"),
        ScrapableBoard("lever", "other", "Other"),
    ]
    monkeypatch.setattr(
        ps.scrapable_boards, "load", lambda ledger, min_jobs=0: list(boards)
    )
    out = tmp_path / "assignments"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scrape_plan",
            "--priority",
            str(tmp_path / "none.csv"),
            "--cost",
            str(tmp_path / "nocost.csv"),  # isolate from any real local ledger
            "--out-dir",
            str(out),
            "--max-boards",
            "0",
            "--max-shards",
            "3",
            "--target-boards",
            "2",
        ],
    )
    assert ps.main() == 0

    plan = json.loads((out / "plan.json").read_text())
    assert plan["count"] == 5
    assert plan["shards"] == [0, 1, 2]  # ceil(5/2) = 3 shards
    assert sum(plan["per_shard_boards"]) == 5

    seen = []
    for k in plan["shards"]:
        for line in (out / f"shard-{k}.jsonl").read_text().splitlines():
            rec = json.loads(line)
            assert set(rec) == {"ats", "slug", "name"}
            seen.append(f"{rec['ats']}:{rec['slug']}")
    # every selected board assigned exactly once
    assert sorted(seen) == sorted(f"{c.ats}:{c.slug}" for c in boards)


def test_main_empty_plan_when_no_boards(tmp_path, monkeypatch):
    monkeypatch.setattr(ps.scrapable_boards, "load", lambda ledger, min_jobs=0: [])
    out = tmp_path / "assignments"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scrape_plan",
            "--priority",
            str(tmp_path / "none.csv"),
            "--out-dir",
            str(out),
        ],
    )
    assert ps.main() == 0
    assert json.loads((out / "plan.json").read_text()) == {
        "shards": [],
        "count": 0,
        "per_shard_boards": [],
    }


def test_plan_ships_the_detail_skip_list_to_the_shards(tmp_path, monkeypatch):
    """The list rides inside the assignments artifact every shard already downloads (ADR-0048/0050),
    under the name the shard looks for — not the name it happened to have on disk."""
    import gzip

    from headstart.ingest import HELD_DETAILS_PATH

    src = tmp_path / "named-something-else.txt.gz"
    with gzip.open(src, "wt", encoding="utf-8") as fh:
        fh.write("eightfold:acme:1\n")
    out = tmp_path / "assignments"

    monkeypatch.setattr(
        ps.scrapable_boards,
        "load",
        lambda ledger, min_jobs=0: [ScrapableBoard("lever", "a", "A")],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scrape_plan",
            "--priority",
            str(tmp_path / "none.csv"),
            "--cost",
            str(tmp_path / "nocost.csv"),
            "--out-dir",
            str(out),
            "--held-details",
            str(src),
            "--max-boards",
            "0",
            "--max-shards",
            "1",
            "--target-boards",
            "1",
        ],
    )
    assert ps.main() == 0

    shipped = out / HELD_DETAILS_PATH.name
    assert shipped.exists(), "the shard looks for this exact name"
    with gzip.open(shipped, "rt", encoding="utf-8") as fh:
        assert fh.read().strip() == "eightfold:acme:1"


def _cost(seconds: float, day: str = "2026-08-18", jobs: int = 1):
    """A cost-ledger row for the gate to judge.

    ``jobs`` defaults to 1, not 0, and the difference is load-bearing since the gate began reading
    it: a row saying ``jobs=0`` now means "a completed scrape of this Board returned nothing" and
    is vetoed outright. Every fixture here that is *about* the score/seconds ratio wants a Board
    that did return something, so 1 is the neutral default and 0 has to be asked for.
    """
    from headstart.board_cost import BoardCost

    return BoardCost(seconds=seconds, jobs=jobs, updated_at=day)


def test_the_gate_drops_a_giant_board_that_yields_almost_no_tech():
    """The 2026-08-18 measurement this gate exists for.

    `workday:dollartree/dollartreeus` holds 24,017 postings, needs ~67 min to page at Workday's
    20-per-page cap — more than a shard's whole 60 min budget — and scores 9.7 from 10 tech jobs.
    It cannot finish, so it kills a shard, defers, and is re-drawn next run. Cost per unit of
    value is the honest way to say that: 0.2 tech jobs per minute of shard time.
    """
    gated = ps._gated_boards(
        ["workday:dollartree"],
        {"workday:dollartree": _cost(4000.0)},
        {"workday:dollartree": 9.7},
        today="2026-08-18",
    )
    assert "workday:dollartree" in gated


def test_the_gate_keeps_a_giant_board_that_earns_its_hour():
    """Walmart is just as big and just as slow — 15,476 postings, 44.5 min — and returns 903 tech
    jobs for it. A rule that dropped this too would be a volume cap, not a value gate."""
    gated = ps._gated_boards(
        ["workday:walmart"],
        {"workday:walmart": _cost(2670.0)},
        {"workday:walmart": 903.9},
        today="2026-08-18",
    )
    assert gated == {}


def test_the_gate_drops_a_ten_minute_board_that_yields_almost_no_tech():
    """`jibe:petsmart`, 2026-09-24: 760 s at a score of 2.8 (4 tech jobs), 0.22 a minute. Under
    the old 15 min floor it sat unjudged while shards now finish in ~9 min, so it set the scrape
    stage's wall clock once Costco was gone (ADR-0064's 2026-09-24 amendment)."""
    gated = ps._gated_boards(
        ["jibe:petsmart"],
        {"jibe:petsmart": _cost(760.0, day="2026-09-24")},
        {"jibe:petsmart": 2.8},
        today="2026-09-24",
    )
    assert "jibe:petsmart" in gated


def test_the_gate_leaves_a_board_under_ten_minutes_alone():
    """The floor moved, it did not vanish: a nine-minute Board is under it however little it
    yields, so the long tail stays out of the gate's business."""
    gated = ps._gated_boards(
        ["jibe:nine"],
        {"jibe:nine": _cost(590.0, day="2026-09-24")},
        {"jibe:nine": 0.5},
        today="2026-09-24",
    )
    assert gated == {}


def test_the_gate_never_touches_a_cheap_board():
    """Almost the whole corpus: a Board too cheap to threaten the makespan is not the gate's
    business however little it yields, and gating on yield alone would gut the long tail."""
    gated = ps._gated_boards(
        ["lever:tiny"],
        {"lever:tiny": _cost(3.0)},
        {},  # unscored, zero tech jobs — and still none of the gate's business
        today="2026-08-18",
    )
    assert gated == {}


def test_the_gate_drops_a_board_whose_measured_yield_is_zero_however_high_its_score():
    """The blind spot that cost 102 Boards five runs of silence — see ADR-0115's writeup.

    `successfactors:careers.te.com` burned 1,631 s a run and returned **0 jobs**, five runs
    running, while carrying priority score 171.7. On the ratio alone that is 6.32 tech/min — well
    clear of the 2.0 threshold — so the gate kept re-packing a Board that produced nothing and set
    the whole scrape stage's makespan doing it.

    The score is not stale by accident. `update_ledgers.priority` builds its snapshot from *rows in
    the scraped jobs file*, so a Board that scrapes and yields nothing contributes no row, lands in
    `update_priority`'s "absent from the snapshot — carry unchanged" branch, and keeps its last
    good score forever. **The collapse this gate exists to catch is exactly what stops it seeing
    one.** Real ledger rows, 2026-09-07: cost `1631s, jobs=0, 2026-09-07` against priority
    `score=171.7, last_tech_jobs=1, 2026-09-04` — the cost row rewritten every run, the priority
    row three days cold.
    """
    gated = ps._gated_boards(
        ["successfactors:careers.te.com"],
        {"successfactors:careers.te.com": _cost(1631.0, "2026-09-07", jobs=0)},
        {"successfactors:careers.te.com": 171.7},
        today="2026-09-07",
    )
    assert gated == {"successfactors:careers.te.com": 0.0}


def test_the_gate_never_drops_a_board_it_has_not_measured():
    """The cost cascade estimates an unmeasured Board from its ATS median, and gating on an
    estimate would drop Boards for their ATS's reputation rather than their own record — every
    unmeasured SuccessFactors board at once, none of them ever measured to disprove it."""
    gated = ps._gated_boards(
        ["successfactors:unknown"],
        {},  # no measurement of its own
        {},
        today="2026-08-18",
    )
    assert gated == {}


def test_a_gated_board_is_re_measured_once_its_costing_goes_stale():
    """What keeps the gate from being a one-way door.

    A gated Board is never scraped, so its cost and score freeze — and a Board judged on frozen
    evidence is judged forever. Letting the measurement expire puts it back in the slice, where
    it is re-measured and re-judged on what it is now, not what it was.
    """
    stale = ps._gated_boards(
        ["workday:dollartree"],
        {"workday:dollartree": _cost(4000.0, day="2026-07-01")},
        {"workday:dollartree": 9.7},
        today="2026-08-18",
    )
    assert stale == {}, "a stale costing must re-admit the board for re-measurement"

    fresh = ps._gated_boards(
        ["workday:dollartree"],
        {"workday:dollartree": _cost(4000.0, day="2026-08-17")},
        {"workday:dollartree": 9.7},
        today="2026-08-18",
    )
    assert "workday:dollartree" in fresh


def test_the_gate_finds_a_workday_giants_score_under_the_one_shared_key():
    """Both ledgers are keyed by `board_identity` since ADR-0096, so a Workday Board — whose slug
    is a whole careers URL — is looked up the same way in each.

    Before that they disagreed, and a gate reading the score under the *cost* key saw every
    Workday giant as zero-yield and dropped them all, walmart included (ADR-0049). The regression
    this guards is now impossible by construction rather than by pairing, so the test asserts the
    outcome: a high-yield giant survives.
    """
    walmart = ScrapableBoard(
        ats="workday", slug="https://walmart.wd504.myworkdayjobs.com/x", name="Walmart"
    )
    key = board_identity(walmart)
    assert key == "workday:walmart/x", "the shared key drops the pod"

    gated = ps._gated_boards(
        [key], {key: _cost(2670.0)}, {key: 903.9}, today="2026-08-18"
    )
    assert gated == {}


def test_a_workday_board_is_costed_under_the_same_key_two_pods_share():
    """The point of one keyspace: `accenture.wd3` and `accenture.wd103` are one Board, so they
    cost-key alike and a tenant migrating between pods keeps its measured history."""
    wd3 = ScrapableBoard(
        ats="workday", slug="https://accenture.wd3.myworkdayjobs.com/careers", name="A"
    )
    wd103 = ScrapableBoard(
        ats="workday",
        slug="https://accenture.wd103.myworkdayjobs.com/careers",
        name="A",
    )
    assert board_identity(wd3) == board_identity(wd103) == "workday:accenture/careers"


def test_floor_warning_compares_wall_clock_not_serial_minutes(
    tmp_path, monkeypatch, caplog
):
    """One board above an even WALL share must be reported as the makespan floor.

    The guard used to compare ``floor`` (wall minutes — what ``predict_minutes`` treats as a
    shard's makespan floor) against ``even`` (SERIAL pack minutes, ~``speedup`` times larger), so
    it asked "23 > 171" and never fired. It stayed silent through all five runs of 2026-09-08 even
    though every one had ``predicted makespan == single-board floor`` to the decimal.

    The board costs are chosen so the OLD comparison stays silent (floor < serial even share) while
    the new one fires (floor > that share divided by the measured speedup) — asserted below, so
    reverting the fix fails this test rather than merely changing a number in it.
    """
    boards = [ScrapableBoard("lever", "giant", "Giant")] + [
        ScrapableBoard("lever", f"small{i}", f"Small{i}") for i in range(9)
    ]
    monkeypatch.setattr(
        ps.scrapable_boards, "load", lambda ledger, min_jobs=0: list(boards)
    )
    # 590 s stays under the ADR-0064 value gate's 10 min bar, so the giant survives into the slice.
    cost = tmp_path / "cost.csv"
    cost.write_text(
        "board,seconds,jobs,updated_at\n"
        f"{board_identity(boards[0])},590.0,900,2026-09-08\n"
        + "".join(f"{board_identity(b)},200.0,50,2026-09-08\n" for b in boards[1:])
    )
    speedup = tmp_path / "speedup.csv"
    speedup.write_text("speedup,shards,updated_at\n13.08,15,2026-09-08\n")

    out = tmp_path / "assignments"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scrape_plan",
            "--priority",
            str(tmp_path / "none.csv"),
            "--cost",
            str(cost),
            "--speedup-ledger",
            str(speedup),
            "--out-dir",
            str(out),
            "--max-boards",
            "0",
            "--max-shards",
            "3",
            "--target-boards",
            "4",
        ],
    )
    with caplog.at_level("INFO"):
        assert ps.main() == 0

    spread = next(
        r.message for r in caplog.records if r.message.startswith("predicted spread:")
    )
    even_serial = float(re.search(r"mean ([\d.]+)", spread).group(1))
    floor = float(re.search(r"single-board floor ([\d.]+)", spread).group(1))
    # The premise that makes this a regression test rather than a restatement of the new code.
    assert floor < even_serial, (
        "the fixture must be one the OLD `floor > even` comparison stayed silent on, "
        f"got floor={floor} even_serial={even_serial}"
    )

    warnings = [
        r.message
        for r in caplog.records
        if "the makespan floor is this board" in r.message
    ]
    assert warnings, (
        f"floor {floor} min exceeds the {even_serial / 13.08:.2f} min even WALL share and must "
        f"be reported; warnings: {[r.message for r in caplog.records if r.levelname == 'WARNING']}"
    )
    reported_share = float(
        re.search(r"above the ([\d.]+) min even share", warnings[0]).group(1)
    )
    assert reported_share == pytest.approx(even_serial / 13.08, abs=0.05), (
        f"the warning must quote the WALL even share, not the serial {even_serial} min one; "
        f"got {reported_share}"
    )


def test_a_zero_yield_board_under_the_floor_is_still_left_alone():
    """The veto rides the existing floor rather than widening the gate's reach.

    A Board that returns nothing in 30 s is not a makespan problem, and this gate is only ever
    about what a shard's slowest item costs. Dropping cheap empty Boards would be a different
    rule — a value gate, not a makespan gate — and it would silently retire every Board between
    hiring rounds.
    """
    gated = ps._gated_boards(
        ["ashby:quiet"],
        {"ashby:quiet": _cost(30.0, "2026-09-07", jobs=0)},
        {"ashby:quiet": 0.0},
        today="2026-09-07",
    )
    assert gated == {}


def test_an_incomplete_measurement_cannot_gate_a_board_it_never_read():
    """The way this veto could have evicted healthy giants — closed at the ledger, and pinned here
    through the REAL `board_cost.update` rather than a hand-built row.

    An earlier version of this test asserted the safety property against a `BoardCost` it
    constructed itself, so it passed without the ledger doing anything and would have passed on
    `main`. It also rested on a false premise: only the *unfinished* branch preserved the count,
    while an **errored** Board wrote its `n_fresh = 0` straight over the last good one — and errors
    run 19-40 a run. `run_one`'s own comment had already named the consequence: "the value gate
    would drop it forever, on a Board that failed instantly".

    Both incomplete outcomes now leave the count alone, so both survive the gate on their ratio.
    """
    from headstart.board_cost import BoardCost, ShardCost, update

    prev = {
        "workday:walmart": BoardCost(
            seconds=2670.0, jobs=15476, updated_at="2026-08-17"
        ),
        "workday:target": BoardCost(seconds=2600.0, jobs=9000, updated_at="2026-08-17"),
    }
    after = update(
        prev,
        {
            # burned an hour, then raised
            "workday:walmart": ShardCost(seconds=3600.0, jobs=0, errored=True),
            # killed by the shard's time budget mid-fetch
            "workday:target": ShardCost(seconds=3600.0, jobs=0, unfinished=True),
        },
        looked_at="2026-08-18",
    )
    assert after["workday:walmart"].jobs == 15476
    assert after["workday:target"].jobs == 9000

    gated = ps._gated_boards(
        ["workday:walmart", "workday:target"],
        after,
        {"workday:walmart": 903.9, "workday:target": 400.0},
        today="2026-08-18",
    )
    assert gated == {}


def test_a_board_with_no_known_yield_is_judged_on_its_ratio_not_vetoed():
    """`jobs=None` means "no complete scrape has ever measured this Board", which is not zero.

    A Board whose only sighting failed, or whose first sighting was a budget kill, lands here. The
    veto must not fire on it — that would turn one bad first run into a fortnight's exclusion — so
    it falls through to ADR-0064's ratio exactly as before this change.
    """
    from headstart.board_cost import BoardCost

    unknown = {
        "workday:new": BoardCost(seconds=1200.0, jobs=None, updated_at="2026-09-07")
    }
    assert (
        ps._gated_boards(
            ["workday:new"], unknown, {"workday:new": 500.0}, today="2026-09-07"
        )
        == {}
    )
    # ...and still gated when the ratio itself is poor, so the fall-through is not an escape hatch
    assert ps._gated_boards(
        ["workday:new"], unknown, {"workday:new": 1.0}, today="2026-09-07"
    )


def test_a_stale_quarantine_is_re_admitted_for_one_run(tmp_path, monkeypatch, caplog):
    """Quarantine is evidence with an age, not a one-way door (ADR-0161).

    Before parole existed, `scrape_plan` dropped every Board at/over QUARANTINE_AT strikes
    unconditionally — so it never scraped, never entered `produced`, and `board_failures.update`'s
    clearing branch was unreachable. Both Boards below would have been skipped; only the one whose
    verdict is still fresh may be.
    """
    boards = [
        ScrapableBoard("greenhouse", "stale", "Stale"),
        ScrapableBoard("greenhouse", "fresh", "Fresh"),
        ScrapableBoard("greenhouse", "alive", "Alive"),
    ]
    monkeypatch.setattr(
        ps.scrapable_boards, "load", lambda ledger, min_jobs=0: list(boards)
    )
    now = datetime.now(UTC)
    stale = (now - timedelta(days=bf.PAROLE_DAYS + 1)).isoformat(timespec="seconds")
    fresh = (now - timedelta(days=1)).isoformat(timespec="seconds")
    failures = tmp_path / "board_failures.csv"
    bf.save(
        failures,
        {
            "greenhouse:stale": bf.Failure(bf.QUARANTINE_AT, "404", stale),
            "greenhouse:fresh": bf.Failure(bf.QUARANTINE_AT, "404", fresh),
        },
    )

    out = tmp_path / "assignments"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scrape_plan",
            "--priority",
            str(tmp_path / "none.csv"),
            "--cost",
            str(tmp_path / "nocost.csv"),
            "--failures",
            str(failures),
            "--out-dir",
            str(out),
            "--max-boards",
            "0",
            "--max-shards",
            "2",
            "--target-boards",
            "2",
        ],
    )
    with caplog.at_level("INFO"):
        assert ps.main() == 0

    planned = {
        f"{json.loads(line)['ats']}:{json.loads(line)['slug']}"
        for k in json.loads((out / "plan.json").read_text())["shards"]
        for line in (out / f"shard-{k}.jsonl").read_text().splitlines()
    }
    assert planned == {"greenhouse:alive", "greenhouse:stale"}
    line = next(
        r.message for r in caplog.records if r.message.startswith("quarantine:")
    )
    assert "1 re-admitted on parole, of 2 quarantined" in line


def test_gate_counts_the_days_of_a_timestamped_cost_row():
    """The cost ledger stamps a full timestamp since ADR-0229; the gate's re-check still counts
    whole days, and the rows written before it (bare dates) keep reading the same."""
    assert ps._days_since("2026-09-10T23:59:59+00:00", "2026-09-24") == 14.0
    assert ps._days_since("2026-09-10", "2026-09-24") == 14.0


def test_main_rotates_the_unscored_tail_oldest_first(tmp_path, monkeypatch):
    """The planner hands the cost ledger's last-look stamps to `pick_boards` (ADR-0229), so a
    Slice smaller than the unscored set takes the Boards read longest ago."""
    from headstart import board_cost

    boards = [ScrapableBoard("lever", f"b{i}", f"B{i}") for i in range(6)]
    monkeypatch.setattr(
        ps.scrapable_boards, "load", lambda ledger, min_jobs=0: list(boards)
    )
    cost = tmp_path / "board_cost.csv"
    board_cost.save(
        cost,
        {f"lever:b{i}": _cost(1.0, f"2026-09-25T0{i}:00:00+00:00") for i in range(6)},
    )
    out = tmp_path / "assignments"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scrape_plan",
            "--priority",
            str(tmp_path / "none.csv"),
            "--cost",
            str(cost),
            "--failures",
            str(tmp_path / "nofailures.csv"),
            "--gap",
            str(tmp_path / "nogap.csv"),
            "--out-dir",
            str(out),
            "--max-boards",
            "2",
            "--max-shards",
            "1",
        ],
    )
    assert ps.main() == 0

    planned = {
        f"{rec['ats']}:{rec['slug']}"
        for rec in map(json.loads, (out / "shard-0.jsonl").read_text().splitlines())
    }
    assert planned == {"lever:b0", "lever:b1"}


def _plan_scored_boards(tmp_path, monkeypatch, n_boards, max_boards):
    """Plan ``n_boards`` Scored Boards (distinct scores) under ``--max-boards max_boards``."""
    from headstart import board_priority

    boards = [ScrapableBoard("lever", f"b{i}", f"B{i}") for i in range(n_boards)]
    monkeypatch.setattr(
        ps.scrapable_boards, "load", lambda ledger, min_jobs=0: list(boards)
    )
    priority = tmp_path / "board_priority.csv"
    board_priority.save(
        priority,
        {
            f"lever:b{i}": board_priority.BoardPriority(100.0 - i, 5, "2026-09-25")
            for i in range(n_boards)
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scrape_plan",
            "--priority",
            str(priority),
            "--cost",
            str(tmp_path / "nocost.csv"),
            "--failures",
            str(tmp_path / "nofailures.csv"),
            "--gap",
            str(tmp_path / "nogap.csv"),
            "--out-dir",
            str(tmp_path / "assignments"),
            "--max-boards",
            str(max_boards),
            "--max-shards",
            "1",
        ],
    )
    assert ps.main() == 0


def test_main_names_scored_boards_the_head_cannot_hold(tmp_path, monkeypatch, caplog):
    """The head holds every Scored Board only while they fit (ADR-0229). Past the cap the
    lowest-scored join the Tail, and the plan says so rather than letting the
    "every tech-yielding Board every run" promise lapse unseen."""
    from headstart.board_priority import head_slots

    with caplog.at_level("WARNING"):
        _plan_scored_boards(tmp_path, monkeypatch, n_boards=20, max_boards=10)

    cap = head_slots(10)
    assert f"head: 20 Scored Boards for {cap} head slots" in caplog.text
    assert f"the lowest-scored {20 - cap} join the Tail" in caplog.text


def test_a_slice_that_takes_every_board_reports_no_head_overflow(
    tmp_path, monkeypatch, caplog
):
    """`pick_boards` returns every Board once the slice is at least as big as the list, so there
    is no Tail for Scored Boards to overflow into and nothing to warn about."""
    with caplog.at_level("WARNING"):
        _plan_scored_boards(tmp_path, monkeypatch, n_boards=10, max_boards=10)

    assert "head:" not in caplog.text
