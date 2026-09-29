"""Tests for headstart.jobs.work_authorization: the sponsorship and relocation stances a job
description states, read by negation-aware rules (ADR-0333). Every phrasing is a real posting's,
from the hand-labelled sample the ADR measures, trimmed to the sentences that decide it."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from headstart.jobs import work_authorization as wa

OFFERS = wa.OFFERS_SPONSORSHIP
REFUSES = wa.REFUSES_SPONSORSHIP
RELOCATION = wa.OFFERS_RELOCATION


@pytest.mark.parametrize(
    "text",
    [
        "Visa sponsorship is available for this role.",
        (
            "COMPENSATION & BENEFITS - Salary: $175,000 – $225,000 USD annually - Visa sponsorship "
            "available LOCATION This role is fully on-site in San Francisco, CA."
        ),
        "Sponsorship support: We sponsor visas to help exceptional talent join our team.",
        (
            "Work Authorization Candidates must be legally authorized to work in the United "
            "States. Visa sponsorship is available for eligible candidates."
        ),
        (
            "Capital One will consider sponsoring a new qualified applicant for employment "
            "authorization for this position."
        ),
        (
            "Santander will pay the employer mandatory government fees that are required to pay "
            "in connection with visa sponsorship."
        ),
        "Visa sponsorship: We sponsor visas in both the UK and US.",
        "If you are outside Canada, we support relocation and immigration.",
    ],
)
def test_an_offer_of_sponsorship(text):
    assert OFFERS in wa.stances(text)
    assert REFUSES not in wa.stances(text)


@pytest.mark.parametrize(
    "text",
    [
        "Visa sponsorship is not available for our new grad positions.",
        (
            "You must be work authorized in the United States on a full-time basis without the "
            "need for employer sponsorship now or in the future."
        ),
        "Caylent is not able to sponsor visa or work permit applications at this time.",
        (
            "Employee Status: Regular Relocation: Domestic VISA Sponsorship: No Travel "
            "Requirements: No Travel Required"
        ),
        (
            "Travel Requirements 0% Available for Work Visa Sponsorship? No Government Clearance "
            "Required? No Job Posting End Date"
        ),
        "This position is not eligible for sponsorship.",
        (
            "PwC does not intend to hire experienced or entry level job seekers who will need, now "
            "or in the future, PwC sponsorship through the H-1B lottery."
        ),
        (
            "For US based roles only, please note the Company may not be able to employ candidates "
            "for this role who have United States work authorization related to certain U.S. visa "
            "categories, or support future H-1B sponsorship at this time."
        ),
        "Must be a U.S. citizen with the ability to obtain a security clearance.",
        (
            "Must be based in the Bay Area with U.S. citizenship or permanent residency Careers "
            "Privacy Statement ***Keysight is an Equal Opportunity Employer."
        ),
        "We are hiring for US Citizens and do not provide H1B Visa support.",
    ],
)
def test_a_refusal_of_sponsorship(text):
    assert wa.stances(text) >= {REFUSES}
    assert OFFERS not in wa.stances(text)


def test_a_text_that_offers_and_refuses_is_read_as_refusing():
    text = (
        "Visa sponsorship is available for our London office. "
        "We cannot sponsor visas for roles based in the United States."
    )
    assert wa.stances(text) == {REFUSES}


@pytest.mark.parametrize(
    "text",
    [
        "Act as the AWS executive technical sponsor for strategic customer initiatives.",
        "Eligible for company-sponsored group health, dental, vision, and life insurance.",
        "Each group has an executive sponsor and is open to all employees.",
        (
            "Workstation Support is responsible for delivering onsite technical support for Visa "
            "staff."
        ),
        (
            "Hybrid working arrangements, individual athletic sponsorship, study assistance "
            "sponsorship, employee referral rewards."
        ),
        "Partner with executive sponsorship to land the design across teams.",
        (
            "Candidates must be willing to obtain a security clearance; we will sponsor your "
            "clearance."
        ),
        (
            "We comply with all laws and do not discriminate on the basis of citizenship status or "
            "national origin."
        ),
        (
            "Travel Requirements Available for Work Visa Sponsorship? Government Clearance "
            "Required? Job Posting End Date"
        ),
        "Sponsorship for future FTE roles is not guaranteed.",
    ],
)
def test_a_sponsor_word_about_something_else_is_no_stance(text):
    assert not wa.stances(text) & {OFFERS, REFUSES}


def test_sponsorship_not_guaranteed_offers_it_to_some():
    assert wa.stances("Sponsorship for this role is not guaranteed.") == {OFFERS}


@pytest.mark.parametrize(
    "text",
    [
        "Relocation assistance may be available for this position.",
        "Relocation: This position offers relocation based on candidate eligibility.",
        "Additional Information Relocation Assistance Provided: Yes",
        (
            "Hassel free Relocation: Support and reimbursement for newly hired employees "
            "relocating to Bangalore, India."
        ),
        "We offer relocation assistance to new employee.",
        "Competitive Salary, Bonus Scheme, plus relocation assistance",
    ],
)
def test_an_offer_of_relocation(text):
    assert RELOCATION in wa.stances(text)


@pytest.mark.parametrize(
    "text",
    [
        (
            "RELOCATION ASSISTANCE: No relocation assistance available CLEARANCE REQUIRED FOR "
            "START: Yes"
        ),
        "Local candidates given preference – relocation assistance not available.",
        "Travel Requirements None Relocation Provided None Position Type Experienced",
        "10% Relocation Assistance : None Sponsorship Available : No PREFERRED QUALIFICATIONS",
        (
            "Candidates must currently reside in the Beaverton, Oregon, area or be willing to "
            "relocate to Beaverton before their start date."
        ),
        "The role focuses on water main relocation and sizing, and drainage systems.",
        "You may be liable for your own personal employee immigration and relocation costs.",
    ],
)
def test_no_offer_of_relocation(text):
    assert RELOCATION not in wa.stances(text)


def test_nothing_to_read_is_no_stance():
    assert wa.stances(None) == frozenset()
    assert wa.stances("") == frozenset()
    assert wa.stances("Build distributed systems in Go.") == frozenset()


def test_every_word_a_rule_reads_is_one_the_prefilter_finds():
    # The Space reads only descriptions PREFILTER matches (in Rust's engine), so a stance held
    # by a text the prefilter misses would never be served.
    prefilter = re.compile(wa.PREFILTER)
    for text in (
        "Must be a U.S. person as defined by ITAR.",
        "Visa sponsorship available.",
        "Relocation assistance provided.",
        "We do not provide visa support.",
        "Must be a US citizen.",
    ):
        assert wa.stances(text), text
        assert prefilter.search(text), text


def test_mentions_quote_each_sentence_about_visas_and_relocation():
    text = (
        "Build our payments platform. Visa Sponsorship: Employer will not sponsor applicants "
        "for employment visa status. We offer relocation assistance. We are an equal "
        "opportunity employer and do not discriminate on citizenship status. Eligible for "
        "company-sponsored health insurance."
    )
    said = wa.mentions(text)
    assert said == [
        "Visa Sponsorship: Employer will not sponsor applicants for employment visa status.",
        "We offer relocation assistance.",
    ]


def test_mentions_cut_a_long_sentence_around_its_word():
    text = "x" * 400 + " We cannot sponsor a visa for this role " + "y" * 400
    (said,) = wa.mentions(text)
    assert len(said) <= wa.MENTION_CHARS
    assert "cannot sponsor a visa" in said and said.startswith("…")


def test_mentions_stop_at_their_bound():
    text = " ".join(f"Relocation offered in city {n}." for n in range(9))
    assert len(wa.mentions(text)) == wa.MENTIONS_MAX


# ---- the hand-labelled sample ADR-0333 measures the rules on ----

_LABELLED = Path(__file__).parent / "fixtures" / "work_authorization_labelled.jsonl"

#: Each stance, and the labels that make it true.
_TRUE_WHEN = {
    OFFERS: ("sponsorship", {"offers"}),
    REFUSES: ("sponsorship", {"refuses", "mixed"}),
    RELOCATION: ("relocation", {"offers"}),
}


def _labelled(sample=None):
    rows = [
        json.loads(line) for line in _LABELLED.read_text(encoding="utf-8").splitlines()
    ]
    return [row for row in rows if sample is None or row["sample"] == sample]


def _precision_recall(rows, stance):
    field, true = _TRUE_WHEN[stance]
    read = [(stance in wa.stances(row["text"]), row[field] in true) for row in rows]
    tp = sum(got and want for got, want in read)
    return tp / sum(got for got, _ in read), tp / sum(want for _, want in read)


@pytest.mark.parametrize(
    ("stance", "precision_at_least", "recall_at_least"),
    [(OFFERS, 0.97, 0.95), (REFUSES, 0.96, 0.96), (RELOCATION, 0.98, 0.98)],
)
def test_the_rules_hold_their_measured_rates_on_the_labelled_sample(
    stance, precision_at_least, recall_at_least
):
    # 660 descriptions read by hand (ADR-0333); measured 0.98/0.96, 0.97/0.97 and 0.99/0.99.
    precision, recall = _precision_recall(_labelled(), stance)
    assert precision >= precision_at_least and recall >= recall_at_least


def test_offers_hold_their_precision_on_the_sample_drawn_after_the_rules_froze():
    # 70 jobs the frozen rules said offer sponsorship, drawn and read after the last change.
    rows = [
        row
        for row in _labelled("predicted-offers-v2-frozen")
        if OFFERS in wa.stances(row["text"])
    ]
    field, true = _TRUE_WHEN[OFFERS]
    right = sum(row[field] in true for row in rows)
    assert len(rows) == 70 and right >= 68
