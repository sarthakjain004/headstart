"""Tests for the pure index planners (headstart.ingest.index_plan).

Board-scoped freshness sync (ADR-0014) and the prune sweep (ADR-0023): off-Board eviction,
case-variant dedup, and the board_key mapping the keep-set is built in.
"""

from __future__ import annotations

import pytest

from headstart.config import load_active_companies
from headstart.ingest.index_plan import (
    errored_boards,
    _live_board_end,
    apply_sync,
    boards_by_canon,
    live_keep_set,
    plan_prune,
    plan_sync,
    resolve_board,
)
from headstart.scrapers.greenhouse import GreenhouseScraper
from headstart.scrapers.personio import PersonioScraper
from headstart.scrapers.workday import WorkdayScraper


def test_plan_add_and_evict_within_scraped_board():
    # board greenhouse:a was scraped; a:2 vanished from the fresh output -> evict; a:3 is new -> add
    plan = plan_sync(
        index_ids=["greenhouse:a:1", "greenhouse:a:2"],
        fresh_ids=["greenhouse:a:1", "greenhouse:a:3"],
        scraped_boards=["greenhouse:a"],
        live={},
    )
    assert plan.add == frozenset({"greenhouse:a:3"})
    assert plan.delete == frozenset({"greenhouse:a:2"})


def test_plan_never_evicts_an_unscraped_board():
    # lever:b wasn't scraped this run, so its rows stay even though they're absent from fresh_ids
    plan = plan_sync(
        index_ids=["greenhouse:a:1", "lever:b:9"],
        fresh_ids=["greenhouse:a:1"],
        scraped_boards=["greenhouse:a"],
        live={},
    )
    assert plan.delete == frozenset()  # lever:b:9 protected
    assert plan.add == frozenset()


def test_plan_id_only_leaves_reseen_ids():
    # an id present in both index and fresh is neither re-embedded nor evicted (id-only, v1)
    plan = plan_sync(
        index_ids=["greenhouse:a:1"],
        fresh_ids=["greenhouse:a:1"],
        scraped_boards=["greenhouse:a"],
        live={},
    )
    assert plan.add == frozenset()
    assert plan.delete == frozenset()


def test_plan_dead_board_below_the_floor_evicts_all_its_rows():
    # a scraped board that yields nothing (dead) -> every one of its rows drops out. Only below
    # COLLAPSE_FLOOR: above it the collapse guard holds them
    # (test_collapse_guard_holds_a_board_that_lost_every_tech_job).
    plan = plan_sync(
        index_ids=["greenhouse:a:1", "greenhouse:a:2"],
        fresh_ids=[],
        scraped_boards=["greenhouse:a"],
        live={},
    )
    assert plan.delete == frozenset({"greenhouse:a:1", "greenhouse:a:2"})


def _board(name: str, n: int) -> list[str]:
    return [f"{name}:{i}" for i in range(n)]


def test_collapse_guard_holds_a_board_that_loses_too_much_at_once():
    # the flap: a throttled scrape re-emits 60 of 200 rows, so 140 live postings look delisted
    indexed = _board("eightfold:nvidia.eightfold.ai", 200)
    plan = plan_sync(
        index_ids=indexed,
        fresh_ids=indexed[:60],
        scraped_boards=["eightfold:nvidia.eightfold.ai"],
        live={},
    )
    assert plan.delete == frozenset()
    assert plan.held == (("eightfold:nvidia.eightfold.ai", 140),)


def test_collapse_guard_allows_ordinary_delisting():
    # 10% churn is normal posting turnover and must still evict
    indexed = _board("greenhouse:a", 200)
    plan = plan_sync(
        index_ids=indexed,
        fresh_ids=indexed[:180],
        scraped_boards=["greenhouse:a"],
        live={},
    )
    assert plan.delete == frozenset(indexed[180:])
    assert plan.held == ()


def test_collapse_guard_ignores_small_boards():
    # below the floor a large *ratio* is a handful of rows — genuine churn, not a collapse
    indexed = _board("lever:tiny", 8)
    plan = plan_sync(
        index_ids=indexed,
        fresh_ids=indexed[:2],
        scraped_boards=["lever:tiny"],
        live={},
    )
    assert plan.delete == frozenset(indexed[2:])
    assert plan.held == ()


def test_collapse_guard_is_per_board():
    # one truncated board must not stop a healthy board's evictions in the same run
    good, bad = _board("greenhouse:good", 100), _board("eightfold:bad", 100)
    plan = plan_sync(
        index_ids=good + bad,
        fresh_ids=good[:95] + bad[:10],
        scraped_boards=["greenhouse:good", "eightfold:bad"],
        live={},
    )
    assert plan.delete == frozenset(good[95:])
    assert plan.held == (("eightfold:bad", 90),)


