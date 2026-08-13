"""Cost-balanced fan-out sizing and packing, shared by the pipeline planners (ADR-0025/0026).

Both the embed planner (by per-Bucket cost) and the scrape planner (by per-Board cost) size a
dynamic shard count and bin-pack their work items across the shards. That sizing is generic — it
lives here so the two planners can't drift on it, and so it is unit-testable on its own. The packs
themselves have diverged: embed uses plain :func:`lpt_pack`, while scrape uses
:func:`lpt_pack_capped`, which additionally caps how many Boards of one ATS a shard may take
(ADR-0047).
"""

from __future__ import annotations

import math
from heapq import heapify, heappop, heappush, heapreplace


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


def lpt_pack_capped(
    costs: list[float], groups: list[str], m: int
) -> tuple[list[int], list[float]]:
    """LPT with a **ceiling on how many of any one group a single shard may take**.

    Plain LPT inside the cap, so cost balance is still the rule that picks the shard; the cap only
    removes a shard from the running once it holds ``ceil(n/m)`` of the group being dealt. Groups
    go heaviest-total first and items heaviest-first, as in :func:`lpt_pack`.

    This exists because a shard's *network origin* is a shared resource: parallel Actions shards
    get distinct egress IPs, so an ATS that rate-limits per origin gets one budget per shard, and
    clustering that ATS's Boards on a few shards leaves the other budgets unspent (ADR-0047). Only
    the *upper* bound protects that budget — a shard holding few Boards of an ATS costs nothing —
    which is why this caps rather than forcing an even deal. It costs ~2-3% on the makespan against
    plain LPT; forcing an even deal cost ~12%.

    The cap has to be an invariant rather than a side effect of greedy packing. Offering a group
    into a heap ordered by load alone spreads only the *first* group — every later group meets an
    already-uneven heap and can pour entirely into whichever shard happens to be lightest. With
    costs ``[100, 90, 1] + [1]*6`` and groups ``['big']*3 + ['small']*6`` over 3 shards, that rule
    puts all six ``small`` items on one shard.
    """
    by_group: dict[str, list[int]] = {}
    for i, group in enumerate(groups):
        by_group.setdefault(group, []).append(i)
    assign = [0] * len(costs)
    loads = [0.0] * m
    for group in sorted(by_group, key=lambda g: -sum(costs[i] for i in by_group[g])):
        members = sorted(by_group[group], key=lambda i: -costs[i])
        cap = math.ceil(len(members) / m)
        # Loads carry across groups (global balance); the cap counter is per group. A shard leaves
        # the heap on reaching the cap instead of being re-pushed.
        heap = [(loads[k], k) for k in range(m)]
        heapify(heap)
        taken = [0] * m
        for i in members:
            load, k = heappop(heap)
            assign[i] = k
            loads[k] = load + costs[i]
            taken[k] += 1
            if taken[k] < cap:
                heappush(heap, (loads[k], k))
    return assign, loads
