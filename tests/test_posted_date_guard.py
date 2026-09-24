import sqlite3

from headstart import posted_date_guard


def test_clause_prefers_the_flag_and_falls_back_to_the_shape_guard():
    assert posted_date_guard.clause(True) == "posted_at_comparable = true"
    assert posted_date_guard.clause(False) == "posted_at LIKE '____-__-__%'"


def test_only_an_iso_shaped_date_is_comparable():
    assert posted_date_guard.is_comparable("2026-09-21T00:00:00Z") is True
    assert posted_date_guard.is_comparable("21-Sep-2026") is False
    assert posted_date_guard.is_comparable(None) is False


def test_migration_sql_agrees_with_the_python_flag():
    """Old tables are migrated with the SQL and new rows get `flags`; one verdict either way."""
    db = sqlite3.connect(":memory:")
    for value in (
        None,
        "",
        "2026-09",
        "2026-09-21",
        "2026-09-21T00:00:00Z",
        "21-Sep-2026",
    ):
        (sql,) = db.execute(
            f"SELECT {posted_date_guard.MIGRATION_SQL['posted_at_comparable']} "
            "FROM (SELECT ? AS posted_at)",
            (value,),
        ).fetchone()
        assert bool(sql) is posted_date_guard.flags(value)["posted_at_comparable"], (
            value
        )
