"""Followed/hidden Boards: the record's invariants, and the clause they compile to (ADR-0171)."""

from __future__ import annotations

import pytest

from headstart.alerts.store import MAX_COMPANIES, CompanyPrefs
from headstart.search import board_clause


def test_following_a_hidden_board_unhides_it() -> None:
    """The lists must stay disjoint, or a filter both requires and excludes the same rows."""
    prefs = CompanyPrefs.blank("a" * 16).with_board("greenhouse:acme", "hide")
    assert prefs.hidden == ("greenhouse:acme",)
    prefs = prefs.with_board("greenhouse:acme", "follow")
    assert prefs.followed == ("greenhouse:acme",)
    assert prefs.hidden == ()


def test_clear_removes_from_both() -> None:
    prefs = CompanyPrefs.blank("a" * 16).with_board("lever:x", "follow")
    assert prefs.with_board("lever:x", "clear").followed == ()


def test_following_the_same_board_twice_does_not_duplicate_it() -> None:
    prefs = CompanyPrefs.blank("a" * 16)
    for _ in range(3):
        prefs = prefs.with_board("ashby:acme", "follow")
    assert prefs.followed == ("ashby:acme",)


def test_the_cap_bounds_the_list_on_write() -> None:
    prefs = CompanyPrefs.blank("a" * 16)
    for n in range(MAX_COMPANIES + 10):
        prefs = prefs.with_board(f"greenhouse:c{n}", "follow")
    assert len(prefs.followed) == MAX_COMPANIES
    assert prefs.followed[-1] == f"greenhouse:c{MAX_COMPANIES + 9}", "newest kept"


def test_a_record_is_bounded_on_read_too() -> None:
    """Every entry becomes a LIKE term, so a hand-edited record must not widen the clause."""
    raw = {
        "account": "a" * 16,
        "followed": [f"greenhouse:c{n}" for n in range(MAX_COMPANIES + 50)],
        "hidden": [],
    }
    assert len(CompanyPrefs.from_dict(raw).followed) == MAX_COMPANIES


def test_a_record_that_lists_a_board_twice_is_read_disjoint() -> None:
    raw = {
        "account": "a" * 16,
        "followed": ["lever:x"],
        "hidden": ["lever:x", "lever:y"],
    }
    prefs = CompanyPrefs.from_dict(raw)
    assert prefs.followed == ("lever:x",)
    assert prefs.hidden == ("lever:y",), "followed wins; the pair stays disjoint"


def test_no_boards_compiles_to_no_clause() -> None:
    """None, not an empty string: a caller must be able to tell 'nothing to say' from a clause."""
    assert board_clause([], exclude=True) is None
    assert board_clause([""], exclude=False) is None


def test_a_board_matches_its_own_jobs_only() -> None:
    """The trailing colon is load-bearing — without it `acme` also matches `acmecorp`."""
    clause = board_clause(["greenhouse:acme"], exclude=False)
    assert clause == "(lower(id) LIKE 'greenhouse:acme:%')"


def test_like_wildcards_in_a_board_key_are_escaped() -> None:
    """Board keys legitimately contain `_`, which is a LIKE wildcard matching any character."""
    clause = board_clause(["workday:ngc/Northrop_Grumman_External_Site"], exclude=True)
    assert r"northrop\_grumman\_external\_site:%" in clause
    assert clause.startswith("NOT (")


def test_matching_is_case_insensitive_so_one_company_is_not_half_hidden() -> None:
    """The index holds 335 Board-key groups differing only in casing — one company, two rows."""
    assert board_clause(["Workday:Micron/External"], exclude=True) == board_clause(
        ["workday:micron/external"], exclude=True
    )


def test_a_long_board_key_is_not_truncated() -> None:
    """`_like` caps terms at 60 chars; a Taleo key is a whole URL, and a truncated prefix
    would hide every Board sharing that prefix rather than the one chosen."""
    board = "taleo_be:https://phh.tbe.taleo.net/phh01/ats/careers/v2/searchResults?org=EXAMPLECO"
    clause = board_clause([board], exclude=True)
    assert "examplecc" not in clause
    assert board.lower() in clause.replace("\\", "")


@pytest.mark.parametrize("exclude", [True, False])
def test_boards_are_deduplicated_and_ordered(exclude: bool) -> None:
    once = board_clause(["lever:b", "lever:a", "lever:b"], exclude=exclude)
    assert once.count("lower(id) LIKE") == 2
    assert once.index("lever:a") < once.index("lever:b"), (
        "sorted, so the clause is stable"
    )
