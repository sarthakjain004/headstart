"""Keeps the README's served-table documentation honest against the real schema.

A schema table in prose rots the moment someone adds a column and forgets the docs — and a stale
schema is worse than none, because it is trusted. So the check is mechanical: parse the column
names out of the README's `### The served table` table and compare them, in order, against
`index._schema()`.

Skips where pyarrow is absent (CI's quality job installs base deps only), so treat it as a local
guard rather than a gate. Run the suite before opening a schema PR.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_README = Path(__file__).resolve().parents[1] / "README.md"
_HEADING = "### The served table"


def _documented_columns() -> list[str]:
    """The column names in the README's schema table — the leading `` `code` `` cell of each row,
    taken from the first markdown table after the heading."""
    body = _README.read_text(encoding="utf-8").split(_HEADING, 1)
    assert len(body) == 2, f"{_README} has no '{_HEADING}' section"
    rows = re.findall(r"^\|\s*`([^`]+)`\s*\|", body[1], flags=re.MULTILINE)
    assert rows, "found the heading but no schema table rows under it"
    return rows


def test_readme_documents_every_column_in_order():
    pa = pytest.importorskip("pyarrow")  # noqa: F841 — index imports it at module scope
    from headstart.ingest.index import _schema

    assert _documented_columns() == _schema(768).names


def test_readme_example_rows_use_the_documented_columns():
    """The worked examples drift as easily as the table. Every key they show must be a real
    column — `vector` included, since it is elided rather than omitted."""
    pytest.importorskip("pyarrow")
    from headstart.ingest.index import _schema

    section = _README.read_text(encoding="utf-8").split(_HEADING, 1)[1]
    block = section.split("```jsonc", 1)[1].split("```", 1)[0]
    keys = set(re.findall(r'^\s*"(\w+)":', block, flags=re.MULTILINE))
    assert keys, "no example rows found under the schema table"
    assert keys <= set(_schema(768).names), (
        f"example rows show columns the table doesn't have: "
        f"{sorted(keys - set(_schema(768).names))}"
    )
