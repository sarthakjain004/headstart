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


def test_field_darwinbox_monthly_timeframe_honored():
    # salary_timeframe (darwinbox.py) is a real, variable field, not always "Annual" — a monthly
    # figure must not silently read as annual (code-review finding, PR #234).
    assert from_field("INR 3 - 5 (Monthly)", "darwinbox") == SalarySpan(
        3_600_000, 6_000_000, "INR", "field"
    )


def test_field_generic_fallback_for_unlisted_ats():
    # an ATS with no calibrated Tier-1 parser yet still gets a best-effort range/currency read.
    assert from_field("80000-100000 USD", "some-new-ats") == SalarySpan(
        80000, 100000, "USD", "field"
    )


def test_field_generic_up_to_states_a_ceiling_not_a_floor():
    # Real, code review, PR #238: ashby/personio pass an HR system's raw free-text field straight
    # into Job.salary with no scraper-side normalization, so _field_generic hits the exact same
    # ceiling-vs-floor risk Tier 2 has — "Up to €50,000" must decline, not report €50,000 as a
    # floor. A real range is unaffected.
    assert from_field("Up to €50,000", "ashby") is None
    assert from_field("Salary up to €50,000 per year", "personio") is None
    assert from_field("40000-50000 EUR", "ashby") == SalarySpan(
        40000, 50000, "EUR", "field"
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
        "Salary: is £29,000 - depending on experience"
    ) == SalarySpan(29000, None, "GBP", "regex")


def test_description_up_to_states_a_ceiling_not_a_floor():
    # Real, confirmed across every ATS sampled so far (zoho pass: "Salary: Up to ₹28 LPA" was
    # extracting as min_annual=2,800,000 — a job that tops out at 28 LPA reported as starting
    # there). "up to $X" states a CEILING; SalarySpan has no way to represent "ceiling known,
    # floor unknown" (min_annual is a required int), so the only safe outcome is to decline
    # rather than silently invert the claim. This is what test_description_labeled_single_gbp
    # used to assert as a successful $29,000 floor extraction before this fix — the exact
    # phrasing from _LABELED's own docstring example.
    assert from_description("Salary: upto £29,000 - depending on experience") is None
    assert from_description("CTC: Up to ₹8 LPA") is None
    assert from_description("Pay up to £28/hr") is None
    # An actual range is unaffected — both bounds are already known regardless of the connector.
    assert from_description("Salary: up to £29,000-£35,000") == SalarySpan(
        29000, 35000, "GBP", "regex"
    )


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


def test_guard_trigger_word_after_a_range_is_caught():
    # code-review finding (PR #234): the guard's post-match window used to start at the match's
    # own start rather than its end, leaving almost no real lookahead — a trigger word trailing a
    # *range* (not a single figure) went uncaught. These are exactly the failing repros found.
    assert (
        from_description("We offer a $50,000 - $60,000 signing bonus for this role.")
        is None
    )
    assert from_description("Salary: $50,000 - $60,000 signing bonus included.") is None
    assert (
        from_description(
            "With over $50,000 - $60,000 in annual revenue and a blue-chip client base."
        )
        is None
    )


def test_guard_401k_benefit_list_does_not_reject_a_real_salary():
    # code-review finding, round 2: once the guard checked the after-window too, bare "401(k)"/
    # "hsa" started rejecting genuine salaries followed by an unrelated benefits list — real
    # corpus examples, both previously (wrongly) rejected before this was narrowed to
    # "contribution" alone.
    span = extract(
        None,
        "Pay range: $150,000 - $195,000 per year with bonus potential 401(k) Dental insurance",
        "workable",
    )
    assert span == SalarySpan(150000, 195000, "USD", "regex")
    span2 = extract(
        None,
        "Competitive salary of $71,700-$85,300 annually 401(k) Dental insurance Employee assistance",
        "workable",
    )
    assert span2 == SalarySpan(71700, 85300, "USD", "regex")


def test_guard_trigger_word_well_after_match_still_caught():
    # a trigger word further than the old ~2-char effective lookahead but within the real window.
    text = "Compensation: $60,000 - $70,000, paid as a referral bonus upon completion."
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


# --- Patterns added during the workday pass (docs/salary-extraction/workday.md) ----------------


