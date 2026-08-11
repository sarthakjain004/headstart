"""Tests for the pure index planners (headstart.ingest.index_plan).

Board-scoped freshness sync (ADR-0014) and the prune sweep (ADR-0023): off-Board eviction,
case-variant dedup, and the board_key mapping the keep-set is built in.
"""

from __future__ import annotations

import pytest

from headstart.ingest.index_plan import apply_sync, plan_prune, plan_sync
from headstart.scrapers.greenhouse import GreenhouseScraper
from headstart.scrapers.personio import PersonioScraper
from headstart.scrapers.workday import WorkdayScraper


def test_plan_add_and_evict_within_scraped_board():
    # board greenhouse:a was scraped; a:2 vanished from the fresh output -> evict; a:3 is new -> add
    plan = plan_sync(
        index_ids=["greenhouse:a:1", "greenhouse:a:2"],
        fresh_ids=["greenhouse:a:1", "greenhouse:a:3"],
        scraped_boards=["greenhouse:a"],
    )
    assert plan.add == frozenset({"greenhouse:a:3"})
    assert plan.delete == frozenset({"greenhouse:a:2"})


def test_plan_never_evicts_an_unscraped_board():
    # lever:b wasn't scraped this run, so its rows stay even though they're absent from fresh_ids
    plan = plan_sync(
        index_ids=["greenhouse:a:1", "lever:b:9"],
        fresh_ids=["greenhouse:a:1"],
        scraped_boards=["greenhouse:a"],
    )
    assert plan.delete == frozenset()  # lever:b:9 protected
    assert plan.add == frozenset()


def test_plan_id_only_leaves_reseen_ids():
    # an id present in both index and fresh is neither re-embedded nor evicted (id-only, v1)
    plan = plan_sync(
        index_ids=["greenhouse:a:1"],
        fresh_ids=["greenhouse:a:1"],
        scraped_boards=["greenhouse:a"],
    )
    assert plan.add == frozenset()
    assert plan.delete == frozenset()


def test_plan_dead_board_evicts_all_its_rows():
    # a scraped board that yields nothing (dead) -> every one of its rows drops out
    plan = plan_sync(
        index_ids=["greenhouse:a:1", "greenhouse:a:2"],
        fresh_ids=[],
        scraped_boards=["greenhouse:a"],
    )
    assert plan.delete == frozenset({"greenhouse:a:1", "greenhouse:a:2"})


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
    fossil, fresh = "workday:co/Site:R1", "workday:co/site:R1"
    index = {fossil}
    for _ in range(3):
        plan = plan_sync(index, {fresh}, {"workday:co/site"})
        index = (index | set(plan.add)) - set(plan.delete)
        off, dup = plan_prune(index, keep)
        index -= set(off) | set(dup)
    assert index == {fresh}  # the fossil is gone and the live row survives


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
