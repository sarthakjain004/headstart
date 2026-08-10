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
