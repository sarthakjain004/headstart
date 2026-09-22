import runpy
from pathlib import Path


def test_content_drift_separates_whitespace_missing_and_failed_descriptions():
    compare = runpy.run_path(
        str(
            Path(__file__).resolve().parents[1]
            / "scripts/eval/measure_content_drift.py"
        )
    )["compare"]
    rows = compare(
        {"a": "same text", "b": "old", "c": "held", "d": "held"},
        {"a": "same  text\n", "b": "new", "d": None},
    )
    assert {row["id"]: row["status"] for row in rows} == {
        "a": "unchanged",
        "b": "different",
        "c": "not_returned",
        "d": "no_current_description",
    }
