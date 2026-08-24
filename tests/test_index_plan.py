"""Tests for the pure index planners (headstart.ingest.index_plan).

Board-scoped freshness sync (ADR-0014) and the prune sweep (ADR-0023): off-Board eviction,
case-variant dedup, and the board_key mapping the keep-set is built in.
"""

from __future__ import annotations

import pytest

from headstart.config import load_active_companies
from headstart.ingest.index_plan import (
    COLLAPSE_FLOOR,
    _live_board_end,
    apply_sync,
    boards_by_canon,
    grace_period_counts,
    live_keep_set,
    plan_prune,
    plan_sync,
    read_unauthoritative_boards,
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


def test_collapse_guard_caps_what_a_board_can_lose_in_one_run():
    # the flap: a throttled scrape re-emits 60 of 200 rows, so 140 live postings look delisted.
    # The Board may shed at most COLLAPSE_RATIO of its rows (50), and the other 90 are withheld —
    # bounded, not refused outright (ADR-0055).
    indexed = _board("eightfold:nvidia.eightfold.ai", 200)
    plan = plan_sync(
        index_ids=indexed,
        fresh_ids=indexed[:60],
        scraped_boards=["eightfold:nvidia.eightfold.ai"],
        live={},
    )
    assert len(plan.delete) == 50
    assert plan.held == (("eightfold:nvidia.eightfold.ai", 90),)
    # never a re-emitted row: the cap chooses among the missing only
    assert plan.delete.isdisjoint(set(indexed[:60]))


def test_a_persistently_truncated_board_drains_instead_of_ratcheting():
    """The ADR-0055 bug: held rows were unreachable by every other path, so they accrued forever.

    `prune` only evicts ids whose Board is absent from the ledger and a held Board is still Live,
    so nothing else could ever take them. Production went 267 -> 953 withheld in 22 hours with
    three Boards withholding an identical count every run. Re-running the same truncated scrape
    must now converge, not plateau.
    """
    board = "ashby:clera"
    indexed = _board(board, 200)
    fresh = indexed[:60]  # the scrape can only ever reach these 60
    live_rows, withheld_each_run = list(indexed), []
    for _ in range(12):
        plan = plan_sync(
            index_ids=live_rows,
            fresh_ids=fresh,
            scraped_boards=[board],
            live={},
        )
        withheld_each_run.append(dict(plan.held).get(board, 0))
        live_rows = [i for i in live_rows if i not in plan.delete]
    assert withheld_each_run[0] > withheld_each_run[-1], "the hold never drained"
    assert withheld_each_run[-1] == 0
    # and it converges on exactly the rows the scrape can still see — nothing more
    assert sorted(live_rows) == sorted(fresh)


def test_the_drain_takes_the_oldest_rows_first():
    # a row unconfirmed for longest is the likeliest genuinely closed; a missing stamp sorts
    # first because those rows predate the first_seen column
    board = "zoho:dataeconomy.zohorecruit.in"
    indexed = _board(board, 20)
    missing = indexed[5:]  # 15 of 20 missing -> cap is int(0.25*20) = 5
    stamps = {job_id: f"2026-08-{i + 10:02d}" for i, job_id in enumerate(missing)}
    del stamps[missing[7]]  # one row predates the column entirely
    plan = plan_sync(
        index_ids=indexed,
        fresh_ids=indexed[:5],
        scraped_boards=[board],
        live={},
        first_seen=stamps,
    )
    assert len(plan.delete) == 5
    assert missing[7] in plan.delete  # the unstamped row goes first
    # the rest are the four oldest stamps
    oldest = sorted((s, i) for i, s in stamps.items())[:4]
    assert {i for _, i in oldest} < plan.delete


def test_the_drain_is_deterministic_without_stamps():
    board = "greenhouse:x"
    indexed = _board(board, 40)
    kwargs = {
        "index_ids": indexed,
        "fresh_ids": indexed[:5],
        "scraped_boards": [board],
        "live": {},
    }
    assert plan_sync(**kwargs).delete == plan_sync(**kwargs).delete


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
    # the healthy Board evicts in full; the truncated one is capped at a quarter of its rows
    # (25 of the 90 missing), so 65 stay withheld — the cap is per Board, like the guard
    assert set(good[95:]) < plan.delete
    assert len(plan.delete) == 5 + 25
    assert plan.held == (("eightfold:bad", 65),)


def test_collapse_guard_holds_a_board_that_lost_every_tech_job():
    """The knowing regression against ADR-0014 (see ADR-0046 Consequences).

    A live Board whose jobs are all non-tech now is in scope with *zero* fresh ids — ADR-0014 had
    its stale rows fall out for free. Above the floor that is indistinguishable from a scrape
    truncated to nothing, so the guard holds them instead. Pinned so the trade is deliberate.

    ADR-0055 bounds the hold rather than removing it: such a Board sheds a quarter of its rows
    per run (12 of 50 here) and so returns to ADR-0014's outcome over several runs instead of
    one, without a single truncated scrape ever collapsing it.
    """
    indexed = _board("greenhouse:went-non-tech", 50)
    plan = plan_sync(
        index_ids=indexed,
        fresh_ids=[],
        scraped_boards=["greenhouse:went-non-tech"],
        live={},
    )
    assert len(plan.delete) == 12
    assert plan.held == (("greenhouse:went-non-tech", 38),)


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
    # the eviction cap never touches adds: 80 rows look missing, 25 drain, the rest are withheld
    assert len(plan.delete) == 25
    assert plan.delete.isdisjoint(plan.add)


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


def test_read_unauthoritative_boards_reads_the_file_lowercased(tmp_path):
    p = tmp_path / "unauthoritative_boards.json"
    p.write_text('{"eightfold:NVIDIA.eightfold.ai": "HTTP 429"}', encoding="utf-8")
    # Lowercased because `resolve_board` returns a Board in the *id's* casing while the file is
    # written from `board_key()`, which carries the ledger's — the two need not agree.
    assert read_unauthoritative_boards(p) == {
        "eightfold:nvidia.eightfold.ai": "HTTP 429"
    }


def test_read_unauthoritative_boards_is_empty_when_the_file_is_absent(tmp_path):
    """A local sync, or a run predating the file: fall back to the old infer-from-lines scope
    rather than failing, since unreadable telemetry must not stop the index updating."""
    assert read_unauthoritative_boards(tmp_path / "nope.json") == {}


@pytest.mark.parametrize("body", ["[1, 2]", '"abc"', "null", "42"])
def test_read_unauthoritative_boards_rejects_a_json_shape_that_is_not_an_object(
    tmp_path, body
):
    """JSON's top level may legally be a list or a string, and iterating either yields items that
    are not Board keys: `"abc"` would quietly protect Boards a, b and c, and `[1, 2]` would raise
    on `.lower()` and take sync down with it."""
    p = tmp_path / "unauthoritative_boards.json"
    p.write_text(body, encoding="utf-8")
    assert read_unauthoritative_boards(p) == {}


def test_read_unauthoritative_boards_survives_a_corrupt_file(tmp_path):
    p = tmp_path / "unauthoritative_boards.json"
    p.write_text("{not json", encoding="utf-8")
    assert read_unauthoritative_boards(p) == {}


def test_read_unauthoritative_boards_keeps_the_reason(tmp_path):
    """The reason is the diagnostic payload: a Board excluded every run reads identically whether
    it was rate-limited or served a short page, and only the reason separates the two."""
    p = tmp_path / "unauthoritative_boards.json"
    p.write_text(
        '{"eightfold:caci.eightfold.ai": "truncated: 500 of 1200 rows"}',
        encoding="utf-8",
    )
    assert read_unauthoritative_boards(p) == {
        "eightfold:caci.eightfold.ai": "truncated: 500 of 1200 rows"
    }


def test_the_drain_always_takes_at_least_one_row():
    """The cap must never round to zero, or the hold is a ratchet again (ADR-0055).

    Safe today only because COLLAPSE_FLOOR (20) keeps `int(0.25 * len(ids))` at 5 or more, and
    those two constants are declared far apart with nothing coupling them. Pinned at the smallest
    Board the guard can reach, and against a hypothetical tiny one.
    """
    board = "greenhouse:at-the-floor"
    indexed = _board(board, COLLAPSE_FLOOR)
    plan = plan_sync(
        index_ids=indexed,
        fresh_ids=[],  # everything missing: the worst case for the guard
        scraped_boards=[board],
        live={},
    )
    assert len(plan.delete) >= 1
    assert dict(plan.held)[board] == COLLAPSE_FLOOR - len(plan.delete)


# --- the ADR-0083 eviction grace period ------------------------------------------------------
# `was_unconfirmed` is the `unconfirmed` set the previous run returned. An absence only becomes
# an eviction on the *second consecutive scrape of that Board* that misses it.


def test_a_first_absence_is_withheld_and_recorded():
    """The Greenhouse case: a silently short response drops live postings from one scrape."""
    indexed = _board("greenhouse:databricks", 30)
    plan = plan_sync(
        index_ids=indexed,
        fresh_ids=indexed[:28],  # two postings absent, still live on the real board
        scraped_boards=["greenhouse:databricks"],
        live={},
        was_unconfirmed=set(),  # grace period on, nothing owed a second look yet
    )
    assert plan.delete == frozenset(), "a single absence must not evict"
    assert plan.unconfirmed == frozenset(indexed[28:])


def test_a_second_consecutive_absence_evicts():
    indexed = _board("greenhouse:databricks", 30)
    absent = set(indexed[28:])
    plan = plan_sync(
        index_ids=indexed,
        fresh_ids=indexed[:28],
        scraped_boards=["greenhouse:databricks"],
        live={},
        was_unconfirmed=absent,  # already missed at the previous scrape of this Board
    )
    assert plan.delete == frozenset(absent)
    assert plan.unconfirmed == frozenset(), "nothing left owing a second look"


def test_reappearing_clears_the_streak():
    """The measured pattern: absent, re-added, absent again — never twice in a row, so it must
    never be evicted. `successfactors:careers.hcltech.com:1364226855` did exactly this and was
    verified still live."""
    indexed = _board("successfactors:careers.hcltech.com", 30)
    plan = plan_sync(
        index_ids=indexed,
        fresh_ids=indexed,  # everything present this scrape
        scraped_boards=["successfactors:careers.hcltech.com"],
        live={},
        was_unconfirmed=set(indexed[:3]),  # these were absent last scrape
    )
    assert plan.delete == frozenset()
    assert plan.unconfirmed == frozenset(), "present again — the streak resets"


def test_an_unscraped_board_is_no_evidence_and_carries_its_ids_forward():
    """Only ~20k of ~66k Boards are in a run's slice, and an Unauthoritative Board is kept out of
    `scraped_boards` too (ADR-0053). Neither is evidence: the ids must keep their state, or a
    Board sitting out a run would silently reset and never reach a second absence."""
    indexed = _board("greenhouse:vast", 10)
    owed = set(indexed[:4])
    plan = plan_sync(
        index_ids=indexed,
        fresh_ids=[],
        scraped_boards=[],  # this Board was not scraped this run
        live={"greenhouse:vast": "greenhouse:vast"},
        was_unconfirmed=owed,
    )
    assert plan.delete == frozenset(), "nothing was looked at, so nothing is delisted"
    assert plan.unconfirmed == frozenset(owed), "state carried forward unchanged"


def test_ids_on_a_board_that_left_the_ledger_are_not_carried_forever():
    """The bound on the file. A Board that leaves the ledger is never scraped again, so its
    entries would accrete for good — the ADR-0055 ratchet in a new place. Those rows leave the
    index via `plan_prune`'s off-Board sweep; their entries leave here with them."""
    indexed = _board("greenhouse:gone", 10)
    plan = plan_sync(
        index_ids=indexed,
        fresh_ids=[],
        scraped_boards=[],
        live={},  # the Board is no longer live
        was_unconfirmed=set(indexed[:4]),
    )
    assert plan.unconfirmed == frozenset(), "dropped, not carried"


def test_the_collapse_guard_measures_the_eligible_set_not_the_raw_absences():
    """The two guards compose rather than duplicate. On the first run of a mass truncation the
    grace period holds everything, so the ratio has nothing to cap and never fires."""
    indexed = _board("eightfold:nvidia.eightfold.ai", 200)
    first = plan_sync(
        index_ids=indexed,
        fresh_ids=indexed[:60],  # 140 absent — far over COLLAPSE_RATIO
        scraped_boards=["eightfold:nvidia.eightfold.ai"],
        live={},
        was_unconfirmed=set(),
    )
    assert first.delete == frozenset(), "grace period holds them all"
    assert first.held == (), "so the collapse guard has nothing to withhold"
    assert len(first.unconfirmed) == 140

    # Still short on the next scrape: now they are eligible, and the ratio caps the drain.
    second = plan_sync(
        index_ids=indexed,
        fresh_ids=indexed[:60],
        scraped_boards=["eightfold:nvidia.eightfold.ai"],
        live={},
        was_unconfirmed=first.unconfirmed,
    )
    assert len(second.delete) == 50
    assert second.held == (("eightfold:nvidia.eightfold.ai", 90),)


def test_none_disables_the_grace_period_entirely():
    """The escape hatch, and the pre-ADR-0083 behaviour. Distinct from an empty set, which means
    the grace period is on and nothing is owed a second look."""
    indexed = _board("greenhouse:databricks", 30)
    plan = plan_sync(
        index_ids=indexed,
        fresh_ids=indexed[:28],
        scraped_boards=["greenhouse:databricks"],
        live={},
        was_unconfirmed=None,
    )
    assert plan.delete == frozenset(indexed[28:]), "evicts on a single absence"
    assert plan.unconfirmed == frozenset()


def test_the_grace_period_is_off_by_default():
    """Every existing caller and test omits the parameter, so the default must be the old
    behaviour — the new one is opted into by `index sync` passing the persisted set."""
    indexed = _board("greenhouse:databricks", 30)
    plan = plan_sync(
        index_ids=indexed,
        fresh_ids=indexed[:28],
        scraped_boards=["greenhouse:databricks"],
        live={},
    )
    assert plan.delete == frozenset(indexed[28:])


def test_a_collapse_held_id_stays_unconfirmed_and_keeps_draining():
    """Regression: a held id must remain eligible, not fall back to a first absence.

    The collapse guard withholds ids that are already *past* the grace period. If they were left
    out of `unconfirmed`, each would read as a fresh first absence next run, the streak would
    reset, and the Board would evict only every other run — halving ADR-0055's drain, the exact
    ratchet it exists to prevent. Measured before the fix: 0, 50, 0, 37, 0, 28.
    """
    board = "eightfold:nvidia.eightfold.ai"
    indexed = _board(board, 200)
    fresh = indexed[:60]  # 140 absent every scrape — a persistently truncated Board

    owed: frozenset[str] = frozenset()
    drained = []
    for _ in range(4):
        plan = plan_sync(
            index_ids=indexed,
            fresh_ids=fresh,
            scraped_boards=[board],
            live={},
            was_unconfirmed=owed,
        )
        drained.append(len(plan.delete))
        # the guard's own withheld ids must still be owed a look, or the next run resets them
        assert plan.unconfirmed >= frozenset(plan.held and indexed[60:]) - plan.delete
        owed = plan.unconfirmed
        indexed = [i for i in indexed if i not in plan.delete]

    # run 1 withholds (first absence); every run after it drains — never an alternating 0
    assert drained[0] == 0, "first absence is always withheld"
    assert all(n > 0 for n in drained[1:]), (
        f"drain stalled on alternate runs: {drained}"
    )


def test_grace_period_counts_measure_reappearance_against_the_scrape():
    """`reappeared` must mean "in the scrape we just took", not "not in the other two buckets".

    ADR-0083: "An id that reappeared, was pruned, or sat on a board that left the ledger is simply
    not written again." Only the first is a reappearance, so a remainder-by-subtraction lumps all
    three together and reports churn that never happened. This calls the real helper `sync` uses,
    so restating the arithmetic here cannot make a wrong implementation pass.
    """
    board = "ats:B"
    indexed = ["ats:B:kept", "ats:B:first_miss", "ats:B:second_miss"]
    was_unconfirmed = frozenset({"ats:B:second_miss"})  # already missed once
    fresh = {"ats:B:kept"}

    plan = plan_sync(
        index_ids=indexed,
        fresh_ids=fresh,
        scraped_boards=[board],
        live={},
        was_unconfirmed=was_unconfirmed,
    )
    # a second consecutive absence evicts; a first one is only unconfirmed
    assert plan.delete == frozenset({"ats:B:second_miss"})
    assert plan.unconfirmed == frozenset({"ats:B:first_miss"})
    assert grace_period_counts(was_unconfirmed, fresh, plan) == (0, 0)

    # ...and when the carried-in id comes back, it counts as a reappearance
    fresh_back = {"ats:B:kept", "ats:B:second_miss"}
    back = plan_sync(
        index_ids=indexed,
        fresh_ids=fresh_back,
        scraped_boards=[board],
        live={},
        was_unconfirmed=was_unconfirmed,
    )
    assert "ats:B:second_miss" not in back.delete
    assert grace_period_counts(was_unconfirmed, fresh_back, back) == (1, 0)


def test_grace_period_counts_exclude_an_id_whose_board_left_the_ledger():
    """Regression: an off-ledger id is not a reappearance.

    `plan_sync`'s carry-forward guard drops it from `unconfirmed` (`board.lower() in live`) and it
    is not evicted either, because its Board went unscraped — so subtracting the other buckets
    would count it as "came back" when the row is really waiting for `plan_prune`'s off-Board
    sweep. Sibling of `test_ids_on_a_board_that_left_the_ledger_are_not_carried_forever`, which
    pins the drop this counter must not misread.
    """
    was_unconfirmed = frozenset({"ats:DEAD:x1"})
    fresh = {"ats:LIVE:y1"}
    plan = plan_sync(
        index_ids=["ats:DEAD:x1", "ats:LIVE:y1"],
        fresh_ids=fresh,
        scraped_boards=["ats:LIVE"],  # ats:DEAD was not scraped
        live={"ats:live": "ats:LIVE"},  # ...and is no longer in the ledger
        was_unconfirmed=was_unconfirmed,
    )
    assert "ats:DEAD:x1" not in plan.unconfirmed and "ats:DEAD:x1" not in plan.delete
    # the buggy definition this test exists to rule out would report 1 reappearance
    assert len(was_unconfirmed - plan.unconfirmed - plan.delete) == 1
    assert grace_period_counts(was_unconfirmed, fresh, plan) == (0, 0)


def test_grace_period_still_waiting_counts_a_collapse_guard_cap_too():
    """`still_waiting` has TWO causes, and the log line must not claim only one.

    An earlier wording said these ids' "Board was not scraped this run". That is wrong for the
    second cause: a Board that *was* scraped, whose id was absent again, but whose evictions the
    ADR-0046 collapse guard capped before reaching it. Both land in
    `was_unconfirmed & plan.unconfirmed`, and this is the one worth watching — it is the shape
    ADR-0055 had to unwind for the guard's own `held`.
    """
    board = "ats:BIG"
    indexed = _board(board, 200)
    was_unconfirmed = frozenset(indexed[60:])  # 140 carried in, all absent again
    fresh = set(indexed[:60])

    plan = plan_sync(
        index_ids=indexed,
        fresh_ids=fresh,
        scraped_boards=[board],  # the Board WAS scraped
        live={},
        was_unconfirmed=was_unconfirmed,
    )
    assert plan.held, "the guard must cap this Board for the test to mean anything"
    reappeared, still_waiting = grace_period_counts(was_unconfirmed, fresh, plan)
    assert reappeared == 0
    # every one of them is on a scraped Board — so "not scraped this run" would be a false claim
    assert still_waiting == len(was_unconfirmed & plan.unconfirmed) == 90


def test_grace_period_still_waiting_counts_an_unscraped_board():
    """The other cause of `still_waiting`: carried-in ids whose Board this run did not read."""
    was_unconfirmed = frozenset({"ats:SKIPPED:x1"})
    plan = plan_sync(
        index_ids=["ats:SKIPPED:x1", "ats:LIVE:y1"],
        fresh_ids={"ats:LIVE:y1"},
        scraped_boards=["ats:LIVE"],  # ats:SKIPPED sat out this run's slice...
        live={
            "ats:skipped": "ats:SKIPPED",
            "ats:live": "ats:LIVE",
        },  # ...but is still live
        was_unconfirmed=was_unconfirmed,
    )
    assert "ats:SKIPPED:x1" in plan.unconfirmed  # streak neither advanced nor reset
    assert grace_period_counts(was_unconfirmed, {"ats:LIVE:y1"}, plan) == (0, 1)
