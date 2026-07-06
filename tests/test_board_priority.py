import random

from headstart.board_priority import BoardPriority, load, pick_boards, save, update
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
    head, tail = picked[:7], picked[7:]
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
