"""Digest rendering (ADR-0035). The body renderer is dependency-free; the spreadsheet needs
xlsxwriter, which is in the [dev] extra so this runs in CI — the importorskip below only
guards a bare environment."""

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
    pytest.importorskip("xlsxwriter")  # in [dev]; guard for a bare environment
    openpyxl = pytest.importorskip("openpyxl")
    import io

    book = openpyxl.load_workbook(io.BytesIO(d.to_xlsx(JOBS)))
    sheet = book.active
    assert [c.value for c in sheet[1]] == list(d.COLUMNS)
    assert sheet.max_row == len(JOBS) + 1
    assert sheet.cell(row=2, column=1).value == "Acme"


def test_to_telegram_chunks_past_the_message_cap():
    # Telegram caps a message at 4096 chars and a Digest carries up to 30 roles.
    sub = Subscription(id="a", email="a@b.c", query="backend")
    jobs = [
        {
            "title": f"Engineer {i}",
            "company": "Acme",
            "score": 0.5,
            "url": f"https://j/{i}",
        }
        for i in range(25)
    ]

    chunks = d.to_telegram(sub, jobs)

    assert len(chunks) == 3, "25 roles at 10 per message"
    assert all(len(c) < 4096 for c in chunks)
    assert "25 new job(s)" in chunks[0]
    assert "continued (11" in chunks[1]
    # Every role appears exactly once across the chunks, in order.
    joined = "\n".join(chunks)
    assert [
        i for i in range(25) if f"Engineer {i}<" in joined or f"Engineer {i} " in joined
    ]
    assert joined.count("https://j/7") == 1


def test_to_telegram_escapes_markup_so_telegram_cannot_reject_the_message():
    sub = Subscription(id="a", email="a@b.c", query="c++ <dev>")
    jobs = [
        {
            "title": "R&D <lead>",
            "company": "A&B",
            "score": 1.0,
            "url": "https://j/1?a=1&b=2",
        }
    ]

    only = d.to_telegram(sub, jobs)[0]

    assert "<dev>" not in only and "&lt;dev&gt;" in only
    assert "R&amp;D" in only
    assert "a=1&amp;b=2" in only
