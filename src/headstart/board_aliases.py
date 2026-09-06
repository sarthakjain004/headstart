"""Alias ledger: the ``(ats, duplicate) -> canonical`` table for Boards published twice (ADR-0111).

One CSV per ATS at ``data/validate/aliases/{ats}.csv``::

    ats,duplicate,canonical,signal,resolved_to,checked_at

A company can run one Board under two hostnames. Both go live in the liveness ledger, both are
scraped, and both are indexed under different ``board_key``s — so the same posting is served
twice. ``config._dedupe_boards`` cannot see it: that collapses Boards whose canonical
``board_key`` already matches modulo casing or URL form, and two different hostnames match
nothing.

This module is the *pure* half of the fix — grouping, classification and election, no network —
so the rules can be tested without a fixture server. Its I/O half is
``scripts/validate/dedupe_boards.py``, which fetches each Board's :meth:`alias_key` and writes
what comes back here.

**Grouping is a plain group-by, deliberately.** Entity resolution's standard answer to transitive
matches (``A`` is ``B``, ``B`` is ``C``) is a connected-components pass, and it buys nothing here:
the HTTP client follows the whole redirect chain, so ``A -> B -> C`` already resolves ``A``'s key
straight to ``C``. The transitive step happens in the transport. A future *pairwise* signal — id-set
overlap between two independently-served Boards, which is what Eightfold's aliases need — would
bring union-find back, and nothing shipping today is pairwise.

**A group is a duplicate cluster only when its key is itself a live Board**, and that one
condition is what keeps two unrelated companies apart. Measured 2026-09-06:
``careers.toagroup.com`` and ``jobs.bhs-world.com`` both resolve to SAP's marketing page, because
both tenants were decommissioned. Grouping on the resolved host alone would have declared them
each other's duplicate.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

FIELDS = ("ats", "duplicate", "canonical", "signal", "resolved_to", "checked_at")

#: Why a Board that moved is not a duplicate. Each needs a different action and none of them is
#: this module's to take (ADR-0111) — they are reported so the decision is informed.
MIGRATED = "migrated"  # resolves somewhere real that the ledger has never heard of
WWW_VARIANT = "www-variant"  # resolves to its own ``www.`` form
TOMBSTONE = "tombstone"  # resolves to the vendor's marketing page: the tenant is gone
UNREACHABLE = "unreachable"  # the probe itself failed; no verdict was earned
UNCONFIRMED = "canonical-unconfirmed"  # target is a live Board this scan never reached


@dataclass(frozen=True, slots=True)
class Alias:
    """One buried Board and the Board it duplicates."""

    ats: str
    duplicate: str
    canonical: str
    signal: str
    resolved_to: str
    checked_at: str  # ISO date, e.g. "2026-09-06"


@dataclass(frozen=True, slots=True)
class Cluster:
    """A canonical Board and the live Boards that resolve onto it."""

    key: str  # what they all resolved to; for a redirect, the canonical's own slug
    canonical: str
    duplicates: tuple[str, ...]
    signal: str


@dataclass(frozen=True, slots=True)
class Moved:
    """A Board that resolved elsewhere without that elsewhere being a live Board."""

    slug: str
    resolved_to: str
    reason: str


@dataclass(frozen=True, slots=True)
class Resolution:
    """Everything one scan concluded: what to bury, and what merely to look at."""

    clusters: tuple[Cluster, ...]
    moved: tuple[Moved, ...]


def resolve(
    alias_keys: Mapping[str, str | None],
    live: Collection[str],
    *,
    signal: str,
    vendor_hosts: Collection[str] = (),
    prefer: Collection[str] = (),
) -> Resolution:
    """Group live Boards by the key each resolved to, and say which of them duplicate which.

    ``alias_keys`` maps every probed Board's slug to what :meth:`BaseScraper.alias_key` returned
    (None where the probe failed). ``live`` is the set of slugs the liveness ledger calls live —
    the membership test that separates a duplicate from a Board that has simply moved.

    **The canonical is the resolved key itself, never inferred.** A redirect is the site owner
    declaring which hostname is the real one, so reading that answer beats any heuristic over the
    two names — and the measured data agrees: of 22 SuccessFactors clusters, direction ran
    SAP-host-to-vanity (BASF) and vanity-to-SAP-host (Colas) in the same ledger, so a rule
    preferring either shape is wrong somewhere. ``prefer`` holds curated slugs, each winning
    whichever cluster it belongs to, for the case where the redirect gets it wrong.

    A cluster whose canonical was not itself reached by this scan is **reported, not elected**: the
    only members present are the ones pointing away, and choosing among those would promote a
    Board we know is a duplicate.
    """
    prefer = set(prefer)
    live = set(live)
    groups: dict[str, list[str]] = defaultdict(list)
    moved: list[Moved] = []

    for slug, key in sorted(alias_keys.items()):
        if key is None:
            moved.append(Moved(slug, "", UNREACHABLE))
        else:
            groups[key].append(slug)

    clusters: list[Cluster] = []
    for key, members in sorted(groups.items()):
        if key not in live:
            moved.extend(
                Moved(slug, key, _why_not_a_duplicate(key, slug, vendor_hosts))
                for slug in members
            )
            continue
        if key not in members:
            moved.extend(Moved(slug, key, UNCONFIRMED) for slug in members)
            continue
        if len(members) == 1:
            continue  # resolves to itself and nothing else points at it: an ordinary Board
        canonical = next((m for m in members if m in prefer), key)
        duplicates = tuple(m for m in members if m != canonical)
        clusters.append(Cluster(key, canonical, duplicates, signal))

    return Resolution(tuple(clusters), tuple(moved))


def _why_not_a_duplicate(key: str, slug: str, vendor_hosts: Collection[str]) -> str:
    """Why ``slug`` resolving to ``key`` is not a duplicate of anything.

    Reporting-only, but the label decides what a person does next — seed the target, normalise the
    ledger row, or mark the Board dead — so the three are not collapsed into one."""
    if key in vendor_hosts:
        return TOMBSTONE
    if key.removeprefix("www.") == slug.removeprefix("www."):
        return WWW_VARIANT
    return MIGRATED


def aliases_of(resolution: Resolution, ats: str, checked_at: str) -> list[Alias]:
    """One :class:`Alias` row per buried Board, ready for :func:`write`."""
    return [
        Alias(ats, duplicate, c.canonical, c.signal, c.key, checked_at)
        for c in resolution.clusters
        for duplicate in c.duplicates
    ]


def path_for(liveness_dir: str | Path, ats: str) -> Path:
    """This ATS's alias ledger, resolved from the liveness dir it sits beside.

    Keyed off the liveness directory rather than the repo root because both callers already hold
    that — ``config.load_active_companies`` is handed it, and the script gets it from
    ``liveness.dir_for`` — so neither has to reconstruct a root by walking up parents."""
    return Path(liveness_dir).parent / "aliases" / f"{ats}.csv"


def load(path: str | Path) -> dict[str, str]:
    """``{duplicate_slug: canonical_slug}``, or empty when no ledger exists for this ATS.

    Missing-file-is-empty rather than an error: every ATS reads this on the scrape path and only
    the ones that have been scanned have a file. A read that raised would make adding an ATS a
    two-step change."""
    path = Path(path)
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        return {
            row["duplicate"]: row["canonical"]
            for row in csv.DictReader(fh)
            if row.get("duplicate") and row.get("canonical")
        }


def write(path: str | Path, aliases: Iterable[Alias]) -> None:
    """Replace the ledger at ``path``, sorted by duplicate so a diff reads as a change of fact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(aliases, key=lambda a: (a.ats, a.duplicate))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(FIELDS)
        for a in rows:
            writer.writerow(
                [a.ats, a.duplicate, a.canonical, a.signal, a.resolved_to, a.checked_at]
            )
