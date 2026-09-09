"""Shared logging for the pipeline: one format, stderr, flushed per record.

Every pipeline stage and scraper logs through here instead of ad-hoc ``print``: a CLI entry
calls :func:`setup` once, modules take :func:`get`-built loggers, and one formatter renders
``HH:MM:SS [tag] message`` to stderr — the tag is the module's name, so a merged CI log still
says which stage (or which scraper) spoke. WARNING and above render as GitHub workflow
``::warning::`` / ``::error::`` annotations when running under Actions, so anomalies surface
on the run's summary page instead of being buried in fifteen shard logs.

Scoped to the ``headstart`` root logger on purpose: a handler on the *root* logger would also
adopt the ML stack's chatter (``sentence_transformers``, ``huggingface_hub``), which stays on
its own handlers. ``StreamHandler`` flushes per record, so the stream-incrementally rule holds
even mid-crash.

INFO is the default; ``HEADSTART_LOG=debug`` turns on per-board / per-retry detail.

WARNING is a **budget**, not a severity: an annotation costs one of GitHub's 10 per step / 50
per run, so ADR-0039's amendment forbids it on any line that can fire once per Board, per shard
or per item. The two ways to stay inside that budget while still saying what happened live here
beside the emit seam, so every stage spells them the same way: :class:`FirstOnly` (warn on the
first occurrence, inform on the rest) and :func:`named_sample` (one line that names a set).

:func:`context` is here for the same reason: the ``stage= run= attempt= sha=`` line every
``python -m`` entry point opens with is what says *which* run a log belongs to once it is off
the Actions page, and its callers are on both sides of the pipeline package — the ingest stages
and ``alerts/``, which may not import from ``ingest``.
"""

from __future__ import annotations

import logging
import os
import sys
from importlib.machinery import ModuleSpec
from typing import Any, NoReturn

#: This module's own logger, for the one line it emits itself (:func:`context`). Everything
#: else here writes through the caller's logger, which is the point of the seam.
_log = logging.getLogger(__name__)

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


class _Formatter(logging.Formatter):
    """``HH:MM:SS [tag] message``; the tag is the logger name's last dotted segment.

    WARNING+ carries its level name — or becomes a workflow annotation under GitHub Actions
    (the env var is checked per record, not at setup, so tests can flip it)."""

    def format(self, record: logging.LogRecord) -> str:
        tag = record.name.rsplit(".", 1)[-1]
        message = record.getMessage()
        if record.exc_info:
            # Rendered here because this formatter never calls ``super().format()``:
            # until it did, ``exc_info=True`` at a call site cost the traceback it
            # asked for and said nothing about the loss, so a parse bug in any of the
            # 25 scrapers named neither file nor line. Cached on the record the way
            # ``logging.Formatter`` does, so a second handler re-uses the render.
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
            message = f"{message}\n{record.exc_text}"
        if record.levelno >= logging.WARNING:
            if os.environ.get("GITHUB_ACTIONS"):
                kind = "error" if record.levelno >= logging.ERROR else "warning"
                # workflow commands are line-oriented: a raw newline would truncate the
                # annotation mid-message; %0A renders as a newline inside it
                return f"::{kind}::[{tag}] {message.replace(chr(10), '%0A')}"
            return (
                f"{self.formatTime(record, '%H:%M:%S')} [{tag}] "
                f"{record.levelname}: {message}"
            )
        return f"{self.formatTime(record, '%H:%M:%S')} [{tag}] {message}"


def get(name: str, spec: ModuleSpec | None = None) -> logging.Logger:
    """The logger for a module — pass ``__name__`` (plus ``__spec__`` in a CLI module).

    A module run as ``python -m headstart.ingest.X`` imports with ``__name__ == "__main__"``,
    which would fall outside the ``headstart`` root and never reach its handler; ``__spec__``
    still carries the real dotted name, so pass it wherever a module doubles as an entry point.
    """
    if name == "__main__" and spec is not None:
        name = spec.name
    return logging.getLogger(name)


def fail(logger: logging.Logger, message: str) -> NoReturn:
    """Log ``message`` at ERROR (an ``::error::`` annotation under Actions) and exit 1 —
    the one shape every fatal pipeline abort shares."""
    logger.error(message)
    raise SystemExit(1)


def context(stage: str, **extra: Any) -> None:
    """One line naming the run this log belongs to. Silent off CI, where it is noise.

    ``stage`` is **the calling module's own name** — ``scrape_run``, never ``scrape``;
    ``scrape_join``, never ``join``. The workflow's job names are the tempting alternative and
    they name a different thing: ``join`` is one Actions job running seven of these modules, so a
    log grepped by job answers "which runner" and a log grepped by stage answers "which code",
    and a vocabulary mixing the two answers neither. Where one module is several passes behind
    one entry point the pass rides as an ``extra`` instead of in ``stage`` — ``index``'s
    ``step=``, ``update_ledgers``' ``ledger=``. ``tests/test_log_contract.py`` enforces the rule.

    No bracketed prefix of its own: ADR-0039 fixes one line format whose only tag is the
    module's name, which the formatter already supplies. ``stage`` rides as a field.

    Here rather than in ``ingest/observability.py``, where it started, because its callers are
    on both sides of the pipeline package: every ``python -m headstart.ingest.*`` stage, plus
    ``alerts/run.py`` and ``alerts/bot.py``, which are ``python -m`` entry points under Actions
    too (``alerts.yml``, ``bot.yml``) and may not import from ``ingest`` — the same reason
    :func:`named_sample` and :class:`FirstOnly` are here rather than beside their first caller.
    ``observability`` keeps its other seams: each of those is about a CI *artifact* — a step
    summary, a shard report — where this one is a log line, which is what this module owns.
    The curated-feed entry (``python -m headstart``) still does not call it, now for the only
    reason that survives the move: no workflow runs it, so there is no run for it to name.
    """
    run = os.environ.get("GITHUB_RUN_ID")
    if not run:
        return
    bits = {
        "stage": stage,
        "run": run,
        "attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
        "sha": (os.environ.get("GITHUB_SHA") or "")[:7],
        **{k: v for k, v in extra.items() if v is not None},
    }
    _log.info(" ".join(f"{k}={v}" for k, v in bits.items()))