def test_description_bare_starting_at_no_label_word():
    # real workday text: "starting at $20.00/hour!" with no preceding salary/pay/wage word.
    span = extract(
        None, "location starting at $20.00/hour! Bilingual in Spanish", "workday"
    )
    assert span == SalarySpan(41600, None, "USD", "regex")


def test_description_starting_at_pto_correctly_rejected():
    # "starting at" fires on PTO/benefit amounts far more often than on pay in real workday text
    # (28 days, 5%, 208 hours, 6:45am, ...) — all must stay None. These three are all small enough
    # that the plausibility floor alone rejects them (every real corpus example was checked, see
    # workday.md) — see test_description_starting_at_requires_strong_period_hint below for the
    # separate, dedicated guard added after adversarial testing found the floor alone isn't enough
    # once the amount is plausible-salary-sized (PR #235).
    assert (
        extract(
            None,
            "Generous retirement plan matching starting at 5% and increasing to 7% after five years",
            "workday",
        )
        is None
    )
    assert (
        extract(
            None, "Paid time off starting at 28 days per year, inclusive", "workday"
        )
        is None
    )
    assert (
        extract(None, "Generous PTO: starting at 208 hours annually", "workday") is None
    )


def test_description_starting_at_requires_strong_period_hint():
    # A bare "starting at $X" with a plausible-salary-sized number ISN'T caught by the floor —
    # real corporate-benefits phrasing (relocation/tuition/stipend amounts) reads identically to a
    # bare salary otherwise (confirmed by direct execution, not observed in the sampled corpus —
    # PR #235). A bare "annual(ly)" doesn't count as a strong hint either: one-time benefit amounts
    # are described that way just as often as real salaries are.
    for text in (
        "Relocation assistance starting at $15,000 for eligible candidates.",
        "Tuition reimbursement starting at $25,000 annually for full-time staff.",
        "Our device stipend program is starting at $30,000 for the fiscal year budget.",
    ):
        assert extract(None, text, "workday") is None, text
    # An explicit rate marker (/hour, per year, ...) is a strong enough hint to keep working.
    span = extract(
        None,
        "This role is starting at $85,000 per year based on experience.",
        "workday",
    )
    assert span == SalarySpan(85000, None, "USD", "regex")


def test_description_label_tolerates_short_filler_and_from_between():
    # "the pay range for Illinois is $X" / "hiring range for this position is $X" — real workday
    # phrasing puts a short qualifier between the label and the connector.
    span = extract(
        None,
        "The approximate pay range for Illinois is $73,047.47 - $109,571.20.",
        "workday",
    )
    assert span == SalarySpan(73047, 109571, "USD", "regex")
    span2 = extract(
        None, "Annual Base Salary: From $57,000 + based on qualifications", "workday"
    )
    assert span2 == SalarySpan(57000, None, "USD", "regex")


def test_description_sek_code_trails_each_number():
    # real workday (Swedish postings): the currency code repeats after each side of the range,
    # not once for the whole range like _BARE_RANGE_CODE's shape.
    span = extract(
        None,
        "The base salary range for this role is 518,910.00 SEK – 815,430.00 SEK on annual basis",
        "workday",
    )
    assert span == SalarySpan(518910, 815430, "SEK", "regex")


def test_description_bare_range_accepts_to_separator():
    # only _LABELED accepted "to" as a separator before; _BARE_RANGE (the no-label fallback)
    # didn't, so "$X to $Y" with no recognized label fell through entirely.
    span = extract(
        None, "compensation is expected between $92,700 to $112,000", "workday"
    )
    assert span == SalarySpan(92700, 112000, "USD", "regex")


def test_description_trailing_comma_not_absorbed_into_number():
    # "\d[\d,]*" allowed a dangling trailing comma (real sentence punctuation, not a thousands
    # separator) to be swept into the `hi` capture — "$100,000," matched hi="100,000," instead of
    # "100,000", shifting the match's end past the comma (greenhouse pass, real bug). The number
    # itself parsed fine either way (_num strips commas), but the shifted end broke period-hint
    # gap detection for the next bug below. Fixed to require the capture end in a real digit.
    span = extract(None, "Compensation: $90,000-$100,000, per year", "greenhouse")
    assert span == SalarySpan(90000, 100000, "USD", "regex")


