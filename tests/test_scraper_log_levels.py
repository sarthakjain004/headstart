"""ADR-0039's annotation-budget rule, enforced on the scrape path's own source.

The rule (ADR-0039's 2026-09-08 amendment): **a line that can fire once per Board, per shard or
per item is never WARNING.** Under GitHub Actions the formatter renders WARNING and ERROR as
``::warning::`` / ``::error::`` workflow annotations, and GitHub keeps only **10 per step, 50 per
job and 50 per run** — everything past that is dropped from the run page silently. So WARNING is
not a severity here, it is a quota, and the scrape path is where it is easiest to spend: a shard
scrapes ~1,300 Boards, and the committed liveness ledgers carry 3,033 workday, 614 jazzhr and 423
freshteam rows that are live at ``jobs=0`` — so one routine per-Board line exhausts the whole
run's budget and displaces the aborts the annotations exist for.

Round 1 of the logging overhaul wrote that rule and violated it in the same commit, which is why
the rule now has a test rather than a paragraph.

**Why a compile-time allowlist and not a runtime ``logging.Filter``.** A filter would suppress the
records after the fact — it hides the volume rather than preventing it, and by the time a filter
sees a record the decision has already been made in code that a reader will copy. The annotation
budget is spent at emit time and the mistake is made at write time, so the check belongs on the
source: an unlisted ``_log.warning`` in a scraper fails this test the moment it is written, and
adding an entry means writing down *why* the line is bounded to one per run.

Parsed with :mod:`ast`, never imported: CI installs base deps only (no numpy/torch/pyarrow), and
several scrapers pull in modules that need more than that.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "src" / "headstart"
_SCRAPERS = _ROOT / "scrapers"
_HARVEST = _ROOT / "harvest.py"

#: ``"<file>:<function>"`` -> why that WARNING is bounded to at most one per run.
#:
#: The key deliberately names the *function*, not a line number: a line number goes stale on the
#: next edit above it and would turn this test into a rename detector.
_ALLOWED: dict[str, str] = {
    "harvest.py:scrape_all": (
        "Bounded by the `unexpected` counter to the FIRST non-transport Board failure per run "
        "(the rest log at INFO). A parse break is systemic — `KeyError: 'title'` raises on every "
        "Board of an ATS — so one stack and one annotation say what broke while `errors` says "
        "how far it reached. Same shape as index_plan.py's keep-set guard."
    ),
    "workday.py:_report_detail_losses": (
        "KNOWN EXCEPTION, not a bounded line: this is per-Board, gated on losing more than "
        "`_MAX_LOST_DETAIL_SHARE` of a Board's details. ADR-0088 decided the WARNING/INFO split "
        "at that threshold deliberately and `tests/test_scrapers.py` pins both sides of it, so "
        "reversing it is an ADR amendment rather than a level edit, and the logging overhaul "
        "that added this test scoped workday's detail tally out. Listed so it stays visible: a "
        "systemic detail outage (the ADR-0115 User-Agent denylist class) trips it on every "
        "affected Board at once, which is exactly the quota failure above."
    ),
}


def _warning_sites() -> list[tuple[str, str, int]]:
    """Every ``*.warning(...)`` call under the scrape path, as ``(file, function, line)``.

    Matches on the attribute name alone (``_log.warning``, ``self._log.warning``, and any other
    spelling of the logger) so a new module-level or instance logger cannot slip a site past this
    by being named something else. A bare reference used as a value — workday's
    ``report = _log.warning if ... else _log.info`` — is a call site too, and is caught by the
    ``Attribute`` visit rather than the ``Call`` one.
    """
    sites: list[tuple[str, str, int]] = []
    for path in [*sorted(_SCRAPERS.glob("*.py")), _HARVEST]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        # Innermost enclosing function for every node, so a site inside a nested helper is
        # attributed to the helper and a module-level one to "<module>".
        scope: dict[int, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for child in ast.walk(node):
                    scope[id(child)] = node.name
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "warning":
                sites.append((path.name, scope.get(id(node), "<module>"), node.lineno))
    return sites


def test_every_scraper_warning_is_bounded_to_one_per_run():
    unlisted = [
        (name, func, line)
        for name, func, line in _warning_sites()
        if f"{name}:{func}" not in _ALLOWED
    ]
    assert not unlisted, (
        "WARNING renders as a GitHub workflow annotation, and the budget is 10 per step / 50 per "
        "run — so ADR-0039's amendment forbids WARNING on any line that can fire once per Board, "
        "per shard or per item. Use `_log.info`, or the bounded first-occurrence idiom "
        "(index_plan.py's keep-set guard, harvest.py's `unexpected` counter) and add the site to "
        "`_ALLOWED` in this file with the reason it is bounded. Unlisted: "
        + ", ".join(f"{n}:{f}:{line}" for n, f, line in unlisted)
    )


def test_the_allowlist_names_only_sites_that_still_exist():
    """A stale entry is worse than none: it silently re-permits a WARNING at a name that has
    been reused, and it hides that the bound it describes was deleted."""
    live = {f"{name}:{func}" for name, func, _ in _warning_sites()}
    assert set(_ALLOWED) <= live, (
        "allowlisted WARNING site(s) no longer exist — delete the entry: "
        + ", ".join(sorted(set(_ALLOWED) - live))
    )