def named_sample(items: list[str], cap: int = 10) -> str:
    """``a, b, c, +N more`` — a warning that names what it is about, without becoming a dump.

    A count sends the reader to diff two artifacts to learn *which* Board a run lost; a full
    list of several hundred is skipped. Every caller wants the same compromise, so they share
    one, and they agree on the cap by sharing its default.

    Here rather than in the pipeline package because the callers are on both sides of it — the
    ingest stages and ``alerts/run.py`` — and the sampling contract is part of what a bounded
    log line is, which is what this module owns.
    """
    shown = ", ".join(items[:cap])
    rest = len(items) - cap
    return shown + (f", +{rest} more" if rest > 0 else "")


class FirstOnly:
    """A systemic fault costs one annotation, not one per item.

    The faults worth reporting per item are rarely per-item faults: a scraper whose
    ``board_key()`` starts raising fails on every Board it owns, and a parse break raises on
    every Board of its ATS. One line each would spend the whole annotation budget restating one
    bug and displace the aborts annotations exist for — but saying nothing loses the one thing
    that names the broken line. So the **first** occurrence warns and carries its traceback, and
    every one after it informs; the caller's own count or :func:`named_sample` says how far it
    reached.

    Level and traceback are decided together on purpose, because bounding one and not the other
    is a bug this repo shipped: ``index_plan``'s keep-set guard chose its emit function from the
    first occurrence and left ``exc_info=True`` unconditional, so a systemic failure printed one
    full stack per Board — the same flood, one indirection later.

    The traceback is whatever ``sys.exc_info()`` reports, which is a **thread**-wide question
    and not a per-frame one — the same rule ``logging``'s own ``exc_info=True`` follows. So a
    site with no exception anywhere gets a bare line rather than logging's ``NoneType: None``,
    but a site reached from inside an *unrelated* ``except``, however many frames up, attaches
    that unrelated stack. 8 of the 15 call sites are lexically inside the ``except`` they report
    on, so the stack is theirs by construction. The other seven report a *condition* rather than a
    caught exception, and they are clean today for two different strengths of reason — which is
    the part worth reading, not the count:

    - ``scrapers/workday.py``'s detail-loss tally, the original of the shape, is clean **by
      construction**: it is a threshold tripping at the end of a detail pass, with no ``except``
      anywhere above it that could still be handling something.
    - ``spare_egress``'s five tunnel checks are clean **by measurement** — an ``ast`` sweep of
      ``src/headstart`` for a network call lexically inside an ``except`` found none, and both
      of ``http.py``'s entries into them sit outside its ``except RequestsError``. That is the
      weaker guarantee: it holds for the call graph as it is, and any new caller that dials
      while handling an exception starts attaching that exception's stack to a line about WARP.
    - ``config``'s identity fallback is clean for a third reason: ``_report_identity_failure``
      is only ever called from ``board_identity``'s own ``except`` arm, so the live exception is
      precisely the ``board_key()`` failure the line is about. The report sits one frame below
      the handler rather than inside it, which is why it counts as outside here — the stack is
      still the right one.

    ``tests/test_log.py`` recomputes both figures from the source with ``ast`` rather than
    trusting this paragraph: the version that said "five of the six" shipped in the very commit
    that took the census to nine, and a census stated in prose goes stale the next time a site
    is added — so adding one means re-reading this, which is the point.

    Detected rather than declared, deliberately. Capturing ``sys.exc_info()`` at construction and
    diffing it at report time bounds nothing, because the instances that most need it are
    module-level and built at import with no exception live. Guessing from frame identity would
    silently *drop* the stack whenever a site reports through a helper — the same silent loss
    ADR-0039 records the formatter shipping, one layer up. And an explicit ``exc_info`` argument
    is flexibility no caller wants today; add it the day a site needs the choice made for it.

    State is per-instance, so a per-run bound is a local and a bound shared by every caller of
    one leaf function is a module-level instance.
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._fired = False

    def report(self, message: str) -> None:
        """WARNING with whatever traceback is live the first time; INFO, bare, thereafter."""
        first, self._fired = not self._fired, True
        (self._logger.warning if first else self._logger.info)(
            message, exc_info=first and sys.exc_info()[0] is not None
        )


def setup() -> None:
    """Configure the ``headstart`` logger — call once at each CLI entry (idempotent).

    Level comes from ``HEADSTART_LOG`` (debug/info/warning/error; default info)."""
    logger = logging.getLogger("headstart")
    logger.setLevel(
        _LEVELS.get(os.environ.get("HEADSTART_LOG", "").lower(), logging.INFO)
    )
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_Formatter())
        logger.addHandler(handler)