def test_collapse_guard_holds_a_board_that_lost_every_tech_job():
    """The knowing regression against ADR-0014 (see ADR-0046 Consequences).

    A live Board whose jobs are all non-tech now is in scope with *zero* fresh ids — ADR-0014 had
    its stale rows fall out for free. Above the floor that is indistinguishable from a scrape
    truncated to nothing, so the guard holds them instead. Pinned so the trade is deliberate.
    """
    indexed = _board("greenhouse:went-non-tech", 50)
    plan = plan_sync(
        index_ids=indexed,
        fresh_ids=[],
        scraped_boards=["greenhouse:went-non-tech"],
        live={},
    )
    assert plan.delete == frozenset()
    assert plan.held == (("greenhouse:went-non-tech", 50),)


def test_collapse_guard_still_adds_the_rows_that_did_arrive():
    # holding evictions must not hold additions — new postings from a truncated board still land
    indexed = _board("eightfold:bad", 100)
    plan = plan_sync(
        index_ids=indexed,
        fresh_ids=indexed[:20] + ["eightfold:bad:new"],
        scraped_boards=["eightfold:bad"],
        live={},
    )
    assert plan.add == frozenset({"eightfold:bad:new"})
    assert plan.delete == frozenset()


def _ids(table) -> set[str]:
    return set(table.to_arrow().column("id").to_pylist())


def test_apply_sync_round_trip(tmp_path):
    # lancedb/pyarrow are in the [embed] optional group, not installed in CI — skip there
    lancedb = pytest.importorskip("lancedb")
    pa = pytest.importorskip("pyarrow")

    schema = pa.schema(
        [pa.field("id", pa.string()), pa.field("vector", pa.list_(pa.float32(), 2))]
    )
    table = lancedb.connect(str(tmp_path)).create_table(
        "jobs",
        data=[
            {"id": "greenhouse:a:1", "vector": [0.1, 0.2]},
            {"id": "greenhouse:a:2", "vector": [0.3, 0.4]},
        ],
        schema=schema,
    )
    apply_sync(
        table,
        add_rows=[{"id": "greenhouse:a:3", "vector": [0.5, 0.6]}],
        delete_ids=["greenhouse:a:2"],
    )
    assert _ids(table) == {"greenhouse:a:1", "greenhouse:a:3"}


def test_off_board_evicted_survivors_kept():
    keep = {"greenhouse:live"}
    off, dup = plan_prune(["greenhouse:live:1", "greenhouse:dead:2"], keep)
    assert off == ["greenhouse:dead:2"]
    assert dup == []


def test_dedup_keeps_the_casing_the_live_ledger_scrapes():
    # one job under two Board casings; the ledger scrapes 'co/site', so that row is the one a
    # future scrape re-sees — keeping the lex-min 'co/Site' would strand a fossil sync can't evict
    keep = {"workday:co/site"}
    off, dup = plan_prune(["workday:co/Site:R1", "workday:co/site:R1"], keep)
    assert off == []
    assert dup == ["workday:co/Site:R1"]


def test_distinct_native_ids_are_not_duplicates():
    keep = {"workday:co/site"}
    _, dup = plan_prune(["workday:co/Site:R1", "workday:co/site:R2"], keep)
    assert dup == []


def test_three_way_casing_keeps_the_live_one():
    keep = {"workday:co/Site"}
    ids = ["workday:co/SITE:R1", "workday:co/Site:R1", "workday:co/site:R1"]
    off, dup = plan_prune(ids, keep)
    assert off == []
    assert sorted(dup) == ["workday:co/SITE:R1", "workday:co/site:R1"]


def test_dedup_falls_back_to_lexmin_when_no_row_has_the_live_casing():
    # every row is a fossil (the live casing isn't in the index yet) — still collapse to one
    keep = {"workday:co/site"}
    off, dup = plan_prune(["workday:co/SITE:R1", "workday:co/Site:R1"], keep)
    assert off == []
    assert dup == ["workday:co/Site:R1"]  # 'co/SITE' (I<i) sorts first


