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
        "lower(employment_type) LIKE '%\\_ft%' ESCAPE '\\' OR "
        "lower(employment_type) LIKE '%temps plein%' OR "
        "lower(employment_type) LIKE '%tiempo completo%' OR "
        "lower(employment_type) LIKE '%vollzeit%' OR "
        "lower(employment_type) LIKE '%全职%' OR "
        "(lower(employment_type) LIKE '%permanent%' AND "
        "lower(employment_type) NOT LIKE '%part%') OR "
        "(lower(employment_type) LIKE '%regular%' AND "
        "lower(employment_type) NOT LIKE '%part%') OR "
        "(lower(employment_type) LIKE '%cdi%' AND "
        "lower(employment_type) NOT LIKE '%part%') OR "
        "(lower(employment_type) LIKE '%fte%' AND "
        "lower(employment_type) NOT LIKE '%after%'))"
    )
    assert RULES["internship"].raw_clause() == (
        "((lower(employment_type) LIKE '%intern%' OR "
        "lower(employment_type) LIKE '%co-op%' OR "
        "lower(employment_type) LIKE '%coop%') AND "
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
        # ADR-0337's terms, and the LIKE wildcard an unescaped "_ft" would be.
        "SALARIED_FT",
        "HOURLY_PT",
        "Software",
        "Regular",
        "Regular Part-Time",
        "Second Shift (afternoon)",
        "fulltime_fixed_term",
        "Co-op",
        "Tiempo completo",
    )
    for value in values:
        for rule in RULES.values():
            (sql,) = db.execute(
                f"SELECT {rule.raw_clause()} FROM (SELECT ? AS employment_type)",
                (value,),
            ).fetchone()
            assert bool(sql) is rule.matches(value), (value, rule.column)


def test_raw_values_that_read_as_none_are_mapped_where_unambiguous():
    """ADR-0337: the served table's top raw values that set no flag."""
    full = {
        "is_full_time": True,
        "is_part_time": False,
        "is_contract": False,
        "is_internship": False,
    }
    for value in (
        "SALARIED_FT",
        "HOURLY_FT",
        "Regular",
        "INTL Regular FT",
        "CDI",
        "FTE",
        "Tiempo completo",
        "Temps plein",
        "Vollzeit",
        "全职",
    ):
        assert flags(value) == full, value
    assert flags("HOURLY_PT")["is_part_time"] is True
    assert flags("Regular Part-Time")["is_full_time"] is False
    assert flags("Second Shift (afternoon)")["is_full_time"] is False
    assert flags("Fixed Term")["is_contract"] is True
    assert flags("fulltime_fixed_term") == {**full, "is_contract": True}
    assert flags("Co-op")["is_internship"] is True
    # Left unread: ambiguous at the source.
    for value in ("OTHER", "Temporary", "Employee", "F"):
        assert not any(flags(value).values()), value


def test_an_underscore_term_is_escaped_in_its_like_pattern():
    assert "LIKE '%\\_ft%' ESCAPE '\\'" in RULES["full-time"].raw_clause()


def test_clause_prefers_the_flag_and_ignores_an_unknown_value():
    assert clause("contract", True) == "is_contract = true"
    assert clause("contract", False) == RULES["contract"].raw_clause()
    assert clause("bogus", True) is None
    assert clause(None, False) is None
