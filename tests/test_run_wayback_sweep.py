"""A failed target cannot masquerade as empty or stop later ATSes being attempted."""

import json
import sys
from contextlib import nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "discover"))

import run_wayback_sweep as runner


def test_sweep_reports_failures_and_resumes_only_unfinished_targets(
    monkeypatch, tmp_path
):
    report = tmp_path / "report.json"
    monkeypatch.setattr(
        sys, "argv", ["sweep", "--report", str(report), "--since", "20260917"]
    )
    monkeypatch.setattr(
        runner,
        "ATS_HOSTS",
        {
            "ashby": [("jobs.ashbyhq.com", "path")],
            "jazzhr": [("applytojob.com", "sub")],
        },
    )
    monkeypatch.setattr(runner, "KNOWN_HOST_ATS", {"phenom"})
    monkeypatch.setattr(runner, "known_hosts", lambda ats: ["careers.example.com"])
    monkeypatch.setattr(runner, "slug_sink", lambda ats: nullcontext(None))
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(runner.log, "setup", lambda: None)
    calls = []

    def sweep(ats, *args, **kwargs):
        calls.append(ats)
        return ats == "jazzhr"

    monkeypatch.setattr(runner, "sweep", sweep)
    monkeypatch.setattr(runner, "fetch", lambda url: "")
    assert runner.main() == 3
    results = json.loads(report.read_text())["targets"]
    assert results["ashby|jobs.ashbyhq.com"]["status"] == "incomplete"
    assert results["jazzhr|applytojob.com"]["status"] == "complete"
    assert results["phenom|careers.example.com"] == {
        "status": "complete",
        "mode": "known-host",
        "has_capture": False,
    }
    assert calls == ["ashby", "jazzhr"]
    monkeypatch.setattr(
        runner, "sweep", lambda ats, *args, **kwargs: calls.append(ats) or True
    )
    assert runner.main() == 0
    assert calls == ["ashby", "jazzhr", "ashby"]
