"""Read a job corpus into canonical ``Job``-shaped dicts for embedding/indexing (ADR-0014).

One reader over the two source shapes the search index ingests:

- a directory of ``{ats}.jsonl`` — the pipeline's own output, each line a canonical ``Job.to_dict``
  (the production corpus). Deduped by ``id``: a resumed scrape can re-emit a board's lines, and a
  Board can return the same id twice within one list. The second source was silently ~1% of the
  tech corpus until it was fixed in ``harvest.scrape_all`` (2026-09-16); the drop is now counted
  rather than invisible, so the next source announces itself instead of hiding.
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

from headstart import log

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))  # descriptions can be long

# Wellfound CSV column -> canonical Job field (the only three that differ).
_WELLFOUND_RENAME = {
    "years_experience": "experience",
    "job_type": "employment_type",
    "compensation": "salary",
}


_log = log.get(__name__)


def _read_jsonl_dir(path: Path) -> Iterator[dict]:
    seen: set[str] = set()
    for file in sorted(path.glob("*.jsonl")):
        duplicates = 0
        with file.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                job = json.loads(line)
                if job["id"] in seen:
                    # A resumed scrape re-emitting a board's lines is one source. It was not the
                    # one actually firing: across the five runs of 2026-09-16 this dropped
                    # 4,135-4,347 lines a run with no shard resuming in any of them — the real
                    # source was `harvest.scrape_all` letting a Board's own list contain the same
                    # id twice, fixed there. This stays as the backstop it was always meant to be,
                    # and the count says so rather than the drop being silent.
                    duplicates += 1
                    continue
                seen.add(job["id"])
                yield job
        if duplicates:
            # Per file, as each one finishes, rather than a total at the end: the end of a
            # generator only runs for a caller that exhausts it, and `iter_jobs` has callers that
            # could stop early. CLAUDE.md's "output must stream incrementally" asks for this shape
            # anyway.
            _log.info(f"corpus: {file.name} dropped {duplicates} duplicate id(s)")


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
