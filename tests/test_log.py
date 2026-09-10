"""Tests for the shared logging seam (headstart.log).

The formatter and the ``__main__``-name resolution are what every stage depends on, so they
are pinned here once: tag derivation, the WARNING/ERROR renderings (plain vs GitHub-Actions
annotation), the HEADSTART_LOG level switch, and setup idempotence.
"""

import ast
import logging
import pathlib

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


def test_first_only_spends_one_annotation_and_one_stack(caplog):
    """N occurrences of one systemic fault cost exactly one WARNING and exactly one traceback.

    Both halves matter, and they are one test on purpose: ``index_plan``'s hand-rolled version
    of this bounded the level and left ``exc_info=True`` unconditional, so a scraper whose
    ``board_key`` started raising still printed a full stack per Board. Asserting only the
    WARNING count would have passed against that bug.
    """
    guard = log.FirstOnly(logging.getLogger("headstart.test_first_only"))
    with caplog.at_level(logging.INFO, logger="headstart.test_first_only"):
        for i in range(50):
            try:
                raise ValueError("the boom")
            except ValueError:
                guard.report(f"board {i}: no board_key")

    assert len(caplog.records) == 50
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert [r.getMessage() for r in warnings] == ["board 0: no board_key"]
    assert [r for r in caplog.records if r.exc_info] == warnings
    assert all(r.levelno == logging.INFO for r in caplog.records[1:])


def test_first_only_outside_an_except_block_carries_no_traceback(caplog):
    """A threshold tripping is not a failure, and `exc_info=True` with no live exception renders
    ``NoneType: None`` — a stack-shaped line saying nothing. Every site of this idiom that
    reports a condition rather than a caught exception (workday's detail-loss tally,
    `spare_egress`'s tunnel checks) is exactly that shape, so the helper has to hold there
    too."""
    guard = log.FirstOnly(logging.getLogger("headstart.test_first_only_bare"))
    with caplog.at_level(logging.INFO, logger="headstart.test_first_only_bare"):
        guard.report("over the loss share")
        guard.report("over the loss share")

    assert [r.levelno for r in caplog.records] == [logging.WARNING, logging.INFO]
    assert not any(r.exc_info for r in caplog.records)


def test_first_only_inherits_an_unrelated_exception_from_up_the_stack(caplog):
    """The documented limit, pinned: `exc_info` detection is thread-wide, not per-frame.

    `FirstOnly.report` asks `sys.exc_info()`, exactly as `logging`'s own `exc_info=True` does,
    and that answers "what is this *thread* handling", not "what is my caller handling". So a
    site with no `except` of its own still attaches whatever stack is live above it — here a
    transport blip three frames up, on a line reporting a detail-loss threshold.

    Latent rather than live: most `.report` sites sit lexically inside the `except` they report
    on, and the ones that do not (`scrapers/workday.py`'s detail-loss tally, the shape below,
    and `spare_egress`'s tunnel checks) have a clean chain today. The class docstring carries
    the census — recomputed by the test below, not remembered — and this pins the behaviour it
    describes, so that a later change
    making the code match the *original* claim — that a site with no failure of its own gets a
    bare line — fails here loudly instead of quietly widening behaviour nobody re-read.
    """
    guard = log.FirstOnly(logging.getLogger("headstart.test_first_only_inherited"))

    def over_the_loss_share():  # no `except` here — workday.py's shape
        guard.report("acme: 900 of 1000 detail(s) failed mid-crawl")

    with caplog.at_level(logging.INFO, logger="headstart.test_first_only_inherited"):
        try:
            raise ConnectionResetError("an unrelated transport blip three frames up")
        except ConnectionResetError:
            over_the_loss_share()

    (record,) = caplog.records
    assert record.levelno == logging.WARNING
    assert record.exc_info is not None, (
        "inherited, not suppressed — the documented behaviour"
    )
    assert record.exc_info[0] is ConnectionResetError


def _report_sites() -> tuple[int, list[str]]:
    """Every ``FirstOnly.report()`` call in the package, and the files of those not in an
    ``except`` — read with ``ast``, never imported (CI installs base deps only)."""
    root = pathlib.Path(log.__file__).parent
    total, outside = 0, []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        bound = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and ast.unparse(node.value.func) == "log.FirstOnly"
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        guarded = {
            id(child)
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
            for stmt in node.body
            for child in ast.walk(stmt)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "report"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in bound
            ):
                total += 1
                if id(node) not in guarded:
                    outside.append(path.relative_to(root).as_posix())
    return total, outside


def test_the_first_only_docstring_census_is_recomputed_not_remembered():
    """The class docstring counts its own call sites, and that count has shipped stale.

    `FirstOnly`'s traceback caveat turns on how many `.report` sites sit inside the `except`
    they report on, and the sentence saying so was written when there were six — in the same
    commit that took the census to nine. A number in prose cannot notice the tenth site, so it
    is recomputed here instead: this fails the moment a site is added, which is exactly when
    the caveat needs re-reading.
    """
    total, outside = _report_sites()
    assert total, (
        "found no `.report` sites at all — this scan has stopped matching them"
    )
    assert (
        f"{total - len(outside)} of the {total} call sites" in log.FirstOnly.__doc__
    ), (
        f"`FirstOnly`'s docstring names a different census than the source has: "
        f"{total - len(outside)} of {total} sites are inside an `except`"
    )
    assert "scrapers/workday.py" in outside, (
        "the docstring names workday's detail-loss tally as the original site with no `except` "
        f"of its own, and the source no longer agrees: {sorted(set(outside))}"
    )


def test_context_is_silent_off_ci(monkeypatch, caplog):
    """Off CI there is no run to correlate, so the line would be noise in every local run."""
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    log.context("scrape_run")
    assert not caplog.records


def test_context_names_the_run_and_carries_its_extras(monkeypatch, caplog):
    """The whole point of the line: which run, which attempt, which commit — and which shard,
    since fifteen concurrent producers write into one log. An extra that is `None` is dropped
    rather than rendered as `shard=None`, so a non-fanned-out stage says nothing about shards."""
    for name, value in {
        "GITHUB_RUN_ID": "32671773723",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_SHA": "674b0679d4e0ab0f2f6e5b6a1c8a9d0e1f2a3b4c",
    }.items():
        monkeypatch.setenv(name, value)
    caplog.set_level(logging.INFO, logger="headstart.log")

    log.context("scrape_run", shard=3, held=None)

    (record,) = caplog.records
    assert (
        record.getMessage()
        == "stage=scrape_run run=32671773723 attempt=2 sha=674b067 shard=3"
    )
