import random

from headstart.board_priority import (
    EXPLORE_FRAC,
    BoardPriority,
    load,
    pick_boards,
    save,
    update,
)
from headstart.config import CompanyRef

TODAY = "2026-07-06"


def _prio(score, jobs=0, when="2026-07-01"):
    return BoardPriority(score=score, last_tech_jobs=jobs, updated_at=when)


def test_update_blends_current_and_past():
    prev = {"lever:acme": _prio(10.0)}
    rows = update(prev, {"lever:acme": 20}, {"lever:acme"}, today=TODAY)
    assert rows["lever:acme"].score == 0.7 * 20 + 0.3 * 10.0
    assert rows["lever:acme"].last_tech_jobs == 20
    assert rows["lever:acme"].updated_at == TODAY


def test_update_absent_board_carries_row_unchanged():
    prev = {"lever:acme": _prio(10.0, jobs=12)}
    rows = update(prev, {}, {"greenhouse:other"}, today=TODAY)
    assert rows["lever:acme"] == prev["lever:acme"]  # score AND updated_at untouched


def test_update_new_board_seeds_from_zero():
    rows = update({}, {"ashby:fresh": 5}, {"ashby:fresh"}, today=TODAY)
    assert rows["ashby:fresh"].score == 0.7 * 5


def test_update_decays_and_prunes():
    prev = {"lever:fading": _prio(0.1)}
    # present with zero tech jobs: 0.7*0 + 0.3*0.1 = 0.03 < 0.05 -> pruned
    rows = update(prev, {}, {"lever:fading"}, today=TODAY)
    assert "lever:fading" not in rows


def test_save_load_round_trip_with_colon_slug(tmp_path):
    ledger = tmp_path / "state" / "board_priority.csv"
    rows = {
        "workday:acme:site:x": _prio(12.25, jobs=17),
        "lever:acme": _prio(3.5, jobs=5),
    }
    save(ledger, rows)
    back = load(ledger)
    assert back["workday:acme:site:x"].last_tech_jobs == 17
    assert back["workday:acme:site:x"].score == 12.25
    assert back["lever:acme"].score == 3.5


def test_load_missing_file_is_empty(tmp_path):
    assert load(tmp_path / "nope.csv") == {}


def _companies(n):
    return [CompanyRef(ats="lever", slug=f"c{i}") for i in range(n)]


def test_pick_boards_split_and_order():
    companies = _companies(100)
    # boards c0..c19 are scored, c0 highest
    scores = {f"lever:c{i}": float(100 - i) for i in range(20)}
    picked = pick_boards(companies, scores, 10, rng=random.Random(7))
    assert len(picked) == 10
    # head size follows EXPLORE_FRAC, so retuning the split doesn't invalidate this test
    head_n = 10 - round(10 * EXPLORE_FRAC)
    head, tail = picked[:head_n], picked[head_n:]
    head_scores = [scores[f"lever:{c.slug}"] for c in head]
    assert head_scores == sorted(head_scores, reverse=True)  # score-desc head
    assert head_scores[0] == 100.0  # top board always first
    assert {c.slug for c in head}.isdisjoint({c.slug for c in tail})


def test_pick_boards_no_scores_is_shuffle_and_cap():
    companies = _companies(30)
    picked = pick_boards(companies, {}, 10, rng=random.Random(1))
    assert len(picked) == 10
    assert len({c.slug for c in picked}) == 10


def test_pick_boards_uncapped_returns_all_priority_first():
    companies = _companies(10)
    scores = {"lever:c7": 50.0}
    picked = pick_boards(companies, scores, 0, rng=random.Random(2))
    assert len(picked) == 10
    assert picked[0].slug == "c7"


def test_pick_boards_short_known_list_still_fills_cap():
    companies = _companies(50)
    scores = {"lever:c0": 5.0}  # only one scored board; head would get 7 slots
    picked = pick_boards(companies, scores, 10, rng=random.Random(3))
    assert len(picked) == 10
    assert picked[0].slug == "c0"
    assert len({c.slug for c in picked}) == 10  # no duplicates


def test_pick_boards_scores_workday_and_personio_by_their_board_key():
    """The ledger is written from `corpus.board_of(job_id)`, which is the **board_key** shape —
    and Workday and Personio override `board_key()` (a Workday slug is a whole careers URL, a
    Personio slug the whole host). Keying the lookup `f"{ats}:{slug}"` therefore never matched
    their rows. 13,402 boards (20.1% of the scrape list) are keyed that way; against a local
    ledger snapshot 4,611 of them had actually earned a score and were reading 0.0, reachable
    only through the random exploration tail."""
    from headstart.config import CompanyRef

    workday = CompanyRef(
        ats="workday", slug="https://x.wd1.myworkdayjobs.com/Careers", name="X"
    )
    personio = CompanyRef(ats="personio", slug="acme.jobs.personio.de", name="Acme")
    plain = CompanyRef(ats="greenhouse", slug="stripe", name="Stripe")
    # keyed exactly as `update_ledgers priority` writes them
    scores = {
        "workday:x/Careers": 90.0,
        "personio:acme": 80.0,
        "greenhouse:stripe": 70.0,
    }

    picked = pick_boards([plain, workday, personio], scores, max_boards=3)

    assert [c.ats for c in picked] == ["workday", "personio", "greenhouse"], (
        "all three are scored, so all three sort by score — a board the lookup misses "
        "would fall to the unscored exploration tail instead"
    )


