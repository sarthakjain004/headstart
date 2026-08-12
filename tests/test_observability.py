"""Tests for headstart.ingest.observability — the observability seam.

Its three jobs are to be *safe*: a summary or a shard report is telemetry, and none of it may
break a run that is otherwise fine (or already dying on its time budget). So the failure paths
matter as much as the happy ones.
"""

from __future__ import annotations


from headstart.ingest import observability


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
        tmp_path / "shard-0", shard="0", undone=12, killed_by_budget=True
    )
    observability.write_shard(
        tmp_path / "shard-1", shard="1", undone=0, killed_by_budget=False
    )

    reports = observability.read_shards(tmp_path)
    assert {r["shard"] for r in reports} == {"0", "1"}
    assert sum(r["undone"] for r in reports) == 12


def test_read_shards_skips_a_corrupt_report_rather_than_dying(tmp_path):
    """The join's real work is unioning job data; a shard's broken telemetry must not stop it."""
    observability.write_shard(tmp_path / "shard-0", shard="0")
    bad = tmp_path / "shard-1"
    bad.mkdir()
    (bad / "_shard_report.json").write_text("{not json", encoding="utf-8")

    assert [r["shard"] for r in observability.read_shards(tmp_path)] == ["0"]


def test_percentiles_expose_the_straggler_a_mean_would_hide():
    """1,330 fast boards beside one 2,237s monster: the mean says 3s, p99/max say otherwise."""
    values = [1.0] * 1329 + [2237.0]
    spread = observability.percentiles(values)

    assert spread["p50"] == 1.0
    assert spread["max"] == 2237.0
    assert observability.percentiles([]) == {}


def test_context_is_silent_off_ci(monkeypatch, caplog):
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    observability.context("scrape")
    assert not caplog.records
