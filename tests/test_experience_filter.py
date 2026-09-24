import sqlite3

from headstart.experience_filter import CEILINGS, MIGRATION_SQL, clause, column, flags


def test_flags_keep_unknown_experience_eligible():
    assert flags(None) == {column(ceiling): True for ceiling in CEILINGS}


def test_flags_match_each_offered_ceiling_independently():
    assert flags(5) == {
        "experience_at_most_0": False,
        "experience_at_most_2": False,
        "experience_at_most_5": True,
        "experience_at_most_10": True,
    }


def test_clause_uses_a_flag_only_for_an_offered_ceiling_on_a_migrated_table():
    assert clause(5, True) == "experience_at_most_5 = true"
    assert clause(3, True) == "(min_years <= 3 OR min_years IS NULL)"
    assert clause(5, False) == "(min_years <= 5 OR min_years IS NULL)"


def test_migration_sql_agrees_with_the_python_flags():
    """Old tables are migrated with the SQL and new rows get `flags`; one verdict either way."""
    db = sqlite3.connect(":memory:")
    for min_years in (None, 0, 1, 2, 5, 9, 10, 11):
        for name, sql in MIGRATION_SQL.items():
            (verdict,) = db.execute(
                f"SELECT {sql} FROM (SELECT ? AS min_years)", (min_years,)
            ).fetchone()
            assert bool(verdict) is flags(min_years)[name], (min_years, name)