# ── the ADR-0062 description-gap quota ────────────────────────────────────────────────────


def test_gap_quota_reserves_slots_from_the_exploration_tail():
    companies = _companies(100)
    scores = {f"lever:c{i}": float(100 - i) for i in range(20)}
    # every unscored board is gap-ful, so the quota is what limits how many are picked
    unsettled = {f"lever:c{i}": 10 for i in range(20, 100)}

    picked = pick_boards(
        companies, scores, 40, unsettled=unsettled, gap_frac=0.5, rng=random.Random(7)
    )

    head_n = 40 - round(40 * EXPLORE_FRAC)
    explore_slots = 40 - head_n
    assert len(picked) == 40
    gap = [c for c in picked[head_n:] if f"lever:{c.slug}" in unsettled]
    assert len(gap) >= round(explore_slots * 0.5)
    assert len({c.slug for c in picked}) == 40  # the quota never double-picks


def test_gap_quota_leaves_the_priority_head_untouched():
    companies = _companies(60)
    scores = {f"lever:c{i}": float(100 - i) for i in range(10)}
    unsettled = {f"lever:c{i}": 99 for i in range(10, 60)}

    with_gap = pick_boards(
        companies, scores, 20, unsettled=unsettled, rng=random.Random(11)
    )
    without = pick_boards(companies, scores, 20, rng=random.Random(11))

    head_n = 20 - round(20 * EXPLORE_FRAC)
    assert [c.slug for c in with_gap[:head_n]] == [c.slug for c in without[:head_n]], (
        "the quota comes out of exploration; a scored board must never lose its slot to it"
    )


def test_gap_quota_drains_listing_only_atses_before_detail_pass():
    """Cheapest class first: a listing-only Board settles every Job on it in one request, while a
    detail-pass Board needs a fetch per Job. Draining the cheap half first buys most of the
    backlog for a fraction of the cost."""
    detail = [CompanyRef(ats="workday", slug=f"d{i}") for i in range(10)]
    listing = [CompanyRef(ats="greenhouse", slug=f"l{i}") for i in range(10)]
    # the detail boards hold far more unsettled Jobs, so only the class ordering can put the
    # listing boards first
    unsettled = {f"workday:d{i}": 500 for i in range(10)}
    unsettled |= {f"greenhouse:l{i}": 1 for i in range(10)}

    # 20 candidates for 10 slots, so the quota genuinely has to choose
    picked = pick_boards(
        detail + listing,
        {},
        10,
        unsettled=unsettled,
        gap_frac=1.0,
        rng=random.Random(5),
    )

    assert len(picked) == 10
    assert [c.ats for c in picked] == ["greenhouse"] * 10, (
        "every listing-only board must drain before any detail-pass board, even though the "
        "detail boards each hold 500x the unsettled Jobs"
    )


def test_gap_quota_orders_by_unsettled_count_within_a_class():
    companies = [CompanyRef(ats="greenhouse", slug=f"c{i}") for i in range(20)]
    unsettled = {f"greenhouse:c{i}": i for i in range(20)}

    picked = pick_boards(
        companies, {}, 10, unsettled=unsettled, gap_frac=1.0, rng=random.Random(3)
    )

    counts = [unsettled[f"greenhouse:{c.slug}"] for c in picked]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] == 19


def test_gap_quota_matches_a_board_whose_ledger_casing_differs():
    """The ledger is written lowercase because a Job id's casing and the liveness ledger's need
    not agree (ADR-0049/ADR-0023). Measured against a real store, 1,693 of 13,708 gap Boards —
    45,375 Jobs, 23% of the backlog — matched the live slice only case-insensitively, so an
    as-observed lookup strands every one of them and the quota silently under-fills."""
    board = CompanyRef(ats="workday", slug="ngc/Northrop_Grumman_External_Site")
    picked = pick_boards(
        [board],
        {},
        1,
        unsettled={"workday:ngc/northrop_grumman_external_site": 1740},
        gap_frac=1.0,
        rng=random.Random(1),
    )
    assert [c.slug for c in picked] == ["ngc/Northrop_Grumman_External_Site"]


def test_absent_gap_ledger_leaves_the_slice_byte_identical():
    """The self-cancelling property: once the backlog drains the ledger empties, and the slice
    must return to exactly what it was before this existed."""
    companies = _companies(80)
    scores = {f"lever:c{i}": float(80 - i) for i in range(30)}

    before = pick_boards(companies, scores, 25, rng=random.Random(42))
    empty = pick_boards(companies, scores, 25, unsettled={}, rng=random.Random(42))

    assert [c.slug for c in before] == [c.slug for c in empty]
