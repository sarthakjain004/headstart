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
    (row,) = module.canonicalize(old, {"9a.006": ("echo/9a.006", 2)})
    assert row["tenant"] == "echo/9a.006"
    assert row["status"] == "live"
    assert row["jobs"] == "2"
