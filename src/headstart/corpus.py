"""Read a job corpus into canonical ``Job``-shaped dicts for embedding/indexing (ADR-0014).

One reader over the two source shapes the search index ingests:

- a directory of ``{ats}.jsonl`` — the pipeline's own output, each line a canonical ``Job.to_dict``
  (the production corpus). Deduped by ``id`` because a resumed scrape can re-emit a board's lines.
- the one-off Wellfound CSV — non-canonical column names, kept only as the frozen eval benchmark.
  Its columns are adapted to the canonical shape here (the mapping the temporary ``to_meta`` did).

Both yield the same dict shape, so the embed/index path is source-agnostic and ``to_meta`` collapses
for the canonical JSONL sources (ADR-0007).
"""

from __future__ import annotations

import csv
import json
import sys
from collections.abc import Iterator
from pathlib import Path

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))  # descriptions can be long

# Wellfound CSV column -> canonical Job field (the only three that differ).
_WELLFOUND_RENAME = {
    "years_experience": "experience",
    "job_type": "employment_type",
    "compensation": "salary",
}


def board_of(job_id: str) -> str:
    """The Board an id belongs to: the ``{ats}:{slug}`` prefix of ``{ats}:{slug}:{native_id}``.

    Split off only the *last* segment, so a slug that itself contains ``:`` (Workday's URL slugs)
    is preserved. **This is a guess, not an exact answer** (ADR-0049): the native id can carry
    colons too — real Workday ids include ``REQ: 228``, a postal address and an entire URL — and
    for those this returns a Board that does not exist.

    Safe where both sides of a comparison run through this same function, which is how ``index
    sync`` uses it: a phantom Board is produced identically for the fresh id and the indexed one,
    so the scope check still pairs them. Not safe where the result is compared against real Board
    keys — ``plan_prune`` does that, and matches ids against the live keep-set by prefix instead.
    """
    return job_id.rsplit(":", 1)[0]


def _read_jsonl_dir(path: Path) -> Iterator[dict]:
    seen: set[str] = set()
    for file in sorted(path.glob("*.jsonl")):
        with file.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                job = json.loads(line)
                if job["id"] in seen:  # a resumed scrape re-emits a board's lines
                    continue
                seen.add(job["id"])
                yield job


def _read_wellfound_csv(path: Path) -> Iterator[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            yield _wellfound_row_to_job(row)


def _wellfound_row_to_job(row: dict) -> dict:
    """Adapt a raw Wellfound CSV row to the canonical Job dict shape (benchmark source only)."""

    def val(key: str) -> str | None:
        return (row.get(key) or "").strip() or None

    remote = (row.get("remote") or "").strip().lower()
    job = {
        "id": val("id"),
        "ats": val("ats"),
        "company": val("company"),
        "title": val("title"),
        "location": val("location"),
        "remote": {"true": True, "false": False}.get(remote),  # bool; None if blank
        "department": val("department"),
        "url": val("url"),
        "posted_at": val("posted_at"),
        "scraped_at": val("scraped_at"),
        "description": val("description"),
    }
    for src, dst in _WELLFOUND_RENAME.items():
        job[dst] = val(src)
    return job


def iter_jobs(source: str | Path) -> Iterator[dict]:
    """Yield canonical Job dicts from a corpus source: a ``{ats}.jsonl`` directory or a Wellfound CSV."""
    source = Path(source)
    if source.is_dir():
        yield from _read_jsonl_dir(source)
    elif source.suffix == ".csv":
        yield from _read_wellfound_csv(source)
    else:
        raise ValueError(
            f"unsupported corpus source: {source} (want a jsonl dir or a .csv)"
        )
