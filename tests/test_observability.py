"""Tests for headstart.ingest.observability — the observability seam.

Its three jobs are to be *safe*: a summary or a shard report is telemetry, and none of it may
break a run that is otherwise fine (or already dying on its time budget). So the failure paths
matter as much as the happy ones.
"""

from __future__ import annotations

import json
from collections import Counter

from headstart.ingest import observability
from headstart.ingest.observability import ScrapeHealth


def test_preparation_progress_reports_every_five_seconds_or_500_jobs(
    caplog, monkeypatch
):
    import logging

    caplog.set_level(logging.INFO)
    clock = iter([0.0, 1.0, 5.0])
    monkeypatch.setattr(observability.time, "monotonic", lambda: next(clock))
    progress = observability.PreparationProgress(logging.getLogger("headstart.test"))
    progress.report(1, 1, 0, 0)
    progress.report(3, 1, 1, 1)
    assert (
        "scanned 3, prepared 1, already 1, non-English 1" in caplog.records[-1].message
    )


def test_summary_is_a_no_op_without_the_github_env(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    observability.summary("Anything", ["- a line"])  # must not raise off CI


def test_summary_appends_so_every_stage_lands_on_one_page(monkeypatch, tmp_path):
    path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))
    observability.summary("Scrape plan", ["- 20000 boards"])
    observability.summary("Index sync", ["- added 1,234"])

    body = path.read_text(encoding="utf-8")
    assert "### Scrape plan" in body and "### Index sync" in body
    assert body.index("Scrape plan") < body.index("Index sync")


def test_summary_swallows_an_unwritable_path(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "nope" / "summary.md"))
    observability.summary(
        "Scrape plan", ["- a line"]
    )  # a bad path must not fail the stage


def test_shard_report_round_trips_through_the_fragment_dir(tmp_path):
    observability.write_shard(
        tmp_path / "shard-0",
        observability.ShardReport(shard="0", undone=12, killed_by_budget=True),
    )
    observability.write_shard(
        tmp_path / "shard-1",
        observability.ShardReport(shard="1", undone=0, killed_by_budget=False),
    )

    reports = observability.read_shards(tmp_path)
    assert {r.shard for r in reports} == {"0", "1"}
    assert sum(r.undone for r in reports) == 12


def test_read_shards_skips_a_corrupt_report_rather_than_dying(tmp_path):
    """The join's real work is unioning job data; a shard's broken telemetry must not stop it."""
    observability.write_shard(
        tmp_path / "shard-0", observability.ShardReport(shard="0")
    )
    bad = tmp_path / "shard-1"
    bad.mkdir()
    (bad / "_shard_report.json").write_text("{not json", encoding="utf-8")

    assert [r.shard for r in observability.read_shards(tmp_path)] == ["0"]


def test_shard_report_from_json_tolerates_missing_fields():
    """The tolerance every reader used to reimplement now lives in one place (ADR-0153): a
    report missing every optional field still parses, with sane zero/empty defaults."""
    report = observability.ShardReport.from_json({"boards_ok": ["workday:a"]})
    assert report is not None
    assert report.errors == {} and report.truncated == {} and report.observations == {}
    assert report.seconds == 0.0 and report.predicted_minutes is None
    assert report.malformed is False  # missing optional fields are not malformed


def test_shard_report_from_json_flags_the_wrong_shape():
    assert observability.ShardReport.from_json(["not", "an", "object"]) is None

    report = observability.ShardReport.from_json({"observations": ["bad"]})
    assert report is not None
    assert report.observations == {} and report.malformed is True


def test_percentiles_expose_the_straggler_a_mean_would_hide():
    """1,330 fast boards beside one 2,237s monster: the mean says 3s, p99/max say otherwise."""
    values = [1.0] * 1329 + [2237.0]
    spread = observability.percentiles(values)

    assert spread["p50"] == 1.0
    assert spread["max"] == 2237.0
    assert observability.percentiles([]) == {}


def test_error_summary_groups_by_type_and_ats():
    errors = {
        "lever:a": "Timeout: slow",
        "lever:b": "Timeout: slower",
        "workday:c": "Timeout: slowest",
        "greenhouse:d": "HTTPError: 500",
    }
    assert observability.error_summary(errors) == (
        "3 Timeout (lever 2, workday 1); 1 HTTPError (greenhouse 1)"
    )


def test_error_summary_caps_atses_at_three_with_more_tail():
    atses = ["a", "a", "a", "b", "b", "c", "d", "e"]
    errors = {f"{ats}:{i}": "Timeout: x" for i, ats in enumerate(atses)}
    assert observability.error_summary(errors) == "8 Timeout (a 3, b 2, c 1, +2 more)"


