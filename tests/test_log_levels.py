"""ADR-0039's annotation-budget rule, enforced on `src/headstart`'s own source.

Two checks, because the rule has two shapes. On the **scrape path** (`scrapers/*.py` plus
`harvest.py`) every WARNING must be listed, because everything there runs once per Board whether
or not a loop is visible in the same file. **Everywhere else** only a WARNING lexically inside a
loop must be listed — most of the package's WARNINGs fire once per stage and are exactly what
the annotation budget is for, so a total check there would be 54 entries reading "once per
stage", and an allowlist nobody can read is one nobody maintains.

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

#: The scrape path, where every WARNING must be justified because every function there runs
#: once per Board whether or not a loop is visible in the same file.
_PER_BOARD_BY_CONSTRUCTION = [*sorted(_SCRAPERS.glob("*.py")), _HARVEST]

#: ``"<file>:<function>"`` -> why that WARNING is bounded to at most one per run.
#:
#: The key deliberately names the *function*, not a line number: a line number goes stale on the
#: next edit above it and would turn this test into a rename detector.
_ALLOWED: dict[str, str] = {
    "harvest.py:scrape_all": (
        "Bounded by `log.FirstOnly` to the FIRST non-transport Board failure per run (the rest "
        "log at INFO). A parse break is systemic — `KeyError: 'title'` raises on every Board of "
        "an ATS — so one stack and one annotation say what broke while `errors` says how far it "
        "reached. Same helper as config.py's board_identity and index_plan.py's keep-set guard."
    ),
    "workday.py:<module>": (
        "`_DETAIL_LOSS_OVER_SHARE`, the module-level `log.FirstOnly` that `_report_detail_losses` "
        "reports a past-`_MAX_LOST_DETAIL_SHARE` detail gap through: the FIRST such Board per "
        "shard process warns and every later one states the identical line at INFO. Module-level "
        "because every Board builds its own scraper, so an instance attribute would bound "
        "nothing — which is why the site lands at `<module>` rather than in the function. "
        "ADR-0088's threshold itself is untouched: it still decides what the gap is, and the "
        "counts in the line still say which side of it each Board fell on. What it no longer "
        "decides is how many annotations a shard spends, because the outage class this line "
        "catches is not per-Board — ADR-0115's User-Agent denylist trips it on every affected "
        "Board at once, 102 in the incident of record. ADR-0088's 2026-09-08 amendment records "
        "the change; `tests/test_scrapers.py` pins both sides of the threshold and the bound."
    ),
}


#: ``"<file>:<function>"`` -> the bound that makes a looped WARNING safe. Package-wide, and
#: deliberately small: the check that feeds it only flags WARNINGs lexically inside a loop, so an
#: entry here is a claim that this particular loop cannot run away.
_LOOPED_OK: dict[str, str] = {
    "config.py:load_active_companies": (
        "Bounded by the ledger directory: one iteration per committed liveness CSV, 28 of them, "
        "and the line fires only for a stem with no registered scraper. A whole ledger silently "
        "dropped is precisely the anomaly worth an annotation, and 28 is the ceiling even if "
        "every scraper were renamed at once."
    ),
    "ingest/embed_merge.py:_good_meta_lines": (
        "Fires at most once: the `break` on the next line ends the scan. The loop is how it "
        "finds the first unparseable metadata line, not how often it can report one."
    ),
    "ingest/embed_run.py:_encode_groups": (
        "The wedged-allocator stop, guarded by `consec_failed >= 64` and followed by "
        "`wedged = True`, which ends the walk — one line per run. The per-batch failure beside "
        "it is the unbounded one, and that goes through `_BATCH_FAILURE` (`log.FirstOnly`)."
    ),
    "ingest/state_fetch.py:fetch_state": (
        "Bounded by the retry ladder itself: the loop is the retries, capped by the attempt "
        "budget and the Hub-advised window, so the count is a handful per stage and each line "
        "reports a different wait. A state fetch that is retrying IS the stage's headline."
    ),
}


def _warning_sites() -> list[tuple[str, str, int]]:
    """Every ``*.warning(...)`` call under the scrape path, as ``(file, function, line)``.

    Matches on the attribute name alone (``_log.warning``, ``self._log.warning``, and any other
    spelling of the logger) so a new module-level or instance logger cannot slip a site past this
    by being named something else. A bare reference used as a value — workday's
    ``report = _log.warning if ... else _log.info`` — is a call site too, and is caught by the
    ``Attribute`` visit rather than the ``Call`` one.

    ``log.FirstOnly`` counts as one as well: it warns on its first call, so a construction is a
    WARNING site even though the word never appears. Without it the check would go blind on
    exactly the sites that adopt the bounded idiom it exists to encourage.
    """
    sites: list[tuple[str, str, int]] = []
    for path in _PER_BOARD_BY_CONSTRUCTION:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        # Innermost enclosing function for every node, so a site inside a nested helper is
        # attributed to the helper and a module-level one to "<module>".
        scope: dict[int, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for child in ast.walk(node):
                    scope[id(child)] = node.name
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in (
                "warning",
                "FirstOnly",
            ):
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
        "(`log.FirstOnly`) and add the site to `_ALLOWED` in this file with the reason it is "
        "bounded. Unlisted: " + ", ".join(f"{n}:{f}:{line}" for n, f, line in unlisted)
    )


def _looped_warning_sites() -> list[tuple[str, str, int]]:
    """Every ``*.warning(...)`` lexically inside a ``for``/``while`` body, package-wide.

    The scrape-path check above is a total one because everything there runs per Board. The rest
    of ``src/headstart`` cannot be read that way — most of its WARNINGs fire once per stage and
    are exactly what the annotation budget is *for* — so listing all 58 of them would be 54
    entries reading "once per stage", and an allowlist nobody can read is an allowlist nobody
    maintains. What distinguishes the dangerous ones is shape: a WARNING inside a loop repeats
    with the collection it iterates.

    Its honest limit: a per-item WARNING in a function whose loop lives in another module is not
    lexically inside one and is not caught here. That is why the scrape path — where that shape
    is the norm — keeps its total check instead.

    ``.report`` is deliberately not matched: :class:`headstart.log.FirstOnly` is the bound, so a
    call to it inside a loop is the fix, not the defect.
    """
    sites: list[tuple[str, str, int]] = []
    for path in sorted(_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        looped: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.For | ast.AsyncFor | ast.While):
                for stmt in node.body:
                    for child in ast.walk(stmt):
                        looped.add(id(child))
        scope: dict[int, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for child in ast.walk(node):
                    scope[id(child)] = node.name
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "warning"
                and id(node) in looped
            ):
                rel = path.relative_to(_ROOT).as_posix()
                sites.append((rel, scope.get(id(node), "<module>"), node.lineno))
    return sites


def test_no_warning_repeats_with_the_collection_it_sits_in():
    unlisted = [
        (name, func, line)
        for name, func, line in _looped_warning_sites()
        if f"{name}:{func}" not in _LOOPED_OK
    ]
    assert not unlisted, (
        "a WARNING inside a loop repeats with that loop, and WARNING is a GitHub annotation "
        "capped at 10 per step / 50 per job. Either bound it with `log.FirstOnly` (whose "
        "`.report` this check deliberately ignores), drop it to `_log.info` and summarise after "
        "the loop, or add it to `_LOOPED_OK` with the bound that makes it safe. Unlisted: "
        + ", ".join(f"{n}:{f}:{line}" for n, f, line in unlisted)
    )


def test_the_looped_allowlist_names_only_sites_that_still_exist():
    live = {f"{name}:{func}" for name, func, _ in _looped_warning_sites()}
    assert set(_LOOPED_OK) <= live, (
        "allowlisted looped WARNING site(s) no longer exist — delete the entry: "
        + ", ".join(sorted(set(_LOOPED_OK) - live))
    )


def test_the_allowlist_names_only_sites_that_still_exist():
    """A stale entry is worse than none: it silently re-permits a WARNING at a name that has
    been reused, and it hides that the bound it describes was deleted."""
    live = {f"{name}:{func}" for name, func, _ in _warning_sites()}
    assert set(_ALLOWED) <= live, (
        "allowlisted WARNING site(s) no longer exist — delete the entry: "
        + ", ".join(sorted(set(_ALLOWED) - live))
    )
