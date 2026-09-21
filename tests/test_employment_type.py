from headstart.employment_type import FILTERS, flags


def test_flags_preserve_the_existing_overlapping_substring_rules():
    assert flags("Permanent / Full-Time") == {
        "is_full_time": True,
        "is_part_time": False,
        "is_contract": False,
        "is_internship": False,
    }
    assert flags("Part-time contract") == {
        "is_full_time": False,
        "is_part_time": True,
        "is_contract": True,
        "is_internship": False,
    }
    assert flags(None) == dict.fromkeys(
        (rule.column for rule in FILTERS.values()), False
    )


def test_international_is_not_an_internship():
    assert flags("International Full Time Employee") == {
        "is_full_time": True,
        "is_part_time": False,
        "is_contract": False,
        "is_internship": False,
    }
    # This is the accepted cost of the old no-lookaround rule, preserved exactly by the flag.
    assert flags("International Internship")["is_internship"] is False


def test_raw_clauses_keep_the_filter_contract():
    assert FILTERS["full-time"].raw_clause() == (
        "(lower(employment_type) LIKE '%full%' OR "
        "lower(employment_type) LIKE '%permanent%')"
    )
    assert FILTERS["internship"].raw_clause() == (
        "(lower(employment_type) LIKE '%intern%' AND "
        "lower(employment_type) NOT LIKE '%international%')"
    )
