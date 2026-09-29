"""Tests for ``headstart.jobs.location``: tidying a stated location and reading an unstated one.

Every string below is a location or description head served on 2026-09-29 (audited table v18), or
a minimal paraphrase of one (``experiment/location-and-extraction-fixes-2026-09-29/
fl09_location_content``).
"""

from __future__ import annotations

import pytest

from headstart.jobs.location import from_description, is_place, tidy

# --- tidy: template tokens and repeats --------------------------------------------------------


def test_blank_template_tokens_are_dropped():
    # greenhouse's own template, 17 served rows
    assert tidy("BLANK,BLANK,Multiple Locations") == "Multiple Locations"


def test_blank_alone_names_nothing():
    assert tidy("BLANK,BLANK") is None


def test_junk_separators_alone_name_nothing():
    assert tidy(",; ,; ,; ,") is None


@pytest.mark.parametrize(
    ("stated", "tidied"),
    [
        ("Mumbai, Mumbai, India", "Mumbai, India"),
        ("Singapore, Singapore, Singapore", "Singapore"),
        ("Hong Kong, Hong Kong", "Hong Kong"),
        ("New York, NEW YORK, United States", "New York, United States"),
        ("Auckland, Auckland, New Zealand", "Auckland, New Zealand"),
        ("ChengDu, Sichuan, Sichuan", "ChengDu, Sichuan"),
    ],
)
def test_a_repeated_neighbouring_token_is_said_once(stated, tidied):
    assert tidy(stated) == tidied


def test_the_same_place_listed_twice_is_listed_once():
    assert tidy("Pune, MH, India; Pune, MH, India") == "Pune, MH, India"
    assert tidy("Noida; Noida; Dehradun; Noida") == "Noida; Dehradun"


def test_a_country_shared_by_two_places_is_kept_on_both():
    stated = "Boston, Massachusetts, USA; Irvine, California, USA"
    assert tidy(stated) == stated


def test_a_token_repeated_across_separated_places_is_kept():
    # not neighbours: a place list, not a stutter
    stated = "Bengaluru, Karnataka, India, Pune, Maharashtra, India"
    assert tidy(stated) == stated


def test_separators_are_kept_as_written():
    assert tidy("Chicago, IL;New York, NY") == "Chicago, IL;New York, NY"
    assert tidy("Remote; Remote; United States of America") == (
        "Remote; United States of America"
    )


@pytest.mark.parametrize(
    "stated",
    [
        "Noida,UP,India",
        "Santa Clara,CA; United States of America",
        "Avenida das Nações Unidas, 12901,11° andar São Paulo",
    ],
)
def test_a_location_with_nothing_to_drop_keeps_its_spacing(stated):
    # 4,000 served rows are written without a space after the comma: not a defect to rewrite
    assert tidy(stated) == stated


def test_dropping_a_repeat_keeps_the_separators_around_what_stays():
    assert tidy("Mumbai,Mumbai,India") == "Mumbai,India"
    assert tidy("Noida,UP,UP, India") == "Noida,UP, India"
    assert tidy("Santa Clara,CA,CA; Austin,TX") == "Santa Clara,CA; Austin,TX"


@pytest.mark.parametrize(
    "stated", ["Remote", "Hybrid", "N/A", "NA", "3 Locations", "Berlin, Germany"]
)
def test_values_with_nothing_to_tidy_are_returned_as_they_are(stated):
    assert tidy(stated) == stated


def test_none_and_empty_stay_none():
    assert tidy(None) is None
    assert tidy("") is None
    assert tidy("  ") is None


# --- is_place ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Charlotte, NC",
        "Kuala Lumpur, Malaysia",
        "Columbia, South Carolina, United States",
        "Maryland, Columbia",
        "Barcelona, Spain",
        "Hyderabad",
    ],
)
def test_places_are_places(text):
    assert is_place(text)


