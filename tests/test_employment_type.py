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
        "(lower(employment_type) LIKE '%permanent%' AND "
        "lower(employment_type) NOT LIKE '%part%'))"
    )
    assert FILTERS["internship"].raw_clause() == (
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
    )
    for value in values:
        for rule in FILTERS.values():
            (sql,) = db.execute(
                f"SELECT {rule.raw_clause()} FROM (SELECT ? AS employment_type)",
                (value,),
            ).fetchone()
            assert bool(sql) is rule.matches(value), (value, rule.column)
