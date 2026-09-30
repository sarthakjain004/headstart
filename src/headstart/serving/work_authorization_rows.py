"""The served Jobs each text-derived work-authorisation stance names, read once a process
(ADR-0333).

The ``work_authorization`` Search filter keeps the Jobs whose description offers visa
sponsorship, refuses it, or offers relocation, as :mod:`headstart.jobs.work_authorization`'s
rules read them. The served table has no column for it: the stance is read from the description at
query time, the way a description keyword is (ADR-0320), and kept for the life of the process,
since the served table never changes under a running one (the Space restarts on a new table).

:meth:`WorkAuthorizationRows.start` reads, in a thread of its own, every description the rules'
:data:`~headstart.jobs.work_authorization.PREFILTER` matches (the engine's regex picks them,
134,327 of 500,167 rows on 2026-09-29), in batches, and keeps each stance's Job ids. It took 29 s
on a laptop, about a minute on the Space's two CPUs. :meth:`clause` then names a stance's Jobs as
``id IN (…)``: the stance a filter asks for, compiled by
:func:`~headstart.search_filters.compiler.build_filter` like any other clause, reads the same on
the served table, on a description keyword's rows read into memory, and on a family table. Named
by id, 65,106 refusals counted in 0.11 s and ranked a page in 0.12 s (LanceDB 0.36, two threads).
"""

from __future__ import annotations

import threading
from typing import Any

from headstart import log
from headstart.jobs import work_authorization
from headstart.search_filters.compiler import ids_in_clause

_log = log.get(__name__)

#: Rows a batch of the read holds: descriptions average about 5,000 characters, so a batch is
#: about 40 MB of text, never the whole 660 MB at once.
READ_BATCH_ROWS = 8_192


class WorkAuthorizationRows:
    """Each stance's Jobs in the served ``table``, read once (ADR-0333)."""

    def __init__(self, table: Any) -> None:
        self._table = table
        self._clauses: dict[str, str] = {}
        self._may_offer_because: dict[str, tuple[str, ...]] = {}
        self._ready = threading.Event()
        self._failed = False
        self._started = False
        self._start_lock = threading.Lock()

    def start(self) -> None:
        """Begin the read in a daemon thread, once; later calls do nothing. A table without a
        description column holds no stance, and is ready at once with none."""
        with self._start_lock:
            if self._started:
                return
            self._started = True
        if "description" not in self._table.schema.names:
            self._ready.set()
            return
        threading.Thread(
            target=self._read, name="work-authorization-rows", daemon=True
        ).start()

    def wait(self, timeout: float) -> bool:
        """Whether the read has finished, waiting up to ``timeout`` seconds for it."""
        return self._ready.wait(timeout)

    @property
    def failed(self) -> bool:
        """Whether the read ended in an error, which leaves every stance unknown, not empty."""
        return self._failed

    def clause(self, stance: str) -> str:
        """A where-clause keeping ``stance``'s Jobs. Only once :meth:`wait` has said the read
        finished; a stance none holds keeps nothing. ``may_offer_sponsorship`` keeps the Jobs
        that offer sponsorship too (ADR-0353)."""
        return self._clauses.get(stance, ids_in_clause([]))

    def sponsorship(self, job_id: str) -> dict[str, Any]:
        """The sponsorship stance of a Job the ``may_offer_sponsorship`` filter keeps, and why a
        possible offer is not a firm one (ADR-0367): read with the stances, so a page tags its
        rows at no cost. Only once :meth:`wait` has said the read finished."""
        because = self._may_offer_because.get(job_id)
        if because is None:
            return {"stance": work_authorization.OFFERS_SPONSORSHIP, "because": []}
        return {
            "stance": work_authorization.MAY_OFFER_SPONSORSHIP,
            "because": list(because),
        }

    def _read(self) -> None:
        ids: dict[str, list[str]] = {s: [] for s in work_authorization.STANCES}
        may_offer_because: dict[str, tuple[str, ...]] = {}
        read = 0
        try:
            reader = (
                self._table.search()
                .where(f"regexp_like(description, '{work_authorization.PREFILTER}')")
                .select(["id", "title", "location", "description"])
                .to_batches(batch_size=READ_BATCH_ROWS)
            )
            for batch in reader:
                for job_id, title, location, text in zip(
                    batch.column("id").to_pylist(),
                    batch.column("title").to_pylist(),
                    batch.column("location").to_pylist(),
                    batch.column("description").to_pylist(),
                    strict=True,
                ):
                    read += 1
                    held = work_authorization.reading(
                        text, title=title, location=location
                    )
                    for stance in work_authorization.filtered_stances(held.stances):
                        ids[stance].append(job_id)
                    if held.may_offer_because:
                        may_offer_because[job_id] = held.may_offer_because
        except Exception:  # noqa: BLE001 — a failed read is said, and refused per request
            _log.exception(
                "work authorization stances unread after %d descriptions", read
            )
            ids = {s: [] for s in work_authorization.STANCES}
            may_offer_because = {}
            self._failed = True
        self._clauses = {stance: ids_in_clause(found) for stance, found in ids.items()}
        self._may_offer_because = may_offer_because
        _log.info(
            "work authorization stances read from %d descriptions: %s",
            read,
            {stance: len(found) for stance, found in ids.items()},
        )
        self._ready.set()
