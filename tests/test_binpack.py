"""Tests for the shared fan-out packing (headstart.ingest.binpack, ADR-0025/0026).

LPT balance and dynamic shard sizing are used by both the embed and scrape planners; they live here
so they're tested once, on their own, with no ML or scrape deps.
"""

from __future__ import annotations

from headstart.ingest.binpack import lpt_pack, shard_count


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


def test_shard_count_clamps_and_scales():
    assert shard_count(0.0, 0, 15, 1200) == 0  # no work -> no shards
    assert shard_count(100.0, 50, 15, 1200) == 1  # small workload collapses to one
    assert shard_count(40_000.0, 5000, 15, 1200) == 15  # big workload saturates the cap
    assert shard_count(2400.0, 10, 15, 1200) == 2
    # unit-agnostic: works for board counts too (total == n_items)
    assert shard_count(8000, 8000, 15, 600) == 14
