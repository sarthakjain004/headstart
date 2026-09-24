"""The schema.org ``JobPosting`` a job page embeds as JSON-LD, found and read once for every
scraper whose detail pass parses a page (ADR-0196). A helper, not a scraper: the registry never
names it.

Three things, smallest first:

- :func:`jsonld_nodes` finds every node of one schema.org type in a page, and
  :func:`find_job_posting` the first ``JobPosting``;
- :func:`job_posting_fields` maps the fields every such scraper reads the same way;
- :func:`place_of` turns a ``jobLocation`` into the "Locality, Region, Country" string.

The finder accepts every shape any one scraper's own copy accepted, save one — Jobvite's took its
first block whatever its ``@type``, and every one of 25 live Jobvite JSON-LD pages states
``JobPosting`` — so no scraper reads less:

- every ``<script … application/ld+json>`` block, whatever the tag's other attributes or quoting
  — Meta's tags carry more than ``type`` (50 of 50 pages sampled 2026-09-24), and JazzHR serves
  its ``Organization`` block before the ``JobPosting`` one (33 of 33);
- JSON parsed with ``strict=False``: Trakstar embeds literal newlines inside string values, which
  strict parsing rejects (45 of 45 of its JSON-LD pages);
- ``@type`` as a string or a list, and a top-level array of nodes — both valid JSON-LD, already
  read by five and six of the nine copies this replaced;
- a ``@graph`` of nodes, the one shape no copy read. None of 460 live pages across nine ATSes
  used it, nor a list ``@type`` or an array (ADR-0196); it is read because it is how JSON-LD
  commonly nests several nodes, and a node found there is still a ``JobPosting``.

What stays in each scraper is only what that scraper chose on purpose: iCIMS's field allowlist and
its fabricated-date filter, Meta's extra description sections, Workday's first-``employmentType``
mapping onto its own ``timeType`` wording, and so on.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Iterator
from typing import Any, TypedDict

_JSONLD_BLOCK = re.compile(
    r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE
)


class JobPostingFields(TypedDict):
    """The fields every page-reading scraper maps from a ``JobPosting`` the same way."""

    title: str | None
    description: str | None
    location: str | None
    posted_at: str | None
    employment_type: str | None
    remote: bool | None


def jsonld_nodes(page: str, schema_type: str) -> Iterator[dict[str, Any]]:
    """Every JSON-LD node of ``schema_type`` in ``page``, in document order.

    A block that does not parse is skipped, not fatal: one malformed block must not hide a
    well-formed one after it.
    """
    for block in _JSONLD_BLOCK.findall(page):
        try:
            data = json.loads(block, strict=False)
        except ValueError:
            continue
        for node in _flattened(data):
            kind = node.get("@type")
            if kind == schema_type or (isinstance(kind, list) and schema_type in kind):
                yield node


def find_job_posting(page: str) -> dict[str, Any] | None:
    """The page's first ``JobPosting`` node, or None when it carries none."""
    return next(jsonld_nodes(page, "JobPosting"), None)


def job_posting_fields(node: dict[str, Any]) -> JobPostingFields:
    """The common fields of one ``JobPosting`` node.

    A list ``employmentType`` is joined, since each entry is a real type the posting states;
    ``remote`` is True on ``TELECOMMUTE`` and otherwise None — the absence of that value says
    nothing about whether the job is onsite. ``location`` is :func:`place_of` with no options; a
    scraper that drops more replaces it.
    """
    employment = node.get("employmentType")
    if isinstance(employment, list):
        employment = ", ".join(str(e) for e in employment) or None
    return {
        "title": node.get("title"),
        "description": node.get("description"),
        "location": place_of(node.get("jobLocation")),
        "posted_at": node.get("datePosted"),
        "employment_type": employment,
        "remote": True if node.get("jobLocationType") == "TELECOMMUTE" else None,
    }


def place_of(
    job_location: Any,
    *,
    placeholders: Collection[str] = (),
    drop_repeats: bool = False,
) -> str | None:
    """The first ``Place``'s "Locality, Region, Country", or None without an address.

    Only the first Place is read: a posting listing several sites gives no signal to prefer one.
    An ``addressCountry`` given as a ``Country`` node contributes its ``name``. Each part is
    stripped and an empty one dropped; ``placeholders`` drops the literal values a tenant writes
    into unset parts (iCIMS's ``UNAVAILABLE``), and ``drop_repeats`` drops a part an earlier one
    already holds, whole or as one of its comma-separated pieces ("Telangana,IN" already holds
    "IN").
    """
    if isinstance(job_location, list):
        job_location = job_location[0] if job_location else None
    if not isinstance(job_location, dict):
        return None
    address = job_location.get("address")
    if not isinstance(address, dict):
        return None
    country = address.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name")
    parts: list[str] = []
    for value in (
        address.get("addressLocality"),
        address.get("addressRegion"),
        country,
    ):
        text = str(value).strip() if value else ""
        if not text or text in placeholders:
            continue
        if drop_repeats and (
            text in parts or any(text in part.split(",") for part in parts)
        ):
            continue
        parts.append(text)
    return ", ".join(parts) or None


def _flattened(data: Any) -> Iterator[dict[str, Any]]:
    """Each node of a parsed block: the block itself or its array's items, then any ``@graph``."""
    for node in data if isinstance(data, list) else [data]:
        if not isinstance(node, dict):
            continue
        yield node
        graph = node.get("@graph")
        for member in graph if isinstance(graph, list) else [graph]:
            if isinstance(member, dict):
                yield member
