"""Suggesting companies for a typed name: loose enough to find, never picking for the user."""

from __future__ import annotations

import pytest

from headstart.company_match import Candidate, normalize, suggest, tier


def _c(name: str, openings: int = 1, key: str | None = None) -> Candidate:
    return Candidate(
        key=key or f"greenhouse:{name.lower()}",
        name=name,
        words=tuple(normalize(name)),
        openings=openings,
    )


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("Burns & McDonnell", ["burns", "and", "mcdonnell"]),
        ("Nestlé S.A.", ["nestle"]),
        ("Acme, Inc.", ["acme"]),
        # nothing but legal form still matches
        ("Private Limited", ["private", "limited"]),
    ],
)
def test_normalize(text: str, words: list[str]) -> None:
    assert normalize(text) == words


@pytest.mark.parametrize(
    ("query", "name", "expected"),
    [
        ("lockheed martin", "Lockheed Martin", 0),
        ("lockh", "Lockheed Martin", 1),
        ("martin", "Lockheed Martin", 2),  # a later word
        ("lokheed", "Lockheed Martin", 3),  # one typo in a long word
        ("amazn", "Amazon", 3),
        ("googel", "Google", 3),  # two neighbouring letters swapped is one typo
        ("gloogle", "Google", 3),  # an extra letter
        ("gogle", "Goggles Co", None),  # two edits from "goggl"
        ("hpe", "Hp", None),  # no typo forgiven in a short word
        ("nvidia", "Micron", None),
    ],
)
def test_tier(query: str, name: str, expected: int | None) -> None:
    assert tier(normalize(query), tuple(normalize(name))) == expected


def test_a_better_tier_beats_more_openings() -> None:
    """An exact name wins over a bigger company that merely starts with the query."""
    got = suggest("amazon", [_c("Amazon Robotics", 5000), _c("Amazon", 1)], limit=5)
    assert [c.name for c in got] == ["Amazon", "Amazon Robotics"]


def test_within_a_tier_more_openings_come_first() -> None:
    """Two "Amazon"s: the real one's 9,214 openings put it above a one-posting collision."""
    got = suggest(
        "amazon",
        [
            _c("Amazon", 1, key="trakstar:amazon"),
            _c("Amazon", 9214, key="amazon:www.amazon.jobs"),
        ],
        limit=5,
    )
    assert [c.key for c in got] == ["amazon:www.amazon.jobs", "trakstar:amazon"]


def test_limit_and_no_match() -> None:
    companies = [_c(f"Acme {i}") for i in range(10)]
    assert len(suggest("acme", companies, limit=3)) == 3
    assert suggest("zzz", companies, limit=3) == []
    assert suggest("  ", companies, limit=3) == []
