"""Tests for the salary-extraction cascade (ADR-0082, docs/salary-extraction/).

Covers Tier 1's per-ATS field parsers, Tier 2's description-mining patterns and guards, and the
no-estimate-fallback cascade ordering. The false-positive guard tests use real phrasing mined from
`workable` description text during the pilot (docs/salary-extraction/workable.md) — company
revenue/funding narrative and benefit-contribution amounts both misread as a salary figure when
unguarded.
"""

from __future__ import annotations

from headstart.salary import SalarySpan, extract, from_description, from_field

# --- Tier 1: from_field, per-ATS formats ------------------------------------------------------


def test_field_lever():
    assert from_field("50000-70000 USD per-year-salary", "lever") == SalarySpan(
        50000, 70000, "USD", "field"
    )


def test_field_recruitee():
    assert from_field("50000-70000 EUR per year", "recruitee") == SalarySpan(
        50000, 70000, "EUR", "field"
    )


def test_field_teamtailor():
    assert from_field("40000-60000 EUR YEAR", "teamtailor") == SalarySpan(
        40000, 60000, "EUR", "field"
    )


def test_field_keka_no_period_left_unannualized():
    # keka's payload never carries the period at all (its scraper's own docstring). An
    # annually-plausible figure is kept as-is rather than guessing a period.
    assert from_field("2500000-3000000 INR", "keka") == SalarySpan(
        2_500_000, 3_000_000, "INR", "field"
    )


def test_field_keka_low_magnitude_real_example_correctly_unresolved():
    # keka.py's own docstring example, "25000-30000 INR", is almost certainly a monthly figure
    # (₹25-30k/mo is plausible; as an annual figure it isn't) — but the period genuinely isn't in
    # the payload, so this is honestly unresolved rather than guessed at, pending keka's own
    # research pass (docs/salary-extraction/README.md's processing order).
    assert from_field("25000-30000 INR", "keka") is None


def test_field_darwinbox_lakhs():
    # "INR 3 - 5 (Annual)" is lakhs, not absolute rupees (ADR-0019's documented example) —
    # 3-5 lakh = 300,000-500,000.
    assert from_field("INR 3 - 5 (Annual)", "darwinbox") == SalarySpan(
        300_000, 500_000, "INR", "field"
    )


def test_field_darwinbox_non_inr_rejected():
    # the lakhs multiplier is INR-specific; a non-INR darwinbox string has no known shape.
    assert from_field("USD 3 - 5 (Annual)", "darwinbox") is None


def test_field_generic_fallback_for_unlisted_ats():
    # an ATS with no calibrated Tier-1 parser yet still gets a best-effort range/currency read.
    assert from_field("80000-100000 USD", "some-new-ats") == SalarySpan(
        80000, 100000, "USD", "field"
    )


def test_field_empty_or_none():
    assert from_field(None) is None
    assert from_field("") is None
    assert from_field("   ") is None


def test_field_implausible_magnitude_rejected():
    # a keka string reading "25-30" with no currency clue defaults to the USD bound check and
    # fails it (25-30 USD/year is not a real salary) — better to reject than mis-scale.
    assert from_field("25-30", "keka") is None


# --- Tier 2: from_description ------------------------------------------------------------------


def test_description_labeled_single_gbp():
    assert from_description(
        "Salary: upto £29,000 - depending on experience"
    ) == SalarySpan(29000, None, "GBP", "regex")


def test_description_labeled_range_usd_k_shorthand():
    assert from_description("Compensation: $100-120k") == SalarySpan(
        100_000, 120_000, "USD", "regex"
    )


def test_description_hourly_range():
    span = from_description("Pay range: $23 - $25/hr")
    assert span.currency == "USD"
    assert span.min_annual == 23 * 2080
    assert span.max_annual == 25 * 2080
    assert span.source == "regex"


def test_description_bare_hr_no_slash():
    # real workable phrasing: "hr" with no slash and no "per" — must still annualize.
    span = from_description("Up to $25.00- $35.00 hr DOE")
    assert span.min_annual == 25 * 2080
    assert span.max_annual == 35 * 2080


