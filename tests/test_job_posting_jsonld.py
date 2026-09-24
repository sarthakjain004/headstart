"""Tests for `headstart.scrapers.job_posting_jsonld` (ADR-0196).

Table-driven: each row is one page or node shape and what the reader must make of it. The
per-scraper tests keep what each scraper chose on its own (iCIMS's allowlist, Meta's description
sections, Workday's `timeType` mapping); these pin the behaviour every one of them now shares.
"""

from __future__ import annotations

import json

import pytest

from headstart.scrapers.job_posting_jsonld import (
    find_job_posting,
    job_posting_fields,
    jsonld_nodes,
    place_of,
)

POSTING = {"@type": "JobPosting", "title": "Backend Engineer"}
ORGANIZATION = {"@type": "Organization", "name": "Acme"}


def _script(
    payload: object, opening: str = '<script type="application/ld+json">'
) -> str:
    return f"{opening}{json.dumps(payload)}</script>"


@pytest.mark.parametrize(
    ("page", "expected_title"),
    [
        pytest.param(_script(POSTING), "Backend Engineer", id="one-block"),
        pytest.param(
            _script({**POSTING, "@type": ["JobPosting"]}),
            "Backend Engineer",
            id="type-is-a-list",
        ),
        pytest.param(
            _script([ORGANIZATION, POSTING]), "Backend Engineer", id="top-level-array"
        ),
        pytest.param(
            _script(
                {"@context": "https://schema.org", "@graph": [ORGANIZATION, POSTING]}
            ),
            "Backend Engineer",
            id="graph",
        ),
        pytest.param(
            '<script type="application/ld+json">'
            '{"@type": "JobPosting", "title": "Line one\nline two"}</script>',
            "Line one\nline two",
            id="literal-newline-in-a-string",
        ),
        pytest.param(
            '<script type="application/ld+json">{"@type": "JobPosting",,}</script>'
            + _script(POSTING),
            "Backend Engineer",
            id="malformed-block-before-a-good-one",
        ),
        pytest.param(
            _script(ORGANIZATION) + _script(POSTING),
            "Backend Engineer",
            id="posting-in-a-later-block",
        ),
        pytest.param(
            _script(POSTING, "<script id='ld' type='application/ld+json'>"),
            "Backend Engineer",
            id="single-quoted-attributes",
        ),
        pytest.param(
            _script(POSTING, '<SCRIPT TYPE="application/ld+json" nonce="n">').replace(
                "</script>", "</SCRIPT>"
            ),
            "Backend Engineer",
            id="upper-case-tag",
        ),
        pytest.param(
            _script({**POSTING, "title": "First"})
            + _script({**POSTING, "title": "Second"}),
            "First",
            id="first-posting-wins",
        ),
        pytest.param(_script(ORGANIZATION), None, id="no-posting"),
        pytest.param(
            '<script type="application/ld+json">[1, "x"]</script>', None, id="no-nodes"
        ),
        pytest.param("<html><body>no JSON-LD</body></html>", None, id="no-block"),
        pytest.param(
            '<script type="text/javascript">' + json.dumps(POSTING) + "</script>",
            None,
            id="another-script-type",
        ),
    ],
)
def test_find_job_posting(page: str, expected_title: str | None) -> None:
    posting = find_job_posting(page)
    assert (posting or {}).get("title") == expected_title


def test_jsonld_nodes_yields_every_node_of_the_type_in_document_order() -> None:
    page = (
        _script(ORGANIZATION)
        + _script({"@graph": [{**POSTING, "title": "A"}, ORGANIZATION]})
        + _script([{**POSTING, "title": "B"}])
    )
    assert [n["title"] for n in jsonld_nodes(page, "JobPosting")] == ["A", "B"]
    assert [n["name"] for n in jsonld_nodes(page, "Organization")] == ["Acme", "Acme"]


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        pytest.param(
            {
                "title": "SRE",
                "description": "<p>Run things.</p>",
                "datePosted": "2026-07-10",
                "employmentType": "FULL_TIME",
                "jobLocationType": "TELECOMMUTE",
                "jobLocation": {
                    "address": {
                        "addressLocality": "Pune",
                        "addressRegion": "MH",
                        "addressCountry": "IN",
                    }
                },
            },
            {
                "title": "SRE",
                "description": "<p>Run things.</p>",
                "location": "Pune, MH, IN",
                "posted_at": "2026-07-10",
                "employment_type": "FULL_TIME",
                "remote": True,
            },
            id="every-field",
        ),
        pytest.param(
            {
                "employmentType": ["FULL_TIME", "CONTRACTOR"],
                "jobLocationType": "ONSITE",
            },
            {
                "title": None,
                "description": None,
                "location": None,
                "posted_at": None,
                "employment_type": "FULL_TIME, CONTRACTOR",
                "remote": None,
            },
            id="employment-list-joined-and-only-telecommute-is-remote",
        ),
        pytest.param(
            {"employmentType": []},
            {
                "title": None,
                "description": None,
                "location": None,
                "posted_at": None,
                "employment_type": None,
                "remote": None,
            },
            id="empty-employment-list",
        ),
    ],
)
def test_job_posting_fields(node: dict, expected: dict) -> None:
    assert job_posting_fields(node) == expected


def _place(locality=None, region=None, country=None) -> dict:
    return {
        "address": {
            "addressLocality": locality,
            "addressRegion": region,
            "addressCountry": country,
        }
    }


@pytest.mark.parametrize(
    ("job_location", "options", "expected"),
    [
        pytest.param(_place("Pune", "MH", "IN"), {}, "Pune, MH, IN", id="one-place"),
        pytest.param(
            [_place("Berlin", None, "DE"), _place("Munich", None, "DE")],
            {},
            "Berlin, DE",
            id="first-place-only",
        ),
        pytest.param(
            _place("Hsinchu", None, {"@type": "Country", "name": "TW"}),
            {},
            "Hsinchu, TW",
            id="country-node",
        ),
        pytest.param(
            _place(" Bangalore ", "", "IN"), {}, "Bangalore, IN", id="stripped"
        ),
        pytest.param(_place(None, None, None), {}, None, id="empty-address"),
        pytest.param([], {}, None, id="empty-list"),
        pytest.param(None, {}, None, id="absent"),
        pytest.param({"name": "HQ"}, {}, None, id="no-address"),
        pytest.param({"address": "Pune"}, {}, None, id="address-not-a-node"),
        pytest.param(
            _place("Buffalo", "UNAVAILABLE", "US"),
            {},
            "Buffalo, UNAVAILABLE, US",
            id="placeholder-kept-by-default",
        ),
        pytest.param(
            _place("Buffalo", "UNAVAILABLE", "US"),
            {"placeholders": {"UNAVAILABLE"}},
            "Buffalo, US",
            id="placeholder-dropped",
        ),
        pytest.param(
            _place("Singapore", "Singapore", "SG"),
            {},
            "Singapore, Singapore, SG",
            id="repeat-kept-by-default",
        ),
        pytest.param(
            _place("Singapore", "Singapore", "SG"),
            {"drop_repeats": True},
            "Singapore, SG",
            id="repeat-dropped",
        ),
        pytest.param(
            _place("Hyderabad", "Telangana,IN", "IN"),
            {"drop_repeats": True},
            "Hyderabad, Telangana,IN",
            id="repeat-held-as-a-comma-piece",
        ),
    ],
)
def test_place_of(job_location: object, options: dict, expected: str | None) -> None:
    assert place_of(job_location, **options) == expected
