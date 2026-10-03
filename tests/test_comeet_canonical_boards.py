"""The public normalization contract preserves unknowns and collapses proven aliases."""

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "comeet_canonical_boards",
    Path(__file__).parents[1] / "scripts/validate/comeet_canonical_boards.py",
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_only_a_read_canonical_board_can_replace_its_alias_verdicts():
    old = [
        {
            "tenant": "echosoftware/9a.006",
            "url": "old",
            "status": "dead",
            "jobs": "",
            "checked_at": "2026-10-03",
        },
        {
            "tenant": "echo/9a.006",
            "url": "new",
            "status": "live",
            "jobs": "2",
            "checked_at": "2026-10-02",
        },
    ]
    assert module.canonicalize(old, {}) == sorted(old, key=lambda row: row["tenant"])
    (row,) = module.canonicalize(
        old, {"9a.006": ("echo/9a.006", 2, "2026-10-03T15:00:00+00:00")}
    )
    assert row["tenant"] == "echo/9a.006"
    assert row["status"] == "live"
    assert row["jobs"] == "2"


def test_old_capture_cannot_replace_a_newer_verdict_and_keeps_its_capture_date():
    old = [
        {
            "tenant": "echo/9a.006",
            "url": "new",
            "status": "dead",
            "jobs": "",
            "checked_at": "2026-10-04",
        }
    ]
    assert (
        module.canonicalize(
            old, {"9a.006": ("echo/9a.006", 2, "2026-10-03T15:00:00+00:00")}
        )
        == old
    )
    (row,) = module.canonicalize(
        old, {"9a.006": ("echo/9a.006", 2, "2026-10-05T15:00:00+00:00")}
    )
    assert row["checked_at"] == "2026-10-05"
    assert row["status"] == "live"


def test_capture_cli_checkpoints_dated_proofs_but_preserves_newer_ledger(
    tmp_path, monkeypatch, capsys
):
    import json
    import sys

    ledger = tmp_path / "comeet.csv"
    pool = tmp_path / "pool.csv"
    ledger.write_text(
        "ats,tenant,url,status,jobs,checked_at\ncomeet,port/59.004,https://www.comeet.com/jobs/port/59.004,dead,,2026-10-03\n"
    )
    pool.write_text(
        "ats,tenant,url,source\ncomeet,port/59.004,https://www.comeet.com/jobs/port/59.004,seed\n"
    )
    before = ledger.read_text()
    captures = tmp_path / "captures"
    captures.mkdir()
    (captures / "port.json").write_text(
        json.dumps(
            {
                "url": "https://www.comeet.com/jobs/port/59.004",
                "status": 200,
                "headers": {"Date": "Fri, 02 Oct 2026 15:00:00 GMT"},
            }
        )
    )
    (captures / "port.body").write_text(
        (Path(__file__).parent / "fixtures/comeet_port.html").read_text()
    )
    progress = tmp_path / "progress.json"
    monkeypatch.setattr(module, "LEDGER", ledger)
    monkeypatch.setattr(module, "POOL", pool)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "normalize",
            "--captures",
            str(captures),
            "--progress",
            str(progress),
            "--apply",
        ],
    )
    module.main()
    assert ledger.read_text() == before
    assert json.loads(progress.read_text())["59.004"][2] == "2026-10-02T15:00:00+00:00"
    assert "canonical proofs checkpointed" in capsys.readouterr().out