def test_description_period_hint_ignores_unrelated_clause_after_comma():
    # Real bug (greenhouse pass): "base salary of $90,000-$100,000, plus weekly and monthly bonus
    # opportunities" read "monthly" — describing the separate BONUS, not the salary — as the
    # salary's own period and multiplied by 12 (-> $1.08M-$1.2M, only caught by luck because it
    # happened to exceed the plausibility ceiling). A comma between the number and the period word
    # now means "different clause, don't apply it" — the genuine, unmultiplied $90k-$100k stands.
    text = (
        "Earn a base salary of $90,000-$100,000, plus weekly and monthly bonus "
        "opportunities (typically averaging $1,000/month) and a $10,000 signing bonus."
    )
    span = extract(None, text, "greenhouse")
    assert span == SalarySpan(90000, 100000, "USD", "regex")


def test_description_period_hint_still_applies_with_no_intervening_comma():
    # Companion to the guard above: a period marker directly adjacent (no comma between it and
    # the number) must still apply normally — every genuine real-corpus case seen so far is this
    # shape ("$23/hr", "per hour", "$300 - $400 day").
    span = extract(None, "Pay range: $23 - $25/hr, based on experience", "greenhouse")
    assert span.min_annual == 23 * 2080
    assert span.max_annual == 25 * 2080


def test_description_period_hint_survives_comma_with_no_real_words():
    # A comma in the gap is fine as long as nothing but punctuation/digits/symbols follows it —
    # real workday bilingual posting: the same range restated in French-formatted numbers between
    # the English figure and its shared "per hour" marker. "per hour" still applies to the English
    # figure even though European-style commas from the French duplicate sit in between.
    text = (
        "Hiring Range / Échelle salariale à l'embauche : $17.60 - $25.90 / "
        "17,60$ - 25,90$ (per hour / de l'heure)"
    )
    span = extract(None, text, "workday")
    # _num() rounds to whole dollars before annualizing (pre-existing behavior) — 17.60 -> 18,
    # 25.90 -> 26.
    assert span.min_annual == 18 * 2080
    assert span.max_annual == 26 * 2080


def test_description_period_hint_word_before_number_with_no_comma():
    # A period word BEFORE the number, with real prose words in between and no comma at all, must
    # still apply — real workable text: "Competitive hourly rate of 19-21 USD". An earlier,
    # overly broad version of the comma-boundary guard (any letters in the gap, not just a
    # comma-introduced clause) wrongly rejected this and ~150 other genuine matches across
    # workable+workday before a full corpus diff caught it.
    span = extract(None, "Competitive hourly rate of 19-21 USD", "workable")
    assert span == SalarySpan(19 * 2080, 21 * 2080, "USD", "regex")


def test_description_period_hint_prefers_closest_not_first():
    # Real bug, smartrecruiters pass: _period_from_window used to take the FIRST period word
    # found when scanning the whole window left-to-right, not the CLOSEST one to the number.
    # "Shift: Day Salary Range: $44.00 - $57.00/hour" read "Day" (describing the WORK SHIFT type,
    # unrelated to pay) as the period instead of the genuine "/hour" that comes right after the
    # number, because "Day" happened to sit earlier in the combined window.
    text = "Every other weekend Shift: Day Salary Range: $44.00 - $57.00/hour (Final offer based on experience)"
    span = extract(None, text, "greenhouse")
    assert span == SalarySpan(round(44.00 * 2080), round(57.00 * 2080), "USD", "regex")


def test_description_period_hint_ignores_new_sentence_starting_at_the_number():
    # Companion regression to the fix above: pure "closest wins" isn't enough on its own — a
    # capitalized word starting a genuinely NEW, unrelated sentence right where the number ends
    # ("$25-35 Annual continuing education benefit...") must not out-rank a real period word that
    # comes a little earlier ("hourly rate: $25-35"). Sentence-initial capitalization (not
    # ALL-CAPS emphasis) is the distinguishing signal.
    text = "Competitive hourly rate: $25-35 Annual continuing education benefit $1250 Opportunities for physician interaction"
    span = extract(None, text, "greenhouse")
    assert span == SalarySpan(25 * 2080, 35 * 2080, "USD", "regex")


def test_description_period_hint_all_caps_emphasis_still_applies():
    # ALL-CAPS emphasis ("$22.50/HOUR!!") must not be mistaken for the sentence-initial
    # capitalization the guard above deprioritizes — real workday text.
    span = extract(
        None, "BENEFITS & SCHEDULING: $22.50/HOUR!! PAID WEEKLY!!", "greenhouse"
    )
    # _num() rounds to whole dollars before annualizing (pre-existing behavior) — 22.50 -> 22.
    assert span == SalarySpan(22 * 2080, None, "USD", "regex")