@pytest.mark.parametrize(
    "text",
    [
        "EA SPORTS NHL",
        "Product Manager",
        "215808",
        "Python, SQL",
        "the ideal candidate will be located in Panama City or Guatemala",
        "US or",
        "PAN",
        "",
    ],
)
def test_other_text_is_not(text):
    assert not is_place(text)


# --- from_description: an explicit Location line ----------------------------------------------


def test_reads_a_location_line_up_to_the_next_label():
    head = "Type: Contract Exp: 4-6 Years Location: Chennai, Hyderabad, Pune Mode: Hybrid JD We need"
    assert from_description(head) == "Chennai, Hyderabad, Pune"


def test_reads_a_city_and_a_state_code():
    head = "Location: Melbourne, FL Clearance Requirement: Top Secret Employment type: Full Time"
    assert from_description(head) == "Melbourne, FL"


def test_a_place_and_the_prose_after_it():
    head = "Location: Hyderabad Employment Type: Full-Time Work Mode: Work from Office"
    assert from_description(head) == "Hyderabad"


def test_a_stated_country_is_a_place():
    assert from_description("Location: India Experience: 5+ Years") == "India"
    assert from_description("Location: US or Canada Salary: 100k") == "US or Canada"


def test_a_direction_is_part_of_a_place():
    assert from_description(
        "Location: Andheri East, Mumbai About the Company: Bit"
    ) == ("Andheri East, Mumbai")


def test_the_value_is_tidied():
    assert from_description("Location: Dublin, Ireland, Ireland Type: Full-time") == (
        "Dublin, Ireland"
    )


def test_a_trailing_workplace_type_is_not_part_of_the_place():
    assert from_description("Location: Noida (Onsite) Experience: 3-5 years") == "Noida"
    assert from_description("Job Location: Pune / Hybrid Notice: 30 days") == "Pune"


def test_a_dash_after_the_place_ends_it():
    head = "Location: Chennai, Hyderabad, Pune, and Bangalore — purely onsite (no remote or hybrid option). JD"
    assert from_description(head) == "Chennai, Hyderabad, Pune, and Bangalore"


def test_a_country_and_its_parenthesis_do_not_leak_prose():
    head = "Location: Colombia (Remote; U.S. time zone overlap preferred) Type: Paid Internship"
    assert from_description(head) == "Colombia"


@pytest.mark.parametrize(
    "label",
    ["Location:", "Locations :", "Job Location:", "Work Location:", "Location(s):"],
)
def test_the_label_spellings(label):
    assert (
        from_description(f"About the role {label} Pune Experience: 2 years") == "Pune"
    )


@pytest.mark.parametrize(
    "head",
    [
        "Location: Remote",
        "Location: Hybrid Experience: 5 years",
        "Work Location Type: Hybrid Experience: 5 years",
        "Location: TBD",
        "Location: Our headquarters are in a vibrant city",
        "We are based in Pune and hiring",
        "",
    ],
)
def test_no_stated_place_reads_none(head):
    assert from_description(head) is None


@pytest.mark.parametrize(
    "head",
    [
        # a bare name several countries share: the description does not say which
        "Location: Melbourne Job Summary: We are looking for",
        "Location: Perth Reporting to: Installation Manager",
        # a bare code is not a place
        "Location: PAN Years of Experience: 5",
        "Location: US or Salary: 100k",
        "Location: IT Department: Engineering",
        # a list cut at a name the gazetteers do not know would state only part of it
        "Location: Japan, Guam, S. Korea, Singapore Security Clearance: Secret",
        # an adjective, not a place
        "Location: Indian Head, MD Type: Full-time",
    ],
)
def test_an_ambiguous_or_partial_reading_is_none(head):
    assert from_description(head) is None


def test_only_the_head_is_read():
    body = "Lorem ipsum " * 300 + "Location: Pune Experience: 2 years"
    assert from_description(body) is None


def test_the_first_labelled_line_decides():
    head = "Location: Remote Experience: 5 years. Office Location: Pune"
    assert from_description(head) is None


def test_none_description():
    assert from_description(None) is None
