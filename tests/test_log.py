"""Tests for the shared logging seam (headstart.log).

The formatter and the ``__main__``-name resolution are what every stage depends on, so they
are pinned here once: tag derivation, the WARNING/ERROR renderings (plain vs GitHub-Actions
annotation), the HEADSTART_LOG level switch, and setup idempotence.
"""

import logging

import pytest

from headstart import log


def _record(name="headstart.ingest.scrape_run", level=logging.INFO, msg="hello"):
    return logging.LogRecord(name, level, __file__, 1, msg, None, None)


def test_info_line_is_time_tag_message(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    line = log._Formatter().format(_record())
    assert line.endswith(" [scrape_run] hello")
    assert line[2] == ":" and line[5] == ":"  # HH:MM:SS prefix


def test_warning_carries_its_level(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    line = log._Formatter().format(_record(level=logging.WARNING))
    assert line.endswith(" [scrape_run] WARNING: hello")


def test_warning_becomes_annotation_under_actions(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    formatter = log._Formatter()
    assert (
        formatter.format(_record(level=logging.WARNING))
        == "::warning::[scrape_run] hello"
    )
    assert (
        formatter.format(_record(level=logging.ERROR)) == "::error::[scrape_run] hello"
    )


def test_info_stays_plain_under_actions(monkeypatch):
    # only anomalies annotate — INFO would flood the summary page
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert not log._Formatter().format(_record()).startswith("::")


def test_annotation_escapes_newlines(monkeypatch):
    # workflow commands are line-oriented: a raw newline would truncate the annotation
    # mid-message (state_fetch's ABORT is multi-line), so %0A must carry it instead
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    line = log._Formatter().format(
        _record(level=logging.ERROR, msg="first line\nsecond line")
    )
    assert line == "::error::[scrape_run] first line%0Asecond line"


def test_fail_logs_error_and_exits_1(caplog):
    caplog.set_level(logging.ERROR, logger="headstart.ingest.embed_merge")
    with pytest.raises(SystemExit) as exc:
        log.fail(logging.getLogger("headstart.ingest.embed_merge"), "store corrupt")
    assert exc.value.code == 1
    assert ["store corrupt"] == [
        r.getMessage()
        for r in caplog.records
        if r.name == "headstart.ingest.embed_merge"
    ]


def test_get_resolves_dunder_main_via_spec():
    spec = log.ModuleSpec("headstart.ingest.embed_run", loader=None)
    assert log.get("__main__", spec).name == "headstart.ingest.embed_run"
    assert log.get("headstart.http").name == "headstart.http"
    assert log.get("__main__", None).name == "__main__"  # direct-script fallback


def test_setup_is_idempotent_and_reads_level(monkeypatch):
    logger = logging.getLogger("headstart")
    saved_handlers, saved_level = logger.handlers[:], logger.level
    try:
        logger.handlers = []
        monkeypatch.setenv("HEADSTART_LOG", "debug")
        log.setup()
        log.setup()
        assert len(logger.handlers) == 1
        assert logger.level == logging.DEBUG
        monkeypatch.setenv("HEADSTART_LOG", "nonsense")
        log.setup()
        assert logger.level == logging.INFO  # unknown value falls back to the default
    finally:
        logger.handlers, logger.level = saved_handlers, saved_level


def _exc_info():
    try:
        raise ValueError("the boom")
    except ValueError:
        import sys

        return sys.exc_info()


def test_exc_info_renders_the_traceback(monkeypatch):
    """``exc_info=True`` must actually cost a traceback — it silently did not.

    The formatter builds its line from ``record.getMessage()`` and never calls
    ``super().format()``, so every ``exc_info=True`` call site was a no-op: a scraper
    parse bug named neither file nor line. Locked here because the loss was invisible
    at the call site — the log read correctly, it was just useless."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    record = _record(level=logging.ERROR, msg="scrape failed")
    record.exc_info = _exc_info()
    line = log._Formatter().format(record)
    assert "[scrape_run] ERROR: scrape failed" in line
    assert "Traceback (most recent call last):" in line
    assert "ValueError: the boom" in line


def test_traceback_stays_inside_one_actions_annotation(monkeypatch):
    """A raw newline truncates a workflow command, so the traceback needs the same
    ``%0A`` escaping the message already gets — one annotation carrying the whole stack,
    not an annotation cut off at the word ``Traceback``."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    record = _record(level=logging.ERROR, msg="scrape failed")
    record.exc_info = _exc_info()
    line = log._Formatter().format(record)
    assert line.startswith("::error::[scrape_run] scrape failed%0A")
    assert "%0AValueError: the boom" in line
    assert "\n" not in line


def test_a_record_without_exc_info_is_unchanged(monkeypatch):
    """The traceback branch must not perturb the ~200 call sites that pass no exception."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert log._Formatter().format(_record()).endswith(" [scrape_run] hello")