def test_description_daily_rate():
    span = from_description("Salary: $300 - $400 day")
    assert span.min_annual == 300 * 260
    assert span.max_annual == 400 * 260


def test_description_lpa_pattern():
    # LPA gets its own first-class pattern per the India-strong-segment scope (CLAUDE.md).
    span = from_description("Compensation: 8-12 LPA")
    assert span is not None
    assert span.currency is None or span.currency == "INR"


def test_description_bare_dollar_range_no_label():
    span = from_description(
        "What we offer: base pay of $60,000 - $75,000 annually plus bonus"
    )
    assert span.min_annual == 60000
    assert span.max_annual == 75000
    assert span.currency == "USD"


def test_description_salary_range_label_variant():
    # only "pay" originally had the optional range/rate suffix; "salary range" is at least as
    # common in real text.
    span = from_description("Salary range: 50k – 70k per year")
    assert span == SalarySpan(50000, 70000, None, "regex")


def test_description_bare_range_with_currency_code_no_symbol():
    span = from_description(
        "Compensation - Base plus commission (50,000-70,000 USD/year) once license is obtained"
    )
    assert span == SalarySpan(50000, 70000, "USD", "regex")


def test_description_hkd_currency_recognized():
    span = from_field("500000-600000 HKD", "some-new-ats")
    assert span.currency == "HKD"


def test_description_none_or_empty():
    assert from_description(None) is None
    assert from_description("") is None


def test_description_no_mention():
    assert (
        from_description("We are looking for a great engineer to join our team.")
        is None
    )


# --- Guards: real false-positive classes found in workable description text --------------------


def test_guard_company_revenue_not_salary():
    # "With over $8 billion in annual revenue and a blue-chip client base" — real workable text.
    text = (
        "With over $8 billion in annual revenue and a blue-chip client base, ABM delivers "
        "innovative technologies."
    )
    assert from_description(text) is None


def test_guard_funding_round_not_salary():
    text = "Finalized the biggest Series B this year (€30 million). Grown their team to 160."
    assert from_description(text) is None


def test_guard_benefit_contribution_not_salary():
    # real, repeated workable boilerplate: an HSA contribution amount, not a wage.
    text = "Boot reimbursement program. Up to $2,400 company contribution to Health Savings Account (HSA)."
    assert from_description(text) is None


def test_guard_signing_bonus_not_salary():
    text = "You'll also receive a $5,000 signing bonus after 90 days."
    assert from_description(text) is None


def test_guard_implausible_magnitude_rejected():
    # a bare, unlabeled tiny USD figure with no period marker: ambiguous, not guessed at.
    assert from_description("Wage: $55 - $60 DOE") is None


def test_guard_multiple_inconsistent_ranges_are_ambiguous():
    text = "Salary: $60,000 - $70,000. Note: for the Chicago office, salary is $90,000 - $100,000."
    assert from_description(text) is None


def test_guard_repeated_consistent_mentions_still_extract():
    text = "Salary: $60,000 - $70,000. This role pays $60,000 - $70,000 depending on experience."
    span = from_description(text)
    assert span is not None
    assert span.min_annual == 60000


# --- Cascade ordering: field always wins, no third tier ----------------------------------------


def test_extract_field_wins_over_description():
    span = extract(
        "50000-70000 USD per-year-salary",
        "Salary: $200,000 - $250,000",  # description disagrees; field still wins
        "lever",
    )
    assert span == SalarySpan(50000, 70000, "USD", "field")


def test_extract_falls_through_to_description():
    span = extract(None, "Compensation: $100-120k", "workable")
    assert span == SalarySpan(100_000, 120_000, "USD", "regex")


def test_extract_none_when_neither_tier_finds_anything():
    # no seniority-style fallback exists — unlike experience.extract, this must never estimate.
    assert extract(None, "We're hiring a great teammate.", "workable") is None
    assert extract("", "", "workable") is None
