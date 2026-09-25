"""Read a job corpus into canonical ``Job``-shaped dicts for embedding/indexing (ADR-0014).

The source is a directory of ``{ats}.jsonl`` — the pipeline's own output, each line a canonical
``Job.to_dict`` (the production corpus). Deduped by ``id``: a resumed scrape can re-emit a board's
lines, and a Board can return the same id twice within one list. The second source was silently
~1% of the tech corpus until it was fixed in ``harvest.scrape_all`` (2026-09-16); the drop is now
counted rather than invisible, so the next source announces itself instead of hiding.

The lines are already canonical, so the embed/index path needs no per-source adapter and
``to_meta`` collapses for them (ADR-0007).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from headstart import log

_log = log.get(__name__)


def _read_jsonl_dir(path: Path) -> Iterator[dict]:
    seen: set[str] = set()
    for file in sorted(path.glob("*.jsonl")):
        duplicates = 0
        with file.open(encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    job = json.loads(line)
                    job_id = job["id"]
                except (KeyError, TypeError, ValueError) as exc:
                    # A bare JSONDecodeError names neither the file nor the line.
                    raise ValueError(f"{file}:{lineno}: {exc!r}") from exc
                if job_id in seen:
                    # A resumed scrape re-emitting a board's lines is one source. It was not the
                    # one actually firing: across the five runs of 2026-09-16 this dropped
                    # 4,135-4,347 lines a run with no shard resuming in any of them — the real
                    # source was `harvest.scrape_all` letting a Board's own list contain the same
                    # id twice, fixed there. This stays as the backstop it was always meant to be,
                    # and the count says so rather than the drop being silent.
                    duplicates += 1
                    continue
                seen.add(job_id)
                yield job
        if duplicates:
            # Per file, as each one finishes, rather than a total at the end: the end of a
            # generator only runs for a caller that exhausts it, and `iter_jobs` has callers that
            # could stop early. CLAUDE.md's "output must stream incrementally" asks for this shape
            # anyway.
            _log.info(f"corpus: {file.name} dropped {duplicates} duplicate id(s)")


def iter_jobs(source: str | Path) -> Iterator[dict]:
    """Yield canonical Job dicts from a corpus source: a ``{ats}.jsonl`` directory."""
    source = Path(source)
    if source.is_dir():
        yield from _read_jsonl_dir(source)
    else:
        raise ValueError(f"unsupported corpus source: {source} (want a jsonl dir)")
