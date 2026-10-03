"""Contract fixtures exercise serving enforcement, never certify a production replay."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from headstart.ingest import restate_publish
from headstart.trends import restated_history as artifact
from headstart.trends import trend_history

FIRST = "2026-10-01T00:00:00+00:00"
LAST = "2026-10-02T00:00:00+00:00"
METHODOLOGY = trend_history.Methodology("map", 3, 1, 1, 1)


@pytest.fixture
def candidate(tmp_path):
    directory = tmp_path / "candidate"
    directory.mkdir()
    levels = {("greenhouse:acme", "stock", "software-engineering", "mid"): 5}
    trend_history.record_tick(directory, FIRST, levels, {}, METHODOLOGY)
    levels = {k: 7 for k in levels}
    trend_history.record_tick(directory, LAST, levels, {}, METHODOLOGY)
    (directory / "company_directory.json").write_text(
        json.dumps(
            {
                "companies": [
                    {"name": "Acme", "boards": ["greenhouse:acme"], "openings": 999999}
                ]
            }
        )
    )
    (directory / "config").mkdir()
    # Deliberately different from the live config: the generation's taxonomy must win.
    (directory / "config/role_families.json").write_text(
        json.dumps(
            {
                "families": [
                    {"name": "software-engineering", "label": "Pinned Engineering"}
                ]
            }
        )
    )
    (directory / "config/role_watchlist.json").write_text('{"roles": []}')
    metadata = {
        "schema_version": 1,
        "rules_fingerprint": "a" * 64,
        "rules_code_sha": "b" * 40,
        "input_revision": "c" * 40,
        "first_covered_tick": FIRST,
        "last_covered_tick": LAST,
        "inputs": [
            {"path": "data/facts/job_facts/one.parquet", "size": 10, "sha256": "d" * 64}
        ],
    }
    (directory / "replay.json").write_bytes(artifact.encoded(metadata))
    files = [
        artifact.file_entry(p, p.relative_to(directory).as_posix())
        for p in sorted(directory.rglob("*"))
        if p.is_file() and artifact.allowed(p.relative_to(directory).as_posix())
    ]
    metadata["files"] = files
    (directory / "replay.json").write_bytes(artifact.encoded(metadata))
    report = {
        **metadata,
        "complete": True,
        "pass": True,
        "files": files,
        "verifier": {"name": "independent-fixture-only", "code_sha": "e" * 40},
        "quality": {"supported_metrics": ["stock"]},
        "limitations": ["Fixture only; no production validation."],
        "checks": [{"name": "fixture-membership", "pass": True}],
    }
    (directory / "validation.json").write_bytes(artifact.encoded(report))
    return directory


@pytest.fixture
def packaged(candidate, tmp_path):
    root = tmp_path / "published"
    directory, pointer = restate_publish.package(candidate, root)
    return root, directory, pointer


def legacy(tmp_path):
    path = tmp_path / "legacy"
    trend_history.record_tick(
        path,
        "2026-09-01T00:00:00+00:00",
        {("lever:new-company", "stock", "software-engineering", "mid"): 99},
        {},
        METHODOLOGY,
    )
    return trend_history.TrendHistory.load(path, Path("config"))


def test_select_pinned_generation_and_separate_legacy(packaged, tmp_path):
    root, _, _ = packaged
    old = legacy(tmp_path)
    selection = artifact.load(root, Path("config"), old)
    assert selection.choose() is selection.restated
    assert selection.choose("legacy") is old
    assert selection.restated.ticks == (FIRST, LAST)
    assert old.ticks == ("2026-09-01T00:00:00+00:00",)
    answer = selection.answer(selection.restated, trend_history.TrendQuestion())
    assert answer["history"]["rules_status"] == "verified_at_code_sha"
    assert answer["history"]["spliced"] is False
    assert answer["totals"][-1] == 7
    assert selection.restated.suggest_companies("Acme", 8)[0]["openings"] == 7
    assert answer["turnover_since"] is None
    assert (
        selection.restated.unnetted_answer(
            trend_history.TrendQuestion(family="software-engineering")
        )["family_label"]
        == "Pinned Engineering"
    )
    for question in (
        trend_history.TrendQuestion(metric="new"),
        trend_history.TrendQuestion(split="roles"),
    ):
        with pytest.raises(trend_history.TrendsUnavailable, match="certify"):
            selection.answer(selection.restated, question)


@pytest.mark.parametrize(
    "key,value",
    [
        ("pass", False),
        ("pass", 1),
        ("complete", False),
        ("input_revision", "f" * 40),
        ("last_covered_tick", FIRST),
        ("rules_fingerprint", "0" * 64),
        ("first_covered_tick", LAST),
        ("checks", []),
        ("verifier", {}),
        ("limitations", None),
        ("quality", {"supported_metrics": ["new"]}),
    ],
)
def test_gate_refuses_partial_or_other_report(candidate, tmp_path, key, value):
    path = candidate / "validation.json"
    report = artifact.read_json(path)
    report[key] = value
    path.write_bytes(artifact.encoded(report))
    with pytest.raises(ValueError):
        restate_publish.package(candidate, tmp_path / "out")


def test_exact_report_inventory_required(candidate, tmp_path):
    path = candidate / "validation.json"
    report = artifact.read_json(path)
    report["files"][0]["sha256"] = "0" * 64
    path.write_bytes(artifact.encoded(report))
    with pytest.raises(ValueError, match="inventory"):
        restate_publish.package(candidate, tmp_path / "out")


def test_passed_is_not_the_approved_pass_field(candidate, tmp_path):
    path = candidate / "validation.json"
    report = artifact.read_json(path)
    del report["pass"]
    report["passed"] = True
    path.write_bytes(artifact.encoded(report))
    with pytest.raises(ValueError, match="complete/pass"):
        restate_publish.package(candidate, tmp_path / "out")


@pytest.mark.parametrize("damage", ["missing", "hash", "extra", "report", "pointer"])
def test_invalid_generation_falls_back(packaged, tmp_path, damage):
    root, directory, _ = packaged
    tick = next((directory / trend_history.DELTAS).glob("*.parquet"))
    if damage == "missing":
        tick.unlink()
    elif damage == "hash":
        tick.write_bytes(b"corrupt")
    elif damage == "extra":
        (directory / "facts.parquet").write_bytes(b"private")
    elif damage == "report":
        (directory / "validation.json").write_bytes(b"{}")
    else:
        (root / artifact.CURRENT).write_bytes(b"{}")
    old = legacy(tmp_path)
    selection = artifact.load(root, Path("config"), old)
    assert selection.choose() is old
    assert selection.reason
    with pytest.raises(trend_history.TrendsUnavailable):
        selection.choose("restated")


@pytest.mark.parametrize(
    "name",
    [
        "../secret",
        "/secret",
        "data/facts/secret",
        "placements/a.parquet",
        "config/../secret",
    ],
)
def test_allowlist_refuses_raw_payloads(name):
    with pytest.raises(ValueError):
        artifact.inventory(
            [{"path": name, "size": 1, "sha256": "a" * 64}], serving=True
        )


def test_size_bound(candidate, tmp_path, monkeypatch):
    monkeypatch.setattr(artifact, "MAX_BYTES", 20)
    with pytest.raises(ValueError, match="size limit"):
        restate_publish.package(candidate, tmp_path / "out")


def test_company_directory_coverage_is_required(candidate, tmp_path):
    (candidate / "company_directory.json").write_text('{"companies": []}')
    with pytest.raises(ValueError):
        artifact.check_history(candidate, artifact.read_json(candidate / "replay.json"))


def test_remote_pull_pins_every_download_and_excludes_raw_inputs(
    packaged, tmp_path, monkeypatch
):
    import huggingface_hub

    root, _, _ = packaged
    calls = []

    def download(repo, name, **kwargs):
        calls.append((name, kwargs["revision"]))
        target = Path(kwargs["local_dir"]) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
        return str(target)

    monkeypatch.setattr(
        huggingface_hub,
        "HfApi",
        lambda **_: SimpleNamespace(
            repo_info=lambda *a, **k: SimpleNamespace(sha="pinned")
        ),
    )
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", download)
    artifact.pull("repo", tmp_path / "space")
    assert {revision for _, revision in calls} == {"pinned"}
    assert all(name.startswith(artifact.ROOT) for name, _ in calls)
    assert not any(
        any(
            part in name
            for part in ("placements/", "data/facts/", "descriptions/", "embeddings/")
        )
        for name, _ in calls
    )
    assert (tmp_path / "space" / artifact.CURRENT).exists()
