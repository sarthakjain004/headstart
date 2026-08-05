"""Digest rendering. The body is dependency-free so it runs in CI's quality job; the
spreadsheet needs xlsxwriter and is skipped there (ADR-0035)."""

import pytest

from headstart.alerts import digest as d
from headstart.alerts.store import Subscription

SUB = Subscription(id="abc", email="ada@example.com", query="backend engineer")
JOBS = [
    {
        "company": "Acme",
        "title": "Backend Engineer",
        "location": "Bengaluru",
        "score": 0.8123,
        "url": "https://jobs.example/1",
    },
    {"company": "Globex", "title": "Platform Engineer", "score": None, "url": ""},
]


def test_subject_counts_and_names_the_query():
    assert d.subject_for(SUB, JOBS) == "2 new matches for “backend engineer”"
    assert d.subject_for(SUB, JOBS[:1]).startswith("1 new match ")


def test_text_body_carries_every_link_and_score():
    body = d.render(SUB, JOBS, "https://space/unsub?id=abc").text
    assert "https://jobs.example/1" in body
    assert "0.812" in body  # rounded for display
    assert "Acme — Backend Engineer — Bengaluru" in body
    assert "https://space/unsub?id=abc" in body  # unsubscribe is always present


def test_missing_score_renders_without_raising():
    assert "—" in d.render(SUB, JOBS, "https://u").text


def test_html_escapes_untrusted_fields():
    nasty = [
        {
            "company": "<script>alert(1)</script>",
            "title": "Engineer",
            "score": 0.5,
            "url": 'https://x/"onmouseover="alert(1)',
        }
    ]
    markup = d.render(SUB, nasty, "https://u").html
    assert "<script>" not in markup
    assert '"onmouseover="' not in markup


def test_xlsx_has_a_header_row_and_one_row_per_job():
    pytest.importorskip(
        "xlsxwriter"
    )  # [alerts] extra — not installed in CI's quality job
    openpyxl = pytest.importorskip("openpyxl")
    import io

    book = openpyxl.load_workbook(io.BytesIO(d.to_xlsx(JOBS)))
    sheet = book.active
    assert [c.value for c in sheet[1]] == list(d.COLUMNS)
    assert sheet.max_row == len(JOBS) + 1
    assert sheet.cell(row=2, column=1).value == "Acme"
