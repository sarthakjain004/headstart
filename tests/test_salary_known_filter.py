import sqlite3

from headstart import salary_known_filter


def test_clause_prefers_the_flag_and_falls_back_to_the_null_check():
    assert salary_known_filter.clause(True) == "salary_known = true"
    assert salary_known_filter.clause(False) == "min_salary_annual IS NOT NULL"


def test_migration_sql_agrees_with_the_python_flag():
    """Old tables are migrated with the SQL and new rows get `flags`; one verdict either way."""
    db = sqlite3.connect(":memory:")
    for value in (None, 0, 120_000):
        (sql,) = db.execute(
            f"SELECT {salary_known_filter.MIGRATION_SQL['salary_known']} "
            "FROM (SELECT ? AS min_salary_annual)",
            (value,),
        ).fetchone()
        assert bool(sql) is salary_known_filter.flags(value)["salary_known"], value
