import json
import sqlite3
from pathlib import Path

from headstart.search_filters import confident_non_tech_filter as f

ROOT = Path(__file__).resolve().parent.parent


def test_the_threshold_is_the_measured_one():
    assert f.PROBABILITY == 0.9


def test_the_threshold_was_measured_on_the_head_the_repo_ships():
    """A retrained head has other probabilities. This fails on the head's version bump until the
    threshold has been measured again on it (ADR-0349) and the constant moved with it."""
    manifest = json.loads(
        (ROOT / "config" / "role_family_classifier" / "manifest.json").read_text()
    )
    assert f.MEASURED_ON_HEAD_VERSION == manifest["version"]


def test_only_a_confident_non_tech_call_is_confident():
    assert f.is_confident("non-tech", 0.9)  # the threshold is inclusive
    assert f.is_confident("non-tech", 0.999)
    assert not f.is_confident("non-tech", 0.8999)
    assert not f.is_confident("software-engineering", 0.999)
    assert not f.is_confident("unclassified-tech", 0.999)


def test_a_new_row_is_not_confident_until_a_tick_says_so():
    assert f.flags() == {"is_confident_non_tech": False}


def test_the_migration_writes_the_same_verdict_a_new_row_gets():
    db = sqlite3.connect(":memory:")
    (sql,) = db.execute(f"SELECT {f.MIGRATION_SQL[f.COLUMN]}").fetchone()
    assert bool(sql) is f.flags()[f.COLUMN]


def test_has_flags_reads_the_schema():
    assert f.has_flags(["id", "is_confident_non_tech"])
    assert not f.has_flags(["id", "title"])


def test_the_clause_hides_confident_rows_only_while_asked_to_and_able_to():
    hides = f.clause(include_non_tech=False, materialized=True)
    assert hides == "(is_confident_non_tech IS NULL OR is_confident_non_tech = false)"
    # asked to include them: no clause
    assert f.clause(include_non_tech=True, materialized=True) is None
    # a table from before the column: nothing to filter on, and no error
    assert f.clause(include_non_tech=False, materialized=False) is None


def test_the_clause_keeps_an_unstamped_row():
    """A row with no verdict (NULL) is visible: nothing is hidden on no evidence."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE jobs (id TEXT, is_confident_non_tech INT)")
    db.executemany(
        "INSERT INTO jobs VALUES (?, ?)",
        [("hidden", 1), ("shown", 0), ("unstamped", None)],
    )
    where = f.clause(include_non_tech=False, materialized=True).replace(
        "= false", "= 0"
    )
    kept = {r[0] for r in db.execute(f"SELECT id FROM jobs WHERE {where}")}
    assert kept == {"shown", "unstamped"}
