"""Suggesting companies for a typed name: loose enough to find, never picking for the user."""

from __future__ import annotations

import pytest

from headstart.company_match import Candidate, normalize, suggest, tier


def _company(name: str, openings: int = 1, key: str | None = None) -> Candidate:
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
        ("Acme Pvt. Ltd.", ["acme"]),
        # only a trailing legal form: a leading one is part of the name
        ("SA Power Networks", ["sa", "power", "networks"]),
        ("Co-op Group", ["co", "op", "group"]),
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
        ("micro soft", "Microsoft", 4),  # a word the company writes whole, split
        ("jp morgan", "JPMorgan Chase", 4),
        (
            "jpmorgan",
            "JP Morgan Chase",
            4,
        ),  # and a word written whole that the company splits
        ("h p", "HP Inc", None),  # too few letters to ignore spaces over
        ("cisco", "Discovery", None),  # no typo in the first letter
    ],
)
def test_tier(query: str, name: str, expected: int | None) -> None:
    assert tier(normalize(query), tuple(normalize(name))) == expected


def test_a_better_tier_beats_more_openings() -> None:
    """An exact name wins over a bigger company that merely starts with the query."""
    got = suggest(
        "amazon", [_company("Amazon Robotics", 5000), _company("Amazon", 1)], limit=5
    )
    assert [c.name for c in got] == ["Amazon", "Amazon Robotics"]


def test_within_a_tier_more_openings_come_first() -> None:
    got = suggest(
        "amazon",
        [_company("Amazon Pay", 3), _company("Amazon Web Services", 900)],
        limit=5,
    )
    assert [c.name for c in got] == ["Amazon Web Services", "Amazon Pay"]


def test_one_suggestion_per_name_the_one_with_most_openings() -> None:
    """Two "Amazon"s: the real one's 9,214 openings beat a one-posting collision, and a
    mirror spelled with its legal form ("NVIDIA Corporation") is the same name."""
    got = suggest(
        "amazon",
        [
            _company("Amazon", 1, key="trakstar:amazon"),
            _company("Amazon", 9214, key="amazon:www.amazon.jobs"),
        ],
        limit=5,
    )
    assert [c.key for c in got] == ["amazon:www.amazon.jobs"]
    got = suggest(
        "nvidia",
        [
            _company("Nvidia", 2043, key="workday:nvidia/NVIDIAExternalCareerSite"),
            _company("NVIDIA Corporation", 2048, key="eightfold:jobs.nvidia.com"),
        ],
        limit=5,
    )
    assert [c.key for c in got] == ["eightfold:jobs.nvidia.com"]


def test_a_collapsed_twin_does_not_take_a_slot() -> None:
    companies = [_company("Acme", 5, key=f"greenhouse:acme{i}") for i in range(3)]
    companies.append(_company("Acme Labs", 1))
    assert [c.name for c in suggest("acme", companies, limit=2)] == [
        "Acme",
        "Acme Labs",
    ]


def test_limit_and_no_match() -> None:
    companies = [_company(f"Acme {i}") for i in range(10)]
    assert len(suggest("acme", companies, limit=3)) == 3
    assert suggest("zzz", companies, limit=3) == []
    assert suggest("  ", companies, limit=3) == []


def test_a_test_tenant_with_no_openings_is_not_offered() -> None:
    companies = [
        _company("Jpmc", 1716, key="oracle:jpmc"),
        _company("Jpmc Dev1", 0, key="oracle:jpmc-dev1"),
        _company("Nvidia Sandbox2", 0),
        _company("Dev Partners", 12),  # an employer: the word alone is not enough
        _company(
            "Acme Studio", 0
        ),  # nor are no openings alone: a closed employer stays
    ]
    assert [c.name for c in suggest("jpmc", companies, 5)] == ["Jpmc"]
    assert suggest("nvidia", companies, 5) == []
    assert [c.name for c in suggest("dev", companies, 5)] == ["Dev Partners"]
    assert [c.name for c in suggest("acme", companies, 5)] == ["Acme Studio"]


def test_a_name_that_differs_by_a_trailing_word_is_another_employer() -> None:
    """Affinity and Affinity Group are two employers, so both are offered."""
    companies = [_company("Affinity", 40), _company("Affinity Group", 12)]
    assert [c.name for c in suggest("affinity", companies, 5)] == [
        "Affinity",
        "Affinity Group",
    ]


def test_a_query_alias_finds_the_company_by_the_name_people_use() -> None:
    companies = [
        Candidate(key="amazon:jobs", name="Amazon", words=("amazon",), openings=9000),
        Candidate(key="oracle:jpmc", name="Jpmc", words=("jpmc",), openings=1718),
        Candidate(key="gh:awsome", name="Awsome", words=("awsome",), openings=3),
    ]
    assert [c.key for c in suggest("aws", companies, 5)] == ["amazon:jobs", "gh:awsome"]
    assert [c.key for c in suggest("JP Morgan", companies, 5)] == ["oracle:jpmc"]
