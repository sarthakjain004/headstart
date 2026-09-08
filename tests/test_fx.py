"""The salary bracket's rate table — `headstart.fx` (ADR-0117).

The regression this file exists for: the module computed its candidate paths at import with a
hardcoded `parents[2]`, which is fine in the repo (`src/headstart/fx.py`) and raises `IndexError`
in the Space, where the same file is `/app/fx.py` and has only two ancestors. It took the live
Space down with a `RUNTIME_ERROR` on the first deploy after ADR-0117, because the failure happened
at *import* — before the guarded read that was supposed to make a missing table survivable.
"""

import json
import logging
from pathlib import Path

import pytest

from headstart import fx

REPO = Path(__file__).resolve().parents[1]


def test_the_spaces_two_ancestor_path_does_not_raise(monkeypatch):
    """`/app/fx.py` has exactly two ancestors, and the old code indexed `parents[2]`.

    Patched rather than staged on disk: reproducing it for real needs a file one directory below
    root, which is not writable here — and a first attempt at this test used `/tmp/x/app/fx.py`,
    which has *three* ancestors, so it passed against the broken code. The shape is the point, so
    the shape is what is asserted.
    """
    monkeypatch.setattr(fx, "__file__", "/app/fx.py")
    assert len(Path("/app/fx.py").resolve().parents) == 2, (
        "premise: this is the Space's shape"
    )
    candidates = fx._candidates()  # must not raise IndexError
    assert Path("/app/fx_rates.json") in candidates
    assert candidates[0] == Path("/app/fx_rates.json"), (
        "the flat copy is looked for first"
    )


def test_the_table_loads_from_a_two_ancestor_path(monkeypatch, tmp_path):
    """…and having not raised, it still finds the table beside the module."""
    monkeypatch.setattr(fx, "__file__", "/app/fx.py")
    monkeypatch.setattr(fx, "_CACHE", False)
    beside = tmp_path / "fx_rates.json"
    beside.write_text(
        json.dumps(
            {"as_of": "2024-06-01", "base": "USD", "rates": {"USD": 1.0, "INR": 83.0}}
        )
    )
    monkeypatch.setattr(fx, "_candidates", lambda: (beside,))
    t = fx.table()
    assert t and t["as_of"] == "2024-06-01"
    assert fx.convert(100.0, "USD", "INR", t["rates"]) == 8300.0


def test_the_flat_copy_beside_the_module_wins_over_the_repo_config():
    """The Space has no `config/`; the table sits beside the module. Nearest first."""
    assert fx._candidates()[0].name == "fx_rates.json"
    assert fx._candidates()[0].parent.name == "headstart"


def test_an_unreadable_table_is_none_rather_than_an_exception(tmp_path):
    """`None` is a supported state: the bracket falls back to one currency (ADR-0117)."""
    bad = tmp_path / "fx_rates.json"
    bad.write_text("{ not json")
    assert fx.table(path=bad) is None
    missing = tmp_path / "nope.json"
    assert fx.table(path=missing) is None


@pytest.mark.parametrize(
    "payload",
    [
        {"base": "USD", "as_of": "2024-06-01"},  # no rates
        {"rates": {"USD": 1.0}, "as_of": "2024-06-01"},  # no base
        {"rates": {"USD": 1.0}, "base": "USD"},  # no date
        {
            "rates": {"USD": 1.0},
            "base": "EUR",
            "as_of": "2024-06-01",
        },  # base has no rate
    ],
)
def test_an_incomplete_table_is_refused_whole(tmp_path, payload):
    """A rate without its date is the defect the file exists to avoid — so a table missing any
    of rates/base/as_of is refused rather than half-used."""
    f = tmp_path / "fx_rates.json"
    f.write_text(json.dumps(payload))
    assert fx.table(path=f) is None


def test_a_nonpositive_rate_is_dropped_without_disabling_the_table(tmp_path):
    """One bad row must not take the feature down; it would divide by zero or invert a bound."""
    f = tmp_path / "fx_rates.json"
    f.write_text(
        json.dumps(
            {"as_of": "x", "base": "USD", "rates": {"USD": 1.0, "INR": 0, "EUR": -2}}
        )
    )
    t = fx.table(path=f)
    assert t is not None
    assert set(t["rates"]) == {"USD"}


def test_convert_returns_none_when_either_side_has_no_rate():
    rates = {"USD": 1.0, "INR": 83.0}
    assert fx.convert(100.0, "USD", "XTS", rates) is None
    assert fx.convert(100.0, "XTS", "INR", rates) is None
    # base-independent: the units-per-base factors cancel
    assert fx.convert(8300.0, "INR", "USD", rates) == 100.0


def test_the_swallowed_read_leaves_a_record_naming_its_consequence(tmp_path, caplog):
    """`None` is a supported state; being *silent* about it is not.

    The result is cached for the life of the process, so a swallowed read is not one failed
    lookup — every salary bracket after it compares within a single currency and drops every Job
    priced in another, with nothing in the UI to say so. On the live Space that made
    cross-currency conversion go permanently dark with zero records anywhere.
    """
    bad = tmp_path / "fx_rates.json"
    bad.write_text("{ not json")
    with caplog.at_level(logging.WARNING, logger="headstart.fx"):
        assert fx.table(path=bad) is None

    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    message = caplog.records[0].getMessage()
    assert "JSONDecodeError" in message  # which failure, not merely that one did
    assert "falls back to one currency" in message  # and what it costs
