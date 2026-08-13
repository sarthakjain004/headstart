"""Invariants of the shipped `config/role_watchlist.json` (ADR-0051, amended by ADR-0052).

Deliberately stdlib-only, so it runs in CI's quality job. `headstart.roles` imports numpy at
module scope, so `test_roles.py` and `test_role_trends.py` both `importorskip` and are SKIPPED
in CI — a watchlist invariant asserted there would gate nothing. These checks are cheap and the
failures they catch are silent: a typo'd `parent` orphans a role from every drill, an
uncompilable pattern raises inside a pipeline step marked `continue-on-error`, and a ninth role
under one parent simply never gets a colour.
"""

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WATCHLIST = REPO / "config" / "role_watchlist.json"
FAMILIES = REPO / "config" / "role_families.json"
APP_JS = REPO / "src" / "headstart" / "ui" / "static" / "app.js"


def _roles() -> list[dict]:
    return json.loads(WATCHLIST.read_text(encoding="utf-8"))["roles"]


def test_every_parent_is_a_real_family():
    """`load_watchlist` raises on an unknown parent, but only when the pipeline runs it — and
    that step is `continue-on-error`, so the first sign would be a chart quietly missing a
    drill, not a red run."""
    families = {
        f["name"] for f in json.loads(FAMILIES.read_text(encoding="utf-8"))["families"]
    }
    orphans = {r["name"]: r["parent"] for r in _roles() if r["parent"] not in families}
    assert not orphans, f"watch roles naming a family that does not exist: {orphans}"


def test_every_pattern_compiles():
    bad = {}
    for role in _roles():
        for pattern in role["match"]:
            try:
                re.compile(pattern)
            except re.error as exc:
                bad[f"{role['name']}:{pattern}"] = str(exc)
    assert not bad, f"watch roles with an uncompilable pattern: {bad}"


def test_names_are_unique():
    names = [r["name"] for r in _roles()]
    assert len(names) == len(set(names)), f"duplicate watch-role names in {names}"


def test_no_parent_exceeds_the_chart_colour_budget():
    """The by-role drill plots `CHART_MAX` distinct colours and greys the rest, so a parent
    carrying more roles than that ships a series nobody can read. Read from app.js rather than
    hardcoded, so the two cannot drift apart."""
    match = re.search(
        r"const\s+CHART_MAX\s*=\s*(\d+)", APP_JS.read_text(encoding="utf-8")
    )
    assert match, "CHART_MAX not found in app.js — did the constant get renamed?"
    chart_max = int(match.group(1))

    per_parent: dict[str, int] = {}
    for role in _roles():
        per_parent[role["parent"]] = per_parent.get(role["parent"], 0) + 1
    over = {p: n for p, n in per_parent.items() if n > chart_max}
    assert not over, f"parents over the {chart_max}-colour budget: {over}"
