from headstart.search_filters.employment_type_filter import RULES, clause, flags


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
    assert flags(None) == dict.fromkeys((rule.column for rule in RULES.values()), False)


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
    assert RULES["full-time"].raw_clause() == (
        "(lower(employment_type) LIKE '%full%' OR "
        "(lower(employment_type) LIKE '%permanent%' AND "
        "lower(employment_type) NOT LIKE '%part%') OR "
        "(lower(employment_type) LIKE '%regular%' AND "
        "lower(employment_type) NOT LIKE '%part%') OR "
        "lower(employment_type) IN ('salaried_ft', 'hourly_ft', 'f', 'ft', "
        "'fte', 'cdi', 'tiempo completo', '全职'))"
    )
    assert RULES["internship"].raw_clause() == (
        "(lower(employment_type) LIKE '%intern%' AND "
        "lower(employment_type) NOT LIKE '%international%')"
    )


def test_permanent_part_time_is_not_full_time():
    # Recruitee's raw code and Personio's joined "employmentType / schedule" both say
    # "permanent" for a part-time job; "permanent" is contract duration, not hours.
    for value in ("parttime_permanent", "permanent / part-time", "Permanent Part-Time"):
        assert flags(value)["is_full_time"] is False, value
        assert flags(value)["is_part_time"] is True, value
    # "full" still counts on its own, so a job open to either hours stays in both.
    assert flags("permanent / full-or-part-time")["is_full_time"] is True
    assert flags("Full-Time (Partly Remote)")["is_full_time"] is True
    assert flags("Permanent")["is_full_time"] is True


def test_raw_clauses_agree_with_the_python_flags():
    """`index sync` materializes old tables with the SQL clause and new rows with `flags`;
    the two must give one verdict. SQLite's `lower`/`LIKE` stand in for LanceDB's."""
    import sqlite3

    db = sqlite3.connect(":memory:")
    values = (
        "parttime_permanent",
        "fulltime_permanent",
        "permanent / part-time",
        "permanent / full-or-part-time",
        "Permanent",
        "International Internship",
        "Part-time contract",
        "",
        "Regular",
        "Regular Part-time",
        "SALARIED_FT",
        "Fte",
        "Fte-something",
        "Freelance",
        "Temporary",
        "fulltime_fixed_term",
        "HOURLY_PT",
        "全职",
    )
    for value in values:
        for rule in RULES.values():
            (sql,) = db.execute(
                f"SELECT {rule.raw_clause()} FROM (SELECT ? AS employment_type)",
                (value,),
            ).fetchone()
            assert bool(sql) is rule.matches(value), (value, rule.column)


def test_clause_prefers_the_flag_and_ignores_an_unknown_value():
    assert clause("contract", True) == "is_contract = true"
    assert clause("contract", False) == RULES["contract"].raw_clause()
    assert clause("bogus", True) is None
    assert clause(None, False) is None


def test_values_that_read_as_full_time_but_carry_no_full_or_permanent():
    """Measured 2026-09-29 on the served table: 10,790 flagless rows held these (Radancy, TikTok,
    ByteDance, Rippling, Applied Materials)."""
    for value in (
        "Regular",
        "Regular Employee",
        "Regular - Permanent",
        "SALARIED_FT",
        "HOURLY_FT",
        "F",
        "FT",
        "fte",
        "CDI",
        "Tiempo completo",
        "全职",
    ):
        assert flags(value)["is_full_time"] is True, value
    assert flags("Regular Part-time")["is_full_time"] is False
    assert flags("Regular Part-time")["is_part_time"] is True


def test_short_codes_match_only_as_the_whole_value():
    for value in (
        "Freelance",
        "Software",
        "Left",
        "Fte-something",
        "Cdi Lyon",
        "Effort",
    ):
        assert flags(value)["is_full_time"] is False, value
    assert flags("Freelance")["is_contract"] is True


def test_temporary_and_fixed_term_count_as_contract():
    for value in (
        "Temporary",
        "TEMPORARY",
        "Fixed Term",
        "Fixed-Term",
        "fulltime_fixed_term",
    ):
        assert flags(value)["is_contract"] is True, value
    # hours and duration are independent: a fixed-term full-time job is both
    assert flags("fulltime_fixed_term")["is_full_time"] is True
    assert flags("parttime_fixed_term")["is_part_time"] is True
    assert flags("Permanent")["is_contract"] is False


def test_rippling_part_time_codes():
    assert flags("SALARIED_PT")["is_part_time"] is True
    assert flags("HOURLY_PT")["is_part_time"] is True


def test_a_title_says_internship_when_the_employment_type_does_not():
    """Workday says "Full time" for an intern, Greenhouse says nothing: 9,354 of 11,993
    intern-titled rows were unflagged (2026-09-29)."""
    assert flags(None, "Software Engineering Intern")["is_internship"] is True
    assert flags("Full time", "2027 Summer Internship Program - Engineering")[
        "is_internship"
    ]
    assert (
        flags("Full time", "Machine Learning Interns (Summer)")["is_internship"] is True
    )
    # the ATS's own word still counts, with or without a title
    assert flags("Intern")["is_internship"] is True
    assert flags("Intern", "Engineer")["is_internship"] is True


def test_the_title_cue_is_a_whole_word():
    for title in (
        "International Sales Engineer",
        "Internal Tools Engineer",
        "Internet Architect",
        "Internist",
        "Interning Manager",
        None,
        "",
    ):
        assert flags(None, title)["is_internship"] is False, title
    # the title never sets the hours flags
    assert flags(None, "Software Intern")["is_full_time"] is False


def test_the_title_cue_does_not_reach_the_sql_fallback():
    """A table that predates the columns keeps the raw-value clause; the title is not a column
    the fallback can pattern-match, and the materialized flag is what carries the cue."""
    assert "title" not in RULES["internship"].raw_clause()
