"""Role-trend taxonomy seam (ADR-0040, ADR-0051, ADR-0220): the curated family list, the
seniority bands, and the watchlist of named roles tracked by title.

Since ADR-0220 a Job's family comes from a trained classifier over its title, and since ADR-0224
its description vector too (:mod:`headstart.ingest.role_family_classifier`); this module holds what the pipeline and the
Space must agree on around it. The family list lives in ``config/role_families.json``, curated
and in git, where each family has a display label and a one-line definition.

Bands come from the experience columns the table already carries (ADR-0009/0018) — banding
stored numbers, never re-extracting — with intern detected from the title or
``employment_type`` since interns rarely carry a years figure.

The **watchlist** (ADR-0051) is a second, deliberately independent title reading: named roles
matched on title patterns, so membership is explainable per Job and survives a new classifier.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_INTERN = re.compile(r"\bintern(ship)?\b|\btrainee\b", re.IGNORECASE)

NON_TECH = "non-tech"  # the reserved family: counted as a diagnostic, never charted


def load_families(path: Path) -> list[str]:
    """The curated family names, in display order, validated hard.

    A duplicate would merge two series under one name, and a family called ``non-tech`` would
    collide with the diagnostic series in the ledger, so either is refused."""
    spec = json.loads(path.read_text(encoding="utf-8"))
    names = [family["name"] for family in spec["families"]]
    if len(set(names)) != len(names):
        raise ValueError(f"{path}: a family is listed twice")
    if NON_TECH in names:
        raise ValueError(
            f"{path}: '{NON_TECH}' is reserved for the diagnostic series — a family of "
            "that name would collide with it in the ledger"
        )
    return names


def family_list_fingerprint(path: Path) -> str:
    """A short digest over which families exist, not over the file's bytes.

    Adding, removing or renaming a family changes what the chart's lines are, so
    ``trends_epochs`` marks the tick it first runs (ADR-0164), in its ``family_map_fingerprint``
    column: the name the column had when the list was a cluster map, kept rather than renamed.
    ``label`` and ``definition`` are left out: rewording a family changes no count."""
    return hashlib.sha256(
        json.dumps(sorted(load_families(path))).encode("utf-8")
    ).hexdigest()[:12]


def band(min_years: int | None, title: str | None, employment_type: str | None) -> str:
    """The seniority band for one row, from fields the served table already carries."""
    if _INTERN.search(title or "") or _INTERN.search(employment_type or ""):
        return "intern"
    if min_years is None:
        return "unspecified"
    if min_years <= 1:
        return "entry"
    if min_years <= 4:
        return "mid"
    if min_years <= 7:
        return "senior"
    return "staff"


WATCH_PREFIX = "watch:"  # ledger namespace for watched roles, so they can never collide with a family


class WatchRole:
    """One curated role tracked by title pattern (ADR-0051, amended by ADR-0052) — compiled once,
    matched per row.

    A watch role names what a family deliberately does not: a language (Java, Python), a named
    sub-role (Forward Deployed, Backend), or a specialty inside a family. It was first a
    workaround for centroids that could not express these (ADR-0051, ADR-0052); under a
    one-axis family list it is where those facets belong. A pattern is explainable, since you can
    say exactly why a Job counted, and survives a new classifier unchanged.

    A watch role is an overlay, never a partition: it re-counts Jobs already counted in their
    family, and overlaps its siblings (a "Senior Backend QA Engineer" counts under both).
    """

    __slots__ = ("_patterns", "label", "name", "parent")

    def __init__(self, name: str, label: str, parent: str, patterns: list[str]) -> None:
        self.name, self.label, self.parent = name, label, parent
        self._patterns = [re.compile(p, re.IGNORECASE) for p in patterns]

    def matches(self, title: str | None) -> bool:
        return bool(title) and any(p.search(title) for p in self._patterns)


def load_watchlist(path: Path, family_names: set[str]) -> list[WatchRole]:
    """The curated watchlist, validated hard — the same posture as :func:`load_families`,
    because the failure modes are as silent: a bad parent orphans the role from every drill,
    and a bad pattern would either crash the pipeline step or quietly count nothing.

    Missing file is an empty list, not an error: the watchlist is optional by design.
    """
    if not path.exists():
        return []
    spec = json.loads(path.read_text(encoding="utf-8"))
    watched: list[WatchRole] = []
    seen: set[str] = set()
    for entry in spec["roles"]:
        name = entry["name"]
        if name in seen:
            raise ValueError(f"{path}: watch role '{name}' defined twice")
        seen.add(name)
        if entry["parent"] not in family_names:
            raise ValueError(
                f"{path}: watch role '{name}' names parent '{entry['parent']}', which is not "
                "a family in role_families.json — the drill it should appear under does not exist"
            )
        try:
            watched.append(
                WatchRole(
                    name, entry.get("label", name), entry["parent"], entry["match"]
                )
            )
        except re.error as exc:
            raise ValueError(
                f"{path}: watch role '{name}' has a bad pattern: {exc}"
            ) from exc
    return watched
