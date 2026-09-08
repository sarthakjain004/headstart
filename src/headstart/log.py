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
"""

from __future__ import annotations

import logging
import os
import sys
from importlib.machinery import ModuleSpec
from typing import NoReturn

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

    The traceback is whatever exception is being handled, so a site with none — a threshold
    tripping, not a failure — simply gets a bare line rather than logging's ``NoneType: None``.
    State is per-instance, so a per-run bound is a local and a bound shared by every caller of
    one leaf function is a module-level instance.
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._fired = False

    def report(self, message: str) -> None:
        """WARNING with its traceback the first time; INFO, without one, thereafter."""
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