def test_error_summary_no_tail_at_exactly_three_atses():
    errors = {"a:1": "E: x", "b:1": "E: y", "c:1": "E: z"}
    assert observability.error_summary(errors) == "3 E (a 1, b 1, c 1)"


def test_error_summary_empty_and_colonless_message():
    assert observability.error_summary({}) == ""
    # a message with no ":" groups under the whole message
    assert observability.error_summary({"x:a": "boom"}) == "1 boom (x 1)"


def test_scrape_health_keeps_atses_and_loss_kinds_separate(tmp_path):
    health = observability.ScrapeHealth.from_reports(
        [
            observability.ShardReport.from_json(
                {
                    "boards_ok": ["workday:a", "icims:x"],
                    "errors": {"workday:b": "boom"},
                    "truncated": {"workday:a": "short"},
                    "observations": {
                        "workday:a": {
                            "listing_pages": 4,
                            "listing_page_losses": 1,
                            "detail_jobs": 10,
                            "detail_attempted": 3,
                            "detail_losses": 8,
                            "detail_breaker_skips": 7,
                            "detail_loss_causes": {"HTTP 500": 1, "breaker": 7},
                        },
                        "icims:x": {
                            "detail_jobs": 5,
                            "detail_attempted": 5,
                            "detail_losses": 2,
                            "detail_loss_causes": {"no JSON-LD on a 200": 2},
                        },
                    },
                }
            )
        ]
    )

    assert health.degraded
    assert health.verdict_line() == (
        # 2 unusable of 3 attempted is 66.67%, past `_CRITICAL_SHARE` — the verdict is graded on
        # the share now, not on `any(failed or partial)`, so this tiny fixture reads CRITICAL.
        "Fresh coverage: CRITICAL — 1 failed and 1 partial of 3 attempted Boards "
        "(66.67% unusable)"
    )
    lines = health.loss_lines()
    assert any("workday detail loss events: 8/10" in line for line in lines)
    assert any("icims detail loss events: 2/5" in line for line in lines)
    assert any("workday detail loss causes: breaker x7" in line for line in lines)

    path = tmp_path / "scrape_health.json"
    observability.write_scrape_health(path, health)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["degraded"] is True
    assert saved["losses"]["workday"]["detail_breaker_skips"] == 7


def test_scrape_health_does_not_call_missing_reports_healthy():
    health = observability.ScrapeHealth.from_reports([])

    assert (
        health.verdict_line()
        == "Fresh coverage: unavailable — no shard reports arrived"
    )
    assert health.to_dict()["available"] is False


def test_scrape_health_marks_a_missing_shard_report_degraded():
    health = observability.ScrapeHealth.from_reports(
        [observability.ShardReport.from_json({"boards_ok": ["workday:a"]})],
        expected_reports=2,
    )

    assert health.degraded
    assert "shard telemetry incomplete: 1/2 reports" in health.verdict_line()
    assert health.to_dict()["complete"] is False


def test_scrape_health_keeps_valid_fields_from_a_malformed_report(caplog):
    health = observability.ScrapeHealth.from_reports(
        [
            observability.ShardReport.from_json(
                {
                    "boards_ok": ["workday:a"],
                    "observations": {
                        "workday:a": {
                            "detail_jobs": "unknown",
                            "detail_losses": 2,
                            "detail_loss_causes": ["bad"],
                        }
                    },
                }
            ),
            observability.ShardReport.from_json({"observations": ["bad"]}),
        ],
        expected_reports=2,
    )

    assert health.losses["workday"]["detail_losses"] == 2
    assert health.malformed_report_count == 2
    assert health.degraded
    assert "2 malformed" in health.verdict_line()
    assert "2 shard report(s) carried malformed" in caplog.text


def test_every_loss_cause_is_reported_with_its_count():
    causes = Counter(
        {
            ("detail", "workday", f"HTTP {code}"): 10 - i
            for i, code in enumerate(range(500, 507))
        }
    )
    health = ScrapeHealth(
        {"workday": Counter({"successful": 100, "failed": 0, "partial": 0})},
        {"workday": Counter({"detail_missing": 7, "detail_attempted": 100})},
        causes,
        {key: {"workday:a"} for key in causes},
        15,
        15,
        0,
    )
    line = next(line for line in health.loss_lines() if "loss causes" in line)
    assert "more causes" not in line
    for code in range(500, 507):
        assert f"HTTP {code}" in line, f"HTTP {code} was dropped from the tail"
