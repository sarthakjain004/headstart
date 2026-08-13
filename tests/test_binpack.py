"""Tests for the shared fan-out packing (headstart.ingest.binpack, ADR-0025/0026).

LPT balance and dynamic shard sizing are used by both the embed and scrape planners; they live here
so they're tested once, on their own, with no ML or scrape deps.
"""

from __future__ import annotations

from headstart.ingest.binpack import lpt_pack, lpt_pack_capped, shard_count


def test_lpt_pack_balances_better_than_round_robin():
    costs = [10.0, 1.0, 1.0, 1.0, 1.0]
    assign, loads = lpt_pack(costs, 2)
    assert len(assign) == len(costs)
    assert all(0 <= k < 2 for k in assign)
    recomputed = [0.0, 0.0]
    for i, k in enumerate(assign):
        recomputed[k] += costs[i]
    assert recomputed == loads
    assert sum(loads) == sum(costs)
    assert max(loads) == 10.0  # LPT keeps the big item alone; round-robin would make 12


def test_lpt_pack_is_deterministic():
    costs = [3.0, 3.0, 2.0, 2.0, 1.0]
    assert lpt_pack(costs, 3) == lpt_pack(costs, 3)


def _per_shard(assign: list[int], groups: list[str], group: str, m: int) -> list[int]:
    counts = [0] * m
    for k, g in zip(assign, groups):
        if g == group:
            counts[k] += 1
    return counts


def test_capped_pack_holds_the_ceiling_for_a_group_dealt_first():
    costs = [1.0] * 12
    groups = ["eightfold"] * 6 + ["lever"] * 6
    assign, _ = lpt_pack_capped(costs, groups, 3)
    assert max(_per_shard(assign, groups, "eightfold", 3)) <= 2  # ceil(6/3)
    assert max(_per_shard(assign, groups, "lever", 3)) <= 2


def test_capped_pack_holds_the_ceiling_for_a_group_dealt_later():
    # the case a load-ordered heap gets wrong: by the time the cheap group is offered the shards
    # are already uneven, so every one of its items lands on whichever shard is lightest. Eightfold
    # ranks 7th of 18 ATSes by total cost, so it is never the first group — this is its real shape.
    costs = [100.0, 90.0, 1.0] + [1.0] * 6
    groups = ["big"] * 3 + ["eightfold"] * 6
    assign, _ = lpt_pack_capped(costs, groups, 3)
    assert max(_per_shard(assign, groups, "eightfold", 3)) <= 2


def test_capped_pack_reaches_every_shard_when_a_group_outnumbers_them():
    # the property the rate-limit budget actually needs: with more Boards than shards, no shard is
    # left without any — otherwise its independent origin budget goes unspent. n is deliberately
    # not a multiple of m, and the prior groups leave the shards unevenly loaded.
    costs = [100.0, 90.0, 80.0] + [1.0] * 17
    groups = ["big"] * 3 + ["eightfold"] * 17
    assign, _ = lpt_pack_capped(costs, groups, 4)
    counts = _per_shard(assign, groups, "eightfold", 4)
    assert min(counts) >= 1  # every shard carries some of the group
    assert max(counts) <= 5  # ceil(17/4)


def test_no_shard_exceeds_the_group_ceiling():
    # the guarantee is an upper bound per shard, not an even deal — a shard holding *few* Boards of
    # an ATS costs nothing, so only the ceiling is load-bearing for the rate-limit budget
    costs = [5.0, 4.0, 3.0, 2.0, 1.0, 1.0, 1.0, 50.0, 40.0]
    groups = ["a"] * 7 + ["heavy"] * 2
    assign, _ = lpt_pack_capped(costs, groups, 3)
    assert max(_per_shard(assign, groups, "a", 3)) <= 3  # ceil(7/3)
    assert max(_per_shard(assign, groups, "heavy", 3)) <= 1  # ceil(2/3)


def test_capped_pack_conserves_and_balances_load():
    costs = [10.0, 8.0, 6.0, 4.0, 2.0, 1.0]
    groups = ["a", "b", "a", "b", "a", "b"]
    assign, loads = lpt_pack_capped(costs, groups, 2)
    recomputed = [0.0, 0.0]
    for i, k in enumerate(assign):
        recomputed[k] += costs[i]
    assert recomputed == loads
    assert sum(loads) == sum(costs)


def test_capped_pack_is_deterministic():
    costs = [3.0, 3.0, 2.0, 2.0, 1.0]
    groups = ["a", "b", "a", "b", "a"]
    assert lpt_pack_capped(costs, groups, 3) == lpt_pack_capped(costs, groups, 3)


def test_capped_pack_caps_even_when_everything_is_one_group():
    # deliberately unlike lpt_pack: LPT would put the one heavy item alone and the four cheap ones
    # together (counts 1 and 4), which is the clustering the cap exists to forbid
    costs = [10.0, 1.0, 1.0, 1.0, 1.0]
    assign, loads = lpt_pack_capped(costs, ["only"] * 5, 2)
    counts = _per_shard(assign, ["only"] * 5, "only", 2)
    assert max(counts) <= 3  # ceil(5/2)
    assert sum(loads) == sum(costs)


def test_capped_pack_prefers_the_lighter_shard_inside_the_cap():
    # inside the cap it is plain LPT, so a group dealt into unevenly-loaded shards must go to the
    # lighter one first rather than filling shards in index order
    costs = [100.0, 1.0, 1.0]
    groups = ["big", "g", "g"]
    assign, _ = lpt_pack_capped(costs, groups, 2)
    heavy_shard = assign[0]
    assert assign[1] != heavy_shard  # the lighter shard wins while it is under the cap


def test_shard_count_clamps_and_scales():
    assert shard_count(0.0, 0, 15, 1200) == 0  # no work -> no shards
    assert shard_count(100.0, 50, 15, 1200) == 1  # small workload collapses to one
    assert shard_count(40_000.0, 5000, 15, 1200) == 15  # big workload saturates the cap
    assert shard_count(2400.0, 10, 15, 1200) == 2
    # unit-agnostic: works for board counts too (total == n_items)
    assert shard_count(8000, 8000, 15, 600) == 14
