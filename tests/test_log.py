"""Tests for the shared logging seam (headstart.log).

The formatter and the ``__main__``-name resolution are what every stage depends on, so they
are pinned here once: tag derivation, the WARNING/ERROR renderings (plain vs GitHub-Actions
annotation), the HEADSTART_LOG level switch, and setup idempotence.
"""

import logging

import headstart.log as log


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
