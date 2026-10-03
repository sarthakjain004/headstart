"""Scratch cleanup is confined to disposable runners and exact unused SDK roots."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "prepare_runner",
    Path(__file__).parents[1] / "scripts/eval/prepare_restatement_runner.py",
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def hosted(monkeypatch):
    for name, value in {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Linux",
    }.items():
        monkeypatch.setenv(name, value)


def test_local_and_self_hosted_runners_cannot_clean_sdks(monkeypatch):
    hosted(monkeypatch)
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "self-hosted")
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *a, **k: pytest.fail("cleanup")
    )
    with pytest.raises(RuntimeError, match="disposable"):
        runner.main()


def test_enough_space_never_removes_any_sdk(monkeypatch):
    hosted(monkeypatch)
    monkeypatch.setattr(runner, "available", lambda: runner.MIN_FREE)
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *a, **k: pytest.fail("cleanup")
    )
    runner.main()


def test_cleanup_rechecks_space_after_each_exact_target(monkeypatch):
    hosted(monkeypatch)
    space = iter([1, runner.MIN_FREE])
    monkeypatch.setattr(runner, "available", lambda: next(space))
    monkeypatch.setattr(Path, "is_dir", lambda _: True)
    monkeypatch.setattr(Path, "is_symlink", lambda _: False)
    commands = []
    monkeypatch.setattr(
        runner.subprocess, "run", lambda command, **k: commands.append(command)
    )
    runner.main()
    assert commands == [
        ["du", "-sh", "/usr/local/lib/android/sdk"],
        ["sudo", "rm", "-rf", "--", "/usr/local/lib/android/sdk"],
    ]


def test_insufficient_space_fails_before_fetch(monkeypatch):
    hosted(monkeypatch)
    monkeypatch.setattr(runner, "available", lambda: 1)
    monkeypatch.setattr(Path, "is_dir", lambda _: False)
    with pytest.raises(RuntimeError, match="40 GiB"):
        runner.main()
