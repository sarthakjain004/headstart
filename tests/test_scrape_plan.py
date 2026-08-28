"""Tests for the scrape planner (headstart.ingest.scrape_plan, ADR-0026).

The per-Board cost weighting (detail-fetch ATSes cost more) and the partition invariant — every
selected Board lands in exactly one shard — are the logic worth locking down. ``main`` is run with a
monkeypatched active-list so the test doesn't couple to the liveness-ledger CSV format.
"""

from __future__ import annotations

import json
import sys

import headstart.ingest.scrape_plan as ps
from headstart.config import CompanyRef, board_identity


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
        CompanyRef("workday", "big", "Big"),
        CompanyRef("lever", "acme", "Acme"),
        CompanyRef("greenhouse", "co", "Co"),
        CompanyRef("keka", "startup", "Startup"),
        CompanyRef("lever", "other", "Other"),
    ]
    monkeypatch.setattr(
        ps, "load_active_companies", lambda ledger, min_jobs=0: list(boards)
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
    monkeypatch.setattr(ps, "load_active_companies", lambda ledger, min_jobs=0: [])
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
        ps,
        "load_active_companies",
        lambda ledger, min_jobs=0: [CompanyRef("lever", "a", "A")],
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


def _cost(seconds: float, day: str = "2026-08-18"):
    from headstart.board_cost import BoardCost

    return BoardCost(seconds=seconds, jobs=0, updated_at=day)


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
    walmart = CompanyRef(
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
    wd3 = CompanyRef(
        ats="workday", slug="https://accenture.wd3.myworkdayjobs.com/careers", name="A"
    )
    wd103 = CompanyRef(
        ats="workday",
        slug="https://accenture.wd103.myworkdayjobs.com/careers",
        name="A",
    )
    assert board_identity(wd3) == board_identity(wd103) == "workday:accenture/careers"
