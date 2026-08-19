"""The ADR index must list every ADR, exactly once, in order.

`docs/adr/README.md` is how anyone finds a decision — CLAUDE.md points at it for the reasoning
behind every non-obvious call. Three ADRs (0045, 0064, 0065) had files and no row, which is the
same class of defect as a stale docstring: the index gets trusted, so a gap in it hides a
decision rather than merely inconveniencing a reader. Pinned here for the same reason
`test_readme_schema.py` pins the served-table columns.
"""

from __future__ import annotations

import pathlib
import re

_ADR_DIR = pathlib.Path(__file__).resolve().parents[1] / "docs" / "adr"
_ROW = re.compile(r"^\|\s*\[(\d{4})\]\((\d{4}-[a-z0-9-]+\.md)\)", re.MULTILINE)


def _files() -> dict[str, str]:
    return {p.name[:4]: p.name for p in _ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md")}


def test_every_adr_has_exactly_one_index_row():
    files = _files()
    assert len(files) > 60, f"only found {len(files)} ADRs — the glob is wrong"
    rows = _ROW.findall((_ADR_DIR / "README.md").read_text(encoding="utf-8"))
    numbers = [n for n, _ in rows]

    assert sorted(set(numbers) - set(files)) == [], "indexed but no such file"
    assert sorted(set(files) - set(numbers)) == [], "has a file but no index row"
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    assert duplicates == [], f"listed more than once: {duplicates}"


def test_index_rows_link_to_the_file_they_name():
    files = _files()
    wrong = [
        (n, target)
        for n, target in _ROW.findall(
            (_ADR_DIR / "README.md").read_text(encoding="utf-8")
        )
        if files.get(n) != target
    ]
    assert wrong == [], f"row links to the wrong file: {wrong}"


def test_adr_numbers_are_unique_across_files():
    """Two ADRs claiming one number is how a merge silently loses a decision — main took 0067
    for the spare-egress amendment while a second 0067 was in flight, caught before it landed."""
    seen: dict[str, list[str]] = {}
    for p in _ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md"):
        seen.setdefault(p.name[:4], []).append(p.name)
    clashes = {n: v for n, v in seen.items() if len(v) > 1}
    assert clashes == {}, f"duplicate ADR numbers: {clashes}"
