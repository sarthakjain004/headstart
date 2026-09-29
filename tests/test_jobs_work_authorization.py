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
MAY = wa.MAY_OFFER_SPONSORSHIP
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
    # A job in the United States, so an offer naming the US or the UK reaches it (ADR-0353).
    assert OFFERS in wa.stances(text, "Software Engineer", "Austin, TX")
    assert REFUSES not in wa.stances(text, "Software Engineer", "Austin, TX")


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
    assert not wa.stances(text) & {OFFERS, MAY, REFUSES}


@pytest.mark.parametrize(
    "text",
    [
        # Amgen, round-4 critique P1-3.
        "Sponsorship Sponsorship for this role is not guaranteed.",
        # Diligent, round-4 critique p1_offers_1.
        (
            "Visa sponsorship may be available for select positions based on business needs and "
            "specific role requirements."
        ),
        (
            "Sponsorship decisions are evaluated on a case-by-case basis, and eligibility should "
            "not be assumed for all opportunities."
        ),
        "We may sponsor H-1B visas for exceptional candidates.",
        "Visa sponsorship is not always available.",
        "Visa sponsorship might be possible for the right candidate.",
    ],
)
def test_a_hedged_offer_may_offer_sponsorship(text):
    # ADR-0353: offered to some, not promised to this job.
    assert wa.stances(text, "Software Engineer", "Austin, TX") == {MAY}


def test_a_firm_offer_outranks_a_hedged_one_and_a_refusal_both():
    hedged = "Sponsorship for this role is not guaranteed. "
    assert wa.stances(hedged + "Visa sponsorship is available.") == {OFFERS}
    assert wa.stances(hedged + "We cannot sponsor visas.") == {REFUSES}


@pytest.mark.parametrize(
    ("location", "want"),
    [
        ("Berlin, Germany", {OFFERS}),
        ("Munich", {OFFERS}),
        # n8n "Senior Developer Advocate, US", round-4 critique P1-3.
        ("United States", set()),
        ("New York, NY", set()),
        # A place no country can be read from: it may reach the job.
        ("Remote", {MAY}),
        (None, {MAY}),
    ],
)
def test_an_offer_scoped_to_a_country_is_judged_against_the_jobs_place(location, want):
    text = (
        "We can sponsor visas to Germany; for any other country, you need to have existing "
        "right to work."
    )
    assert wa.stances(text, "Senior Developer Advocate", location) == want


def test_an_offer_scoped_only_to_another_country_refuses_this_job():
    text = "Visa sponsorship is available only for roles based in the Netherlands."
    assert wa.stances(text, "Backend Engineer", "Amsterdam, NL") == {OFFERS}
    assert wa.stances(text, "Backend Engineer", "London, United Kingdom") == {REFUSES}


def test_a_country_the_candidate_comes_from_is_not_the_offers_scope():
    text = "If you are outside Canada, we support relocation and immigration."
    assert OFFERS in wa.stances(text, "Engineer", "Toronto, ON, Canada")


@pytest.mark.parametrize(
    ("title", "want"),
    [
        ("Principal Software Engineer", {OFFERS}),
        ("Director, Software Engineering", {OFFERS}),
        ("Senior Software Engineer", {REFUSES}),
        ("Software Engineer II", {REFUSES}),
        # Medtronic's "Software Engineering Manager", round-4 critique P1-3: a manager's rank
        # beside a Principal engineer's is not read from the title.
        ("Software Engineering Manager", {MAY}),
        (None, {MAY}),
    ],
)
def test_an_offer_limited_to_levels_is_judged_against_the_title(title, want):
    text = (
        "Visa sponsorship for this position is offered exclusively for Principal-level roles "
        "and above. Roles below the Principal level require unrestricted U.S. work "
        "authorization."
    )
    assert wa.stances(text, title, "Minneapolis, MN") == want


@pytest.mark.parametrize(
    "text",
    [
        # AeroVironment, round-4 critique P1-3.
        "Some positions will require current U.S. Citizenship due to contract requirements.",
        "Certain roles may require U.S. citizenship to obtain a security clearance.",
        "This position may require U.S. citizenship.",
    ],
)
def test_a_citizenship_requirement_of_some_positions_does_not_refuse_this_job(text):
    assert REFUSES not in wa.stances(text, "Software Engineer", "Austin, TX")


@pytest.mark.parametrize(
    ("text", "want"),
    [
        # US Bank: an agency's name, not a requirement.
        (
            "The E-Verify program is operated by the U.S. Citizenship and Immigration Services.",
            set(),
        ),
        # RTX: a field label before an offer.
        (
            (
                "U.S. Citizen, U.S. Person, or Immigration Status Requirements: The company "
                "will offer immigration sponsorship for this position, if needed."
            ),
            {OFFERS},
        ),
    ],
)
def test_citizenship_named_but_not_required_does_not_refuse(text, want):
    assert wa.stances(text, "Quality Engineer", "San Marcos, TX") == want


def test_an_offer_to_candidates_in_the_eu_reaches_a_job_in_a_member_country():
    text = (
        "Visa sponsorship may be available for eligible candidates already located in a UK/EU "
        "country who require support."
    )
    assert wa.stances(text, "Backend Engineer", "France") == {MAY}
    assert wa.stances(text, "Backend Engineer", "United States") == set()


def test_an_offer_not_made_for_every_role_is_hedged_and_a_firm_one_beside_it_stands():
    # Anthropic, labelled offers in ADR-0333's frozen sample, had read as a refusal.
    hedge = "However, we aren't able to successfully sponsor visas for every role."
    assert wa.stances(hedge, "Engineer", "Seattle, WA") == {MAY}
    assert wa.stances("We do sponsor visas! " + hedge, "Engineer", "Seattle, WA") == {
        OFFERS
    }


