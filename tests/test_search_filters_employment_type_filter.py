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
        "lower(employment_type) NOT LIKE '%after%') OR "
        "lower(employment_type) IN ('f', 'ft'))"
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
    # Left unread: ambiguous at the source. ("Temporary" and "F" were here until ADR-0340 read
    # them: Temporary as contract, F as a whole value.)
    for value in ("OTHER", "Employee"):
        assert not any(flags(value).values()), value


def test_an_underscore_term_is_escaped_in_its_like_pattern():
    assert "LIKE '%\\_ft%' ESCAPE '\\'" in RULES["full-time"].raw_clause()


def test_clause_prefers_the_flag_and_ignores_an_unknown_value():
    assert clause("contract", True) == "is_contract = true"
    assert clause("contract", False) == RULES["contract"].raw_clause()
    assert clause("bogus", True) is None
    assert clause(None, False) is None


def test_a_single_letter_code_counts_only_as_the_whole_value():
    """Radancy's "F" (Applied Materials, 1,009 rows) and a bare "FT" (ADP): a substring rule
    would read "soft", "left" and "effort" as full-time."""
    assert flags("F")["is_full_time"] is True
    assert flags("ft")["is_full_time"] is True
    for value in ("Freelance", "Software", "Left", "Effort", "Soft skills"):
        assert RULES["full-time"].matches(value) is False, value


def test_temporary_counts_as_contract():
    for value in ("Temporary", "TEMPORARY", "Temporary Employee"):
        assert flags(value)["is_contract"] is True, value
    assert flags("Permanent")["is_contract"] is False


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


def test_the_index_hands_the_title_to_the_flags():
    """`_served_meta` is the one place every written row's flags come from (new rows, refreshed
    rows, and the comparison `_refresh_metadata` rewrites a stale row on): the title must reach
    `flags`, or an intern-titled row would be rewritten with the old verdict on every run."""
    from headstart.ingest.index import _served_meta

    row = _served_meta(
        {"employment_type": "Full time", "title": "Software Engineering Intern"}, None
    )
    assert row["is_internship"] is True and row["is_full_time"] is True
    plain = _served_meta({"employment_type": "Full time", "title": "Engineer"}, None)
    assert plain["is_internship"] is False


def test_no_flag_is_a_response_field():
    """The flags are index columns, not API fields: `/search` and `/job` project explicit lists."""
    from headstart.serving.job_search import JOB_DETAIL_COLUMNS, RESULT_COLUMNS

    served = set(RESULT_COLUMNS) | set(JOB_DETAIL_COLUMNS)
    assert served.isdisjoint(rule.column for rule in RULES.values())