def test_description_em_dash_range_separator():
    # "... Range $X — $Y USD" (em-dash, not hyphen) is a dominant, templated pattern across many
    # unrelated greenhouse companies — almost certainly a shared compliance/HR tool, not one
    # company's own phrasing (confirmed: recurs verbatim across sunnyside, greenthumbindustries,
    # luminishealth, westernspecialtycontractors, powerx, blackbirdhealth in the real corpus).
    # Small-dollar occurrences of this same template ("Pay Range $17 — $17 USD") are a separate,
    # known, genuinely ambiguous gap — see docs/salary-extraction/greenhouse.md.
    span = extract(None, "Salary Range $40,000 — $105,000 USD", "greenhouse")
    assert span == SalarySpan(40000, 105000, "USD", "regex")


def test_description_between_and_anchored_phrase():
    # "between $X and $Y" — real, common greenhouse phrasing not covered by any dash/to separator
    # (charliehealth: "will be between $60,000 and $70,000 per year"; zetacharterschools: "is
    # between $90,000 and $125,000"). Anchored on the literal word "between" (not a generic
    # unlabeled "and") so it can't join two unrelated dollar mentions.
    span = extract(
        None,
        "The expected base pay for this role will be between $60,000 and $70,000 per year",
        "greenhouse",
    )
    assert span == SalarySpan(60000, 70000, "USD", "regex")


def test_description_between_guards_unrelated_dollar_mentions():
    # "between" alone isn't a strong enough anchor if the two amounts are genuinely unrelated —
    # this is why _BARE_BETWEEN requires the literal word right before the first number, not a
    # generic "and"-joined bare range: two unrelated dollar figures almost never both follow the
    # word "between" directly.
    text = "You will receive $50,000 in RSUs and $10,000 signing bonus over four years."
    assert extract(None, text, "greenhouse") is None


def test_description_between_does_not_override_a_headline_range():
    # Real regression, caught by a full corpus diff against workday's already-merged coverage:
    # when a description states BOTH a full "range is $X to $Y" AND a narrower "new hires usually
    # start between $A and $B", the between-clause must not win over the more representative
    # headline figure just because it's a different pattern. _BARE_BETWEEN runs last (lowest
    # priority) among the bare patterns for exactly this reason.
    text = (
        "Compensation: Expected range is $108,300 to $140,800. New hires usually start "
        "between $108,300 and $130,000, depending on experience and internal equity."
    )
    span = extract(None, text, "workday")
    assert span == SalarySpan(108300, 140800, "USD", "regex")


def test_description_bare_hourly_no_label_at_all():
    # Real, common on smartrecruiters' retail/logistics/care-work postings: a bare "$X/hour" or
    # "$X per day" with no salary/pay word or connector anywhere nearby.
    span = extract(
        None,
        "Support Workers in Nottinghamshire £8.72 per hour (As well as an hourly rate of...)",
        "smartrecruiters",
    )
    # _num() rounds to whole dollars before annualizing (pre-existing behavior) — 8.72 -> 9.
    assert span == SalarySpan(9 * 2080, None, "GBP", "regex")


def test_description_bare_daily_no_label_at_all():
    span = extract(
        None,
        "Registration Clerk: $ 371 per day Election Official duties as assigned",
        "smartrecruiters",
    )
    assert span == SalarySpan(371 * 260, None, "USD", "regex")


def test_description_bare_hourly_ambiguous_multiple_rates_stays_none():
    # Multiple genuinely different bare hourly rates for different roles in one description must
    # still resolve to None via the shared ambiguity machinery, not arbitrarily pick one.
    text = "Registration Clerk: $ 371 per day Election Official: $ 350 per day Poll Supervisor: $ 400 per day"
    assert extract(None, text, "smartrecruiters") is None


