"""Tests for retiring the Trends files ADR-0230's history replaced
(scripts/state/retire_superseded_trends_files.py).

What it checks of the history itself is the migration's own `verify`, tested in
test_migrate_trends_to_one_delta_history.py. These pin the order of its three refusals and what
the delete touches.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_STATE_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "state"


@pytest.fixture(scope="module")
def retire():
    # for its sibling import, the migration script
    sys.path.insert(0, str(_STATE_SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "retire_superseded_trends_files",
        _STATE_SCRIPTS / "retire_superseded_trends_files.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeHub:
    """The two HfApi calls `main` makes: a listing with sizes, and one commit."""

    def __init__(self, present: list[str]) -> None:
        self.present = present
        self.commits: list[list] = []

    def repo_info(self, repo, **kwargs):
        return SimpleNamespace(
            siblings=[
                SimpleNamespace(rfilename=p, size=1_000_000) for p in self.present
            ]
        )

    def create_commit(self, repo, operations, **kwargs):
        self.commits.append(operations)


def _run(retire, monkeypatch, hub, argv, refusals):
    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: hub)
    called = []
    for name, answer in refusals.items():
        monkeypatch.setattr(
            retire, name, lambda *a, _n=name, _v=answer: called.append(_n) or _v
        )
    monkeypatch.setattr(sys, "argv", ["retire", *argv])
    return retire.main(), called


def test_it_deletes_every_file_still_present_in_one_commit(retire, monkeypatch):
    hub = FakeHub([retire.RETIRED[0], retire.RETIRED[2], "data/state/other.csv"])
    everything_holds = dict.fromkeys(
        ("chain_refusal", "space_refusal", "history_refusal")
    )

    code, _ = _run(retire, monkeypatch, hub, ["--apply"], everything_holds)

    assert code == 0
    [operations] = hub.commits
    assert [op.path_in_repo for op in operations] == [
        retire.RETIRED[0],
        retire.RETIRED[2],
    ]


def test_a_dry_run_deletes_nothing(retire, monkeypatch):
    hub = FakeHub(list(retire.RETIRED))
    everything_holds = dict.fromkeys(
        ("chain_refusal", "space_refusal", "history_refusal")
    )

    code, _ = _run(retire, monkeypatch, hub, [], everything_holds)

    assert code == 0
    assert hub.commits == []


def test_a_running_chain_refuses_before_anything_is_fetched(retire, monkeypatch):
    hub = FakeHub(list(retire.RETIRED))
    refusals = {
        "chain_refusal": "pipeline.yml is 'active'",
        "space_refusal": None,
        "history_refusal": None,
    }

    code, called = _run(retire, monkeypatch, hub, ["--apply"], refusals)

    assert code == 1
    assert called == ["chain_refusal"]
    assert hub.commits == []


def test_a_space_that_cannot_read_the_archive_refuses(retire, monkeypatch, tmp_path):
    import huggingface_hub

    reader = tmp_path / "trend_history.py"
    reader.write_text('AGGREGATE = "role_trends.parquet"\n')
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", lambda *a, **k: reader)

    assert "deploy step 6 first" in retire.space_refusal()

    reader.write_text(f'ARCHIVE = "{retire.ARCHIVE}"\n')
    assert retire.space_refusal() is None


def test_a_history_missing_from_the_dataset_refuses(retire, monkeypatch, tmp_path):
    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda *a, **k: None)

    refusal = retire.history_refusal(tmp_path)

    assert refusal.startswith("not on ")
    assert retire.ARCHIVE in refusal