def test_prune_does_not_churn_the_freshly_scraped_row():
    # the regression that motivated the rule: sync re-adds what prune deleted, every run
    keep = {"workday:co/site"}
    live = boards_by_canon(keep)  # as sync gets it in production, not an empty ledger
    fossil, fresh = "workday:co/Site:R1", "workday:co/site:R1"
    index = {fossil}
    for _ in range(3):
        plan = plan_sync(index, {fresh}, {"workday:co/site"}, live)
        index = (index | set(plan.add)) - set(plan.delete)
        off, dup = plan_prune(index, keep)
        index -= set(off) | set(dup)
    assert index == {fresh}  # the fossil is gone and the live row survives


_CASE_VARIANT_SITES = ("External", "external", "EXTERNAL")


def test_prune_keeps_the_casing_the_scrape_emits(tmp_path):
    """The casing a scrape emits and the casing prune keeps must be the same one.

    They agree structurally rather than by coincidence: ``load_active_companies`` collapses a
    Board's case-variant ledger rows to one entry, and *both* consumers read that same deduped
    list — the scrape to decide what to fetch, ``live_keep_set`` to build the keep-set prune keeps
    rows against. One choice, made once, consumed twice. This pins the join end to end, from ledger
    rows to eviction, because if the keep-set ever carried a casing the scrape does not emit, every
    case-variant Board would loop: sync adds the scraped row, prune deletes it as the other
    casing's duplicate, forever (ADR-0023's amendment). The committed Workday ledger holds 1,943
    case-variant URL groups, so the population this protects is not hypothetical.
    """
    ledger = tmp_path / "liveness"
    ledger.mkdir()
    rows = ["ats,tenant,url,status,jobs,checked_at"] + [
        f"workday,acme,https://acme.wd1.myworkdayjobs.com/{site},live,5,2026-08-13"
        for site in _CASE_VARIANT_SITES
    ]
    (ledger / "workday.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    scraped = load_active_companies(ledger, min_jobs=0)
    assert len(scraped) == 1  # one Board is fetched, not three
    emitted = WorkdayScraper(scraped[0].slug).board_key()

    indexed = [f"workday:acme/{site}:R1" for site in _CASE_VARIANT_SITES]
    off_board, duplicate = plan_prune(indexed, live_keep_set(ledger))
    assert off_board == []  # every casing resolves to the one live Board
    assert set(indexed) - set(duplicate) == {f"{emitted}:R1"}


def test_board_key_default_is_ats_colon_slug():
    assert GreenhouseScraper("stripe").board_key() == "greenhouse:stripe"


def test_board_key_workday_is_company_slash_site():
    s = WorkdayScraper("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite")
    assert s.board_key() == "workday:nvidia/NVIDIAExternalCareerSite"


def test_board_key_personio_is_the_bare_tenant_its_ids_carry():
    # personio's slug is the host, but `parse` builds ids from its first label, so the default
    # `{ats}:{slug}` key would never match `board_of` of its own rows.
    assert (
        PersonioScraper("ailylabs.jobs.personio.com").board_key() == "personio:ailylabs"
    )


def test_prune_keeps_rows_whose_board_is_live():
    # The churn this locks down: a live Board whose keep-set key disagrees with the ids it emits
    # is pruned off-Board every run, and re-added by the next sync — forever.
    keep = {PersonioScraper("ailylabs.jobs.personio.com").board_key()}
    off, dup = plan_prune(["personio:ailylabs:2036107"], keep)
    assert off == []
    assert dup == []


def test_prune_keeps_rows_whose_native_id_contains_a_colon():
    """ADR-0049: real Workday native ids carry colons — `REQ: 228`, a postal address, a whole URL.
    Splitting the composite key on the last colon attributed these to a Board that does not exist,
    so prune called them off-Board and evicted them; sync re-added them next run, forever. The same
    seven rows were pruned in three of five production runs."""
    keep = {"workday:dmainc/DMA", "workday:otis/REC_Ext_Gateway"}
    ids = [
        "workday:dmainc/DMA:REQ: 228",
        "workday:otis/REC_Ext_Gateway:OT221: GD - NEW YORK, NY One Penn Plaza, NY, 10119",
        "workday:campaignmonitor/marigold:https://x.wd5.myworkdayjobs.com/marigold/job/R2454",
    ]
    off_board, duplicate = plan_prune(ids, keep)
    assert off_board == [
        ids[2]
    ]  # not in this test's keep-set, so correctly off-Board here
    assert duplicate == []


def test_the_longest_nesting_board_owns_the_row():
    """Defence in depth, asserted on the helper because no live Board key nests at a colon today.

    Workday's ``co/site`` tenants nest at a *slash*, which is never a candidate position, so
    ``plan_prune``'s output cannot currently tell first-match from longest-match — only the split
    point can. If a Board key ever gains a colon, first-match would hand the longer Board's rows a
    native id carrying the rest of the Board key.
    """
    live = {"ats:a": "ats:a", "ats:a:b": "ats:a:b"}
    assert _live_board_end("ats:a:b:R1", live) == 7  # "ats:a:b", not "ats:a" at 5
    assert _live_board_end("ats:a:zz", live) == 5
    assert _live_board_end("other:x:1", live) is None


def test_prune_slices_the_native_id_from_the_original_casing():
    # `live` is lowercased and str.lower() is not length-preserving, so slicing by the lowercased
    # key's *length* would eat a character of the native id and collide two distinct Jobs
    keep = {"workday:\u0130nc/Site"}
    off_board, duplicate = plan_prune(
        ["workday:\u0130nc/Site:R1", "workday:\u0130nc/Site:X1"], keep
    )
    assert off_board == []
    assert duplicate == []  # R1 and X1 stay distinct


def test_prune_still_dedupes_case_variants_with_a_colon_in_the_native_id():
    keep = {"workday:co/site"}
    ids = ["workday:co/Site:REQ: 9", "workday:co/site:REQ: 9"]
    off_board, duplicate = plan_prune(ids, keep)
    assert off_board == []
    assert duplicate == ["workday:co/Site:REQ: 9"]  # the fossil casing goes


def test_sync_can_evict_a_closed_posting_whose_native_id_has_a_colon():
    """The half-fix trap (ADR-0049). Teaching prune to match by prefix stops it evicting these
    rows — correct while they are live, but it also removed the only reach anything had on them
    once closed, because sync's scope held a *phantom* Board (`…:OT221`) that is unique per
    requisition and so is never recreated by a fresh sibling. Resolving both sides against the
    live ledger puts the closed row and its live siblings on the same real Board, so sync sees it.
    """
    live = boards_by_canon({"workday:otis/REC_Ext_Gateway"})
    closed = "workday:otis/REC_Ext_Gateway:OT221: GD - NEW YORK, NY One Penn Plaza"
    fresh = "workday:otis/REC_Ext_Gateway:OT999: SOMEWHERE ELSE"

    scope = {resolve_board(fresh, live)}
    assert scope == {
        "workday:otis/REC_Ext_Gateway"
    }  # a real Board, not a per-req phantom
    plan = plan_sync([closed], [fresh], scope, live)
    assert plan.delete == frozenset({closed})

    # ...and prune still leaves it alone while it is live, which is the loop this ADR closes
    assert plan_prune([closed], {"workday:otis/REC_Ext_Gateway"}) == ([], [])


def test_sync_without_a_ledger_keeps_the_board_of_scoping():
    # empty keep-set -> resolve_board falls back to board_of, the pre-ADR-0049 rule
    plan = plan_sync(
        ["greenhouse:a:1", "greenhouse:a:2"], ["greenhouse:a:1"], {"greenhouse:a"}, {}
    )
    assert plan.delete == frozenset({"greenhouse:a:2"})


def test_errored_boards_reads_the_file_lowercased(tmp_path):
    p = tmp_path / "scrape_errors.json"
    p.write_text('{"eightfold:NVIDIA.eightfold.ai": "HTTP 429"}', encoding="utf-8")
    # Lowercased because `resolve_board` returns a Board in the *id's* casing while the file is
    # written from `board_key()`, which carries the ledger's — the two need not agree.
    assert errored_boards(p) == {"eightfold:nvidia.eightfold.ai"}


def test_errored_boards_is_empty_when_the_file_is_absent(tmp_path):
    """A local sync, or a run predating the file: fall back to the old infer-from-lines scope
    rather than failing, since unreadable telemetry must not stop the index updating."""
    assert errored_boards(tmp_path / "nope.json") == set()


@pytest.mark.parametrize("body", ["[1, 2]", '"abc"', "null", "42"])
def test_errored_boards_rejects_a_json_shape_that_is_not_an_object(tmp_path, body):
    """JSON's top level may legally be a list or a string, and iterating either yields items that
    are not Board keys: `"abc"` would quietly protect Boards a, b and c, and `[1, 2]` would raise
    on `.lower()` and take sync down with it."""
    p = tmp_path / "scrape_errors.json"
    p.write_text(body, encoding="utf-8")
    assert errored_boards(p) == set()


def test_errored_boards_survives_a_corrupt_file(tmp_path):
    p = tmp_path / "scrape_errors.json"
    p.write_text("{not json", encoding="utf-8")
    assert errored_boards(p) == set()
