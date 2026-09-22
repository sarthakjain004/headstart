"""Tests for the Wayback archived recruiting-contact collector."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "discover"))

import wayback_hr_emails as collector


def test_role_inbox_is_kept_with_its_hiring_context():
    page = "<title>Acme careers</title><p>Email careers@acme.example to apply.</p>"
    assert collector.contacts(page) == [
        (
            "careers@acme.example",
            "recruiting_role_inbox",
            "Acme careers Email careers@acme.example to apply.",
        )
    ]


def test_mailto_target_is_kept_even_when_its_label_omits_the_address():
    page = '<a href="mailto:jobs@acme.example">Contact our jobs team</a>'
    assert collector.contacts(page)[0][:2] == (
        "jobs@acme.example",
        "recruiting_role_inbox",
    )


def test_eeo_and_named_addresses_are_not_recruiting_contacts():
    page = "Contact hr@acme.example for disability accommodation. Send a resume to ada@acme.example."
    assert collector.contacts(page) == []


def test_application_context_can_keep_a_generic_contact_address():
    page = "For this job application, email contact@acme.example with your resume."
    assert collector.contacts(page)[0][:2] == (
        "contact@acme.example",
        "application_contact",
    )


def test_cdx_rows_separates_the_resume_key():
    text = "20240101010101 https://jobs.example/a DIGEST\n\nresume-key\n"
    assert collector.cdx_rows(text) == (
        [("20240101010101", "https://jobs.example/a")],
        "resume-key",
    )


def test_board_path_filter_rejects_a_lexical_sibling_tenant():
    assert collector.belongs_to_board(
        "https://job-boards.greenhouse.io/stripe",
        "https://job-boards.greenhouse.io/stripe/jobs/1",
    )
    assert not collector.belongs_to_board(
        "https://job-boards.greenhouse.io/stripe",
        "https://job-boards.greenhouse.io/stripes/jobs/1",
    )
