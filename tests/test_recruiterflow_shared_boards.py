"""A Recruiterflow alias needs matching database identity and entire posting set."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "recruiterflow_shared_boards",
    ROOT / "scripts/validate/recruiterflow_shared_boards.py",
)
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def test_readable_slug_wins_identical_database_alias():
    assert validator.choose_aliases(
        {"db_123": "db_123", "brand": "db_123"},
        {"db_123": {"1", "2"}, "brand": {"1", "2"}},
    ) == {"db_123": "brand"}


def test_partial_overlap_empty_sets_and_different_databases_are_not_aliases():
    assert (
        validator.choose_aliases(
            {"a": "db_123", "b": "db_123", "c": "db_456", "empty": "db_123"},
            {"a": {"1", "2"}, "b": {"1"}, "c": {"1", "2"}, "empty": set()},
        )
        == {}
    )


def test_explicit_preference_is_deterministic():
    assert validator.choose_aliases(
        {"a": "db_123", "b": "db_123"}, {"a": {"1"}, "b": {"1"}}, prefer={"b"}
    ) == {"a": "b"}


def test_identity_requires_specific_public_database_evidence():
    assert validator.database_of("random text db_123") is None
    assert validator.database_of("/careers-page/prod/db_123/logo.png") == "db_123"
    assert (
        validator.database_of("/careers-page/prod/db_123/x /careers-page/prod/db_456/y")
        is None
    )
    assert (
        validator.database_of(
            "", {"google_for_jobs_fragment": {"identifier": {"value": "db_123__44"}}}
        )
        == "db_123"
    )


def test_failed_refresh_preserves_the_previous_alias_ledger(monkeypatch, tmp_path):
    directory = tmp_path / "data/validate/liveness"
    directory.mkdir(parents=True)
    (directory / "recruiterflow.csv").write_text(
        "ats,tenant,url,status,jobs,checked_at\n"
        "recruiterflow,brand,https://recruiterflow.com/brand/jobs,live,1,2026-10-03\n"
    )
    alias_path = tmp_path / "data/validate/aliases/recruiterflow.csv"
    alias_path.parent.mkdir()
    previous = (
        "ats,duplicate,canonical,signal,resolved_to,checked_at\n"
        "recruiterflow,db_123,brand,shared-reqs,db_123,2026-10-03\n"
    )
    alias_path.write_text(previous)
    monkeypatch.setattr(validator, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["recruiterflow_shared_boards.py", "--apply"])

    def failed(_slug):
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(validator, "read_board", failed)
    with pytest.raises(RuntimeError, match="upstream unavailable"):
        validator.main()
    assert alias_path.read_text() == previous


@pytest.mark.parametrize("canonical_status", ["dead", "live", "unknown"])
def test_refresh_unburies_a_survivor_when_its_canonical_is_dead_or_empty(
    monkeypatch, tmp_path, canonical_status
):
    directory = tmp_path / "data/validate/liveness"
    directory.mkdir(parents=True)
    (directory / "recruiterflow.csv").write_text(
        "ats,tenant,url,status,jobs,checked_at\n"
        f"recruiterflow,brand,https://recruiterflow.com/brand/jobs,{canonical_status},0,2026-10-03\n"
        "recruiterflow,db_123,https://recruiterflow.com/db_123/jobs,live,1,2026-10-03\n"
    )
    alias_path = tmp_path / "data/validate/aliases/recruiterflow.csv"
    alias_path.parent.mkdir()
    previous = "ats,duplicate,canonical,signal,resolved_to,checked_at\nrecruiterflow,db_123,brand,shared-reqs,db_123,2026-10-03\n"
    alias_path.write_text(previous)
    monkeypatch.setattr(validator, "ROOT", tmp_path)
    monkeypatch.setattr(validator, "CACHE", tmp_path / "experiment/cache")
    monkeypatch.setattr(sys, "argv", ["recruiterflow_shared_boards.py", "--apply"])
    monkeypatch.setattr(
        validator,
        "read_board",
        lambda slug, **_kwargs: (None, set()) if slug == "brand" else ("db_123", {"1"}),
    )
    if canonical_status == "unknown":
        with pytest.raises(SystemExit, match="previous alias"):
            validator.main()
        assert alias_path.read_text() == previous
    else:
        validator.main()
        assert validator.alias_ledger.load(alias_path) == {}