def test_description_bare_hourly_excludes_shift_differential_add_on():
    # Real, confirmed false positive found via a full corpus diff against 3 already-merged ATSes:
    # a shift-differential list ("+$4.50/hr -> Mon-Thu Nights +$9.00/hr -> Fri-Sun Nights") states
    # several genuinely different ADD-ON figures, not the base wage — when the plausibility floor
    # happened to filter out all but one of them, the ambiguity that should have blocked this got
    # masked, and the sole survivor was wrongly reported as the base rate. A leading "+" is a
    # reliable, structural signal the figure is an add-on, not the rate itself.
    text = (
        "Earn More with Shift Differentials Maximize your earnings with additional hourly "
        "pay: +$4.50/hr -> Monday-Thursday Nights +$9.00/hr -> Friday-Sunday Nights +$4.50/hr "
        "-> Saturday-Sunday Days What You'll Do: The Telehealth Psychiatrist is responsible"
    )
    assert extract(None, text, "greenhouse") is None


def test_description_bare_hourly_base_rate_not_excluded_by_nearby_plus_differential():
    # Companion to the guard above: a genuine, non-"+"-prefixed base rate stated near a "+$X/hr"
    # differential must still extract correctly — only the "+"-prefixed figure is excluded.
    span = extract(None, "$21/hr + $0.50 shift differential + full benefits", "workday")
    assert span == SalarySpan(21 * 2080, None, "USD", "regex")


def test_description_ph_hourly_shorthand_glued_to_number():
    # Real, common on one UK recruitment agency's postings (zoho pass): British informal "p/h"
    # glued directly onto the number with no separating character ("£21.50p/h") — no word
    # boundary exists between the trailing digit and "p" (both are word characters), unlike the
    # "/hour" fix which could anchor on the "/". Requires _PERIOD_HINT's "p/h" alternative to have
    # no leading \b. The real corpus phrasing is "Pay up to £21.50p/h" — connector swapped to
    # "is" here (real "up to" phrasing now correctly declines instead, see
    # test_description_up_to_states_a_ceiling_not_a_floor) so this test still isolates the p/h
    # mechanism itself rather than being masked by that later, unrelated fix.
    span = extract(None, "Pay is £21.50p/h, hours M-F 9-5.", "zoho")
    assert span == SalarySpan(round(21.50) * 2080, None, "GBP", "regex")


def test_description_ph_hourly_shorthand_with_space():
    span = extract(None, "Pay is £20.00 p/h, M-F 8.30-4.30", "zoho")
    assert span == SalarySpan(20 * 2080, None, "GBP", "regex")


def test_description_paying_verb_form_of_pay_label():
    # Real, 175 corpus misses (zoho pass): "paying" (a verb conjugation) wasn't recognized as a
    # variant of the "pay" label word, so "is paying up to $X" fell through entirely. The real
    # corpus phrasing also said "up to" ("...is paying up to £9.00p/h..."), which now correctly
    # declines instead (see test_description_up_to_states_a_ceiling_not_a_floor) — "up to"
    # dropped here so this test still isolates the "paying" mechanism itself.
    span = extract(
        None,
        "The Kitchen Porter job is paying £9.00p/h. The working hours",
        "zoho",
    )
    assert span == SalarySpan(9 * 2080, None, "GBP", "regex")


def test_description_a_year_period_marker():
    # Real, common phrasing beyond one company's template (zoho pass): "$X a year" (not "per
    # year"/"/year"/"annual(ly)") as a bare period marker.
    span = extract(
        None, "Salary £25,000 - £30,000 a year, full benefits included", "zoho"
    )
    assert span == SalarySpan(25000, 30000, "GBP", "regex")


def test_description_compound_connector_of_up_to():
    # Real, zoho pass: "of" and "up to" recognized individually but not as a two-word sequence —
    # "salary of up to $X" fell through entirely before that fix landed. It's since been
    # superseded by a separate, later fix (test_description_up_to_states_a_ceiling_not_a_floor):
    # a bare "up to $X" states a ceiling, not a floor, and now correctly declines rather than
    # misreporting $22,000 as the minimum. The connector is still recognized — proven by the
    # real range case below, where recognizing "of up to" is what lets the full, genuine range
    # extract instead of falling through entirely.
    single = extract(
        None,
        "the nursery offers a competitive salary of up to £22,000 per year, subject to quals",
        "zoho",
    )
    assert single is None
    ranged = extract(
        None,
        "the nursery offers a competitive salary of up to £22,000-£25,000 per year",
        "zoho",
    )
    assert ranged == SalarySpan(22000, 25000, "GBP", "regex")


