"""Independent decoding of model inputs must not copy saved family labels."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "reference_validation",
    Path(__file__).parents[1] / "scripts/eval/validate_trend_reference.py",
)
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


def test_candidate_ignores_a_wrong_saved_family():
    row = {
        "title": "Backend Engineer",
        "employment_type": None,
        "min_years": 3,
        "reference_board": "lever:acme",
        "reference_family": "qa-test",
        "title_logits": [10, -10],
        "row_logits": [0, 0],
    }
    assert validation.decide(
        {"lever:acme:1": row},
        {"families": ["software-engineering", "non-tech"], "cutoff": 0.6},
        {"lever:acme": "lever:acme"},
    ) == {"lever:acme:1": ("lever:acme", "software-engineering", "mid")}


def test_missing_title_encoding_does_not_apply_developer_fallback():
    row = {
        "title": "Software Engineer",
        "employment_type": None,
        "min_years": 3,
        "reference_board": "lever:acme",
        "title_logits": None,
        "row_logits": [0, 0],
    }
    assert (
        validation.decide(
            {"lever:acme:1": row},
            {"families": ["software-engineering", "non-tech"], "cutoff": 0.6},
            {"lever:acme": "lever:acme"},
        )["lever:acme:1"][1]
        == "unclassified-tech"
    )
