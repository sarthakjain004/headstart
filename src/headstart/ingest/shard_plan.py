"""The typed shape of a stage's ``plan.json``, and the shard-index convention both run halves
read it through (ADR-0154).

``scrape_plan`` and ``embed_plan`` each write a ``plan.json`` beside the ``shard-{k}.jsonl``
files they hand their matrix, but the two are packed on different measured units — Board-seconds
(``board_cost.csv``) versus per-Bucket Doc-seconds — and only ``scrape_run`` ever reads its
predictions back. A shared base type would paper over that difference rather than remove it, so
``ScrapePlan`` and ``EmbedPlan`` stay two records; what they share is the *technique* (a
``@dataclass`` with ``to_json``/``from_json``, not a generic serializer — the tolerance each
needs differs enough that a shared helper would only save the one-line ``json.dumps`` call) and
the on-disk convention their readers rely on.

That convention is :func:`shard_index`: the shard number embedded in a planner-written assignment
filename (``shard-{k}.jsonl`` -> ``"k"``). It stays a plain function, not a method on either Plan,
because a shard learns its own index from its *own* CLI argument before it has read any plan at
all — the index is what a shard uses to look itself up in a Plan, not something a Plan hands out.
``scrape_run`` and ``embed_run`` both import it, the same "shared module both halves import"
pattern as :mod:`binpack` (shared by both planners) and :mod:`doc_prep` (shared by embed's own
plan/run pair).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


def shard_index(assignment: str | Path | None) -> str | None:
    """The shard number embedded in a planner-written assignment filename (``shard-{k}.jsonl``).

    ``None`` for a non-shard run (no ``--assignment``) — the monolith path, not an error.
    """
    return Path(assignment).stem.rsplit("-", 1)[-1] if assignment else None


@dataclass(frozen=True, slots=True)
class ScrapePlan:
    """``scrape_plan``'s ``plan.json``: the board slice each scrape shard was assigned.

    ``per_shard_minutes`` (the predicted wall clock — what a shard should take) and
    ``per_shard_serial_minutes`` (the packed sum — what its Boards cost run end to end) are
    reported separately so the join can measure the fan-out's speedup against the *serial*
    figure; measuring against the prediction, which is derived from that same speedup, would
    make the estimate chase its own tail (ADR-0054). Both are ``None`` on a cold start (no
    ``board_cost.csv`` measurements yet) — the plan still ships boards, just no prediction.
    """

    shards: list[int]
    count: int
    per_shard_boards: list[int]
    per_shard_minutes: list[float] | None = None
    per_shard_serial_minutes: list[float] | None = None

    def to_json(self) -> str:
        plan: dict[str, object] = {
            "shards": self.shards,
            "count": self.count,
            "per_shard_boards": self.per_shard_boards,
        }
        if self.per_shard_minutes is not None:
            plan["per_shard_minutes"] = [round(m, 2) for m in self.per_shard_minutes]
        if self.per_shard_serial_minutes is not None:
            plan["per_shard_serial_minutes"] = [
                round(m, 2) for m in self.per_shard_serial_minutes
            ]
        return json.dumps(plan, indent=2)

    @classmethod
    def from_json(cls, path: Path) -> ScrapePlan | None:
        """The plan beside a shard's own assignment file, or ``None`` for an older plan shape
        or a missing/corrupt file.

        Tolerant on purpose, at the whole-record level: a shard's own numbers matter more than
        this cross-check, so any read failure reads as "no prediction" rather than raising.
        """
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                shards=raw["shards"],
                count=raw["count"],
                per_shard_boards=raw["per_shard_boards"],
                per_shard_minutes=raw.get("per_shard_minutes"),
                per_shard_serial_minutes=raw.get("per_shard_serial_minutes"),
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return None

    def predicted_minutes(self, shard: str) -> float | None:
        """This shard's predicted wall-clock minutes — what it should take (ADR-0054)."""
        return self._at(self.per_shard_minutes, shard)

    def serial_minutes(self, shard: str) -> float | None:
        """This shard's packed serial minutes — what its Boards cost run end to end."""
        return self._at(self.per_shard_serial_minutes, shard)

    @staticmethod
    def _at(values: list[float] | None, shard: str) -> float | None:
        if values is None:
            return None
        try:
            return float(values[int(shard)])
        except (IndexError, ValueError, TypeError):
            return None


@dataclass(frozen=True, slots=True)
class EmbedPlan:
    """``embed_plan``'s ``plan.json``: the Doc slice each embed shard was assigned.

    Unlike :class:`ScrapePlan`, nothing currently reads ``makespan_s``/``per_shard_s`` back
    structurally — ``embed_run`` is stateless per shard and never compares its own time against
    the prediction the way ``scrape_run`` does. ``from_json`` exists anyway for symmetry with
    ``ScrapePlan`` and so a future reader (or a test) has it ready-made.
    """

    shards: list[int]
    count: int
    makespan_s: float
    per_shard_s: list[float]

    def to_json(self) -> str:
        plan = {
            "shards": self.shards,
            "count": self.count,
            "makespan_s": round(self.makespan_s, 1),
            "per_shard_s": [round(x, 1) for x in self.per_shard_s],
        }
        return json.dumps(plan, indent=2)

    @classmethod
    def from_json(cls, path: Path) -> EmbedPlan | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                shards=raw["shards"],
                count=raw["count"],
                makespan_s=raw["makespan_s"],
                per_shard_s=raw["per_shard_s"],
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return None