def test_description_compound_connector_is_up_to():
    # Same story as test_description_compound_connector_of_up_to above: the connector is
    # recognized, but a bare single value now correctly declines as a ceiling rather than a floor.
    single = extract(
        None,
        "This is for a 42.5 hr working week, salary is up to £34,255 depending on experience",
        "zoho",
    )
    assert single is None


def test_description_up_to_no_longer_creates_false_ambiguity():
    # Real, understood side effect of the ceiling-vs-floor fix (found reviewing PR #238): before
    # it, "of up to $92,400" was wrongly treated as a second, competing FLOOR claim alongside the
    # genuine "$84,000" base salary, and the ambiguity guard correctly declined as a side effect
    # of that confusion (not because it understood "up to" is a different kind of claim). Now
    # that a bare "up to" figure is correctly excluded from floor-assignment entirely, only the
    # real, clearly-stated $84,000 base salary remains a candidate, and it extracts cleanly.
    text = (
        "The non-negotiable starting salary for this position is $84,000. Candidates "
        "exceeding the minimum requirements outlined above may be provided a higher "
        "starting salary of up to $92,400. All salary offers are non-negotiable."
    )
    assert extract(None, text, "workday") == SalarySpan(84000, None, "USD", "regex")


def test_description_level_bands_envelope_across_multiple_bands():
    # Real, near-single-company template (spacex, confirmed 494/495 corpus occurrences): several
    # explicitly labeled "Level N: $X - $Y" bands for one role. Every generic pattern would see
    # these as multiple genuinely different numbers and correctly refuse to guess — but each band
    # is explicitly part of the SAME role's stated range, so the envelope (lowest floor to highest
    # ceiling) is real, stated information, not a guess.
    text = (
        "COMPENSATION AND BENEFITS: Pay Range: Level 1: $105,000.00 - $122,500.00 "
        "Level 2: $120,000.00 - $150,000.00 Your actual level and base salary will be "
        "determined on a case-by-case basis."
    )
    span = extract(None, text, "greenhouse")
    assert span == SalarySpan(105000, 150000, "USD", "regex")


def test_description_level_bands_apply_period_hint_per_band():
    # Each band can carry its own period marker ("Level 1: $23.00 - $27.00/hour Level 2: ...").
    text = (
        "Pay range: Propulsion Technician/Level 1: $23.00 - $27.00/hour "
        "Propulsion Technician/Level 2: $26.00 - $32.50/hour "
        "Propulsion Technician/Level 3: $31.00 - $38.00/hour"
    )
    span = extract(None, text, "greenhouse")
    assert span == SalarySpan(23 * 2080, 38 * 2080, "USD", "regex")


def test_description_level_bands_without_period_hint_stay_unresolved():
    # Companion to the above: the same "Level N: $X-$Y" shape with NO period marker anywhere and
    # small (implausible-as-annual) numbers correctly stays unresolved rather than guessing hourly
    # — the same genuine, already-established ambiguity as an unmarked "Pay Range: $17 — $17 USD"
    # elsewhere in this file: the plausibility floor rejects $22-$37 as an annual figure, and
    # nothing here confirms it's hourly instead.
    text = (
        "COMPENSATION AND BENEFITS: Pay Range: Level 1: $22.00 - $26.50 "
        "Level 2: $25.50 - $31.00 Level 3: $29.50 - $37.00 Your actual level and base "
        "salary will be determined on a case-by-case basis."
    )
    assert extract(None, text, "greenhouse") is None


def test_description_labeled_currency_code_requires_word_boundary():
    # _LABELED's per-side currency-code tolerance matched as a prefix of an unrelated word
    # (code review finding, PR #235) — "$50,000 CADillac" read currency='CAD' off "CAD" inside
    # "CADillac" with no word boundary enforced. Must fall back to symbol-guessed USD instead.
    span = extract(
        None, "Compensation: $50,000 CADillac Escalade included as a perk", "workday"
    )
    assert span == SalarySpan(50000, None, "USD", "regex")


def test_guard_sign_on_bonus_variant():
    # "Sign-On Bonus" (hyphenated variant) wasn't covered by the "signing bonus" trigger.
    text = "$1,000 Sign-On Bonus* About Fairfield If you're driven and seek a collaborative workplace"
    assert extract(None, text, "workday") is None


def test_guard_referral_program_not_just_referral_bonus():
    text = (
        "Generous referral program ranging from $500-$2500, depending on business need"
    )
    assert extract(None, text, "workday") is None
