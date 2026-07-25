"""Cost-balanced fan-out sizing and packing, shared by the pipeline planners (ADR-0025/0026).

Both the embed planner (by per-Bucket cost) and the scrape planner (by per-Board cost) size a
dynamic shard count and LPT-bin-pack their work items across the shards. That logic is generic —
it lives here so the two planners can't drift, and so it is unit-testable on its own.
"""

from __future__ import annotations

import math
from heapq import heapify, heapreplace


def shard_count(total: float, n_items: int, max_shards: int, target: float) -> int:
    """How many shards to spin: enough that each carries ~``target`` of ``total``, clamped to
    ``[1, max_shards]`` when there is work (0 when there isn't). A big workload saturates the cap;
    a small one collapses to a single shard — no spinning many VMs for a handful of items apiece.

    ``total``/``target`` share a unit chosen by the caller: seconds of embed cost, or board count.
    ``n_items`` is only the has-work guard."""
    if n_items == 0:
        return 0
    return max(1, min(max_shards, math.ceil(total / target)))


def lpt_pack(costs: list[float], m: int) -> tuple[list[int], list[float]]:
    """Longest-Processing-Time bin-pack: return (shard-index per item, per-shard load).

    Sort items by cost descending, then hand each to the currently least-loaded shard (a min-heap
    of ``(load, shard)``). Heavy-first keeps the makespan — the slowest shard — tight on a
    heavy-tailed cost distribution: a 4/3-approximation of the optimal, versus round-robin's
    reliable straggler."""
    order = sorted(range(len(costs)), key=lambda i: costs[i], reverse=True)
    heap = [(0.0, k) for k in range(m)]
    heapify(heap)
    assign = [0] * len(costs)
    loads = [0.0] * m
    for i in order:
        load, k = heap[0]
        assign[i] = k
        loads[k] = load + costs[i]
        heapreplace(heap, (loads[k], k))
    return assign, loads