@pytest.mark.parametrize(
    ("text", "want"),
    [
        # PHINIA, drawn after the first freeze: the company sponsors, this role does not.
        (
            (
                "PHINIA does provide sponsorship for employment visa status based on business need. "
                "However, for this role, applicants must be currently authorized to work on a "
                "full-time basis, in the country where the position is currently based."
            ),
            {REFUSES},
        ),
        (
            "You must currently possess valid and unrestricted U.S. work authorization.",
            {REFUSES},
        ),
        # UCI: a heading, then a demand for proof; nothing is offered.
        (
            (
                "Consideration for Work Authorization Sponsorship Must be able to provide proof of "
                "work authorization"
            ),
            set(),
        ),
        # An offer made later, or for a move the job does not need.
        (
            "Visa sponsorship and relocation support for a move to NYC (after 2 years' tenure)",
            {MAY, RELOCATION},
        ),
        ("If you wish to relocate, we are happy to help you obtain a visa.", {MAY}),
        # The usual demand beside an offer is no refusal.
        (
            (
                "Candidates must be legally authorized to work in the United States. Visa "
                "sponsorship is available for this position."
            ),
            {OFFERS},
        ),
    ],
)
def test_what_the_first_draw_after_the_freeze_got_wrong(text, want):
    assert wa.stances(text, "Engineer", "Austin, TX") == want


@pytest.mark.parametrize(
    ("text", "want"),
    [
        # Carrier: a curly apostrophe negates as a straight one does.
        ("This position doesn’t support immigration sponsorship.", {REFUSES}),
        # MSR-FSR: "support" is the job's duty; the authorisation is demanded.
        (
            (
                "Travel to customer sites to support essential therefore EU work authorisation "
                "and eligibility required."
            ),
            set(),
        ),
    ],
)
def test_what_the_second_draw_after_the_freeze_got_wrong(text, want):
    assert wa.stances(text, "Engineer", "Dresden, Germany") == want


@pytest.mark.parametrize(
    ("text", "want"),
    [
        (
            "You must have a valid NZ work visa for your application to be considered.",
            {REFUSES},
        ),
        (
            (
                "Collaborate with cross-functional teams to support immigration and border "
                "security operations globally."
            ),
            set(),
        ),
    ],
)
def test_what_the_draw_after_the_last_freeze_got_wrong(text, want):
    assert wa.stances(text, "Engineer", "Wellington, New Zealand") == want


def test_mentions_quote_a_us_person_requirement():
    text = "Applicants will be asked to verify U.S. person status under the ITAR."
    assert wa.mentions(text) == [text]


def test_a_citizenship_requirement_of_this_job_still_refuses_it():
    for text in ("U.S. Citizenship required.", "Must be a U.S. citizen."):
        assert wa.stances(text, "Software Engineer", "Austin, TX") == {REFUSES}


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
    MAY: ("sponsorship", {"may_offer"}),
    REFUSES: ("sponsorship", {"refuses", "mixed"}),
    RELOCATION: ("relocation", {"offers"}),
}


def _labelled(sample=None):
    rows = [
        json.loads(line) for line in _LABELLED.read_text(encoding="utf-8").splitlines()
    ]
    return [row for row in rows if sample is None or row["sample"] == sample]


def _stances(row):
    """What the rules read in a labelled row, against its job's own title and place."""
    return wa.stances(row["text"], row["title"], row["location"])


def _precision_recall(rows, stance):
    field, true = _TRUE_WHEN[stance]
    read = [(stance in _stances(row), row[field] in true) for row in rows]
    tp = sum(got and want for got, want in read)
    return tp / sum(got for got, _ in read), tp / sum(want for _, want in read)


@pytest.mark.parametrize(
    ("stance", "precision_at_least", "recall_at_least"),
    [
        (OFFERS, 0.98, 0.96),
        (MAY, 0.97, 0.95),
        (REFUSES, 0.98, 0.97),
        (RELOCATION, 0.98, 0.98),
    ],
)
def test_the_rules_hold_their_measured_rates_on_the_labelled_sample(
    stance, precision_at_least, recall_at_least
):
    # 893 descriptions read by hand (ADR-0333, ADR-0353); measured 0.99/0.98, 1.00/1.00,
    # 0.98/0.98 and 0.99/0.99.
    precision, recall = _precision_recall(_labelled(), stance)
    assert precision >= precision_at_least and recall >= recall_at_least


def test_offers_hold_their_precision_on_the_sample_drawn_after_the_rules_froze():
    # ADR-0333's 70 frozen offers, read against each job's place and title (ADR-0353): 13 of
    # them are hedged and now only may offer; 58 of the 59 left are right.
    rows = [
        row
        for row in _labelled("predicted-offers-v2-frozen")
        if OFFERS in _stances(row)
    ]
    field, true = _TRUE_WHEN[OFFERS]
    right = sum(row[field] in true for row in rows)
    assert len(rows) == 59 and right >= 58


def test_offers_hold_their_precision_on_the_50_drawn_after_the_last_freeze():
    # ADR-0353: 50 live offers drawn after the rules froze, 48 right; the two wrong ones (a
    # demand for a valid local visa, immigration as a product's domain) were fixed after.
    rows = [
        row
        for row in _labelled("predicted-offers-v3-frozen")
        if OFFERS in _stances(row)
    ]
    field, true = _TRUE_WHEN[OFFERS]
    assert len(rows) >= 48 and sum(row[field] in true for row in rows) >= 48


def test_every_labelled_row_carries_its_jobs_title_and_place():
    # A scoped offer is judged against them (ADR-0353); None where the job left the index.
    assert all("title" in row and "location" in row for row in _labelled())
