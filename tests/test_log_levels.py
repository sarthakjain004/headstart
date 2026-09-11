"""ADR-0039's annotation-budget rule, enforced on `src/headstart`'s own source.

Two checks, because the rule has two shapes. Where a whole package is per-item **by
construction** — the scrape path (`scrapers/*.py` plus `harvest.py`), which runs once per Board,
and `alerts/`, whose every entry point runs once per Account or once per alerts run — the check is
a total one: every annotation-level site must be listed, because the repetition lives in the
caller and the file itself shows no loop to key on. **Everywhere else** only a site lexically
inside a loop must be listed — most of the package's WARNINGs fire once per stage and are exactly
what the annotation budget is for, so a total check there would be 54 entries reading "once per
stage", and an allowlist nobody can read is one nobody maintains.

**Annotation level means WARNING *and* ERROR.** :class:`headstart.log._Formatter` renders both as
workflow annotations, and GitHub's budget does not distinguish them: an ERROR costs exactly what a
WARNING costs. Both checks nonetheless matched only ``.warning`` until 2026-09-08, so the *more*
expensive of the two levels was the unchecked one — and three per-item ERROR sites in ``alerts/``
were spending a budget these tests said was safe.

**What makes an attribute a log call is its receiver**, not its name. ``.error`` is also how the
stdlib spells an exception class (``re.error``, ``urllib.error.HTTPError``) and how ``argparse``
spells an abort (``ap.error``); all six such sites are in this package, and matching on the name
alone read every one of them as an annotation site. So a site counts only when the expression on
the left names a logger — a spelling
:func:`test_the_receiver_guard_knows_every_logger_in_the_package` recovers from the source rather
than assumes, though only for a logger the source *constructs*. A logger **aliased** from one
already bound (``_alarm = _log``) stays unchecked; :func:`_names_a_logger` says why that one hole
is written down rather than claimed shut.

The rule (ADR-0039's 2026-09-08 amendment): **a line that can fire once per Board, per shard or
per item is never WARNING or ERROR.** Under GitHub Actions the formatter renders WARNING and ERROR
as ``::warning::`` / ``::error::`` workflow annotations, and GitHub keeps only **10 per step, 50
per job and 50 per run** — everything past that is dropped from the run page silently. So WARNING is
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
source: an unlisted ``_log.warning`` or ``_log.error`` on the scrape path or in ``alerts/`` fails
this test the moment it is written, and adding an entry means writing down *why* the line is
bounded to one per run.

Parsed with :mod:`ast`, never imported: CI installs base deps only (no numpy/torch/pyarrow), and
several scrapers pull in modules that need more than that.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "src" / "headstart"
_SCRAPERS = _ROOT / "scrapers"
_ALERTS = _ROOT / "alerts"
_HARVEST = _ROOT / "harvest.py"
_SPARE_EGRESS = _ROOT / "spare_egress.py"
_SEARCH = _ROOT / "search.py"

#: Where *every* annotation-level site must be justified, because the repetition lives in the
#: caller rather than in a loop the file shows. Two packages and two modules, each with its own
#: population:
#:
#: * the scrape path (`scrapers/*.py` plus `harvest.py`) — once per Board;
#: * `alerts/` — once per Account (the Subscription walk in `run.main`, the update walk in
#:   `bot.main`) or once per alerts run;
#: * `spare_egress.py` — once per *request*, the finest of the three: its failure lines all sit
#:   on the retry path and are reached through `http._rotate_for`, two hops from any loop.
#:
#: The last two were added after an audit found a flood in each that *both* checks in this file
#: were blind to — `alerts/store.py`'s per-Account ERROR plus traceback, and `spare_egress.py`'s
#: rotation failures at ~720 a shard. Neither is a scraper, and neither is lexically inside the
#: loop that drives it, which is precisely the limit `_looped_annotation_sites` records about
#: itself. Both are small and carry no per-stage lines to drown the reasons in, so a total check
#: over them stays as readable as the scrape path's.
_PER_ITEM_BY_CONSTRUCTION = [
    *sorted(_SCRAPERS.glob("*.py")),
    *sorted(_ALERTS.glob("*.py")),
    _HARVEST,
    _SPARE_EGRESS,
    # `search.py` runs once per HTTP request on the deployed Space, so every line in it is
    # per-item by construction and none of it is lexically looped — the loop rule cannot see it.
    # It is here because this is where the worst regression of the whole overhaul happened: a
    # filter-drop warning re-entered by `facets.counts` once per facet option cost 58 records a
    # request, driven by a URL parameter. That was fixed by hoisting the check to the single
    # parse point, and nothing but this list would notice it coming back.
    _SEARCH,
]

#: Every spelling a logger has in this package — the receiver a matched ``.warning``/``.error``
#: must sit on for it to be a log call at all. ``log`` is here for the module itself, whose
#: ``log.FirstOnly`` construction is a WARNING site (see :func:`_annotation_sites`).
_LOGGER_NAMES = frozenset({"_log", "log", "logger", "_logger"})

#: The two spellings this package builds a logger with. Read twice: to recover the names loggers
#: are bound to (:func:`_logger_bindings`), and to accept a construction used straight as a
#: receiver, which binds no name for the list above to hold (:func:`_names_a_logger`).
_LOGGER_FACTORIES = ("log.get", "logging.getLogger")

#: ``"<path>:<function>"`` -> why that annotation-level site is bounded to at most one per run.
#:
#: The key deliberately names the *function*, not a line number: a line number goes stale on the
#: next edit above it and would turn this test into a rename detector. The path is relative to
#: ``src/headstart`` rather than a bare filename because both scanned packages carry a
#: ``registry.py`` and the two are unrelated modules — a bare name would let one waive the other.
_ALLOWED: dict[str, str] = {
    "alerts/store.py:_note_unreadable": (
        "Bound: 1 per process, by the `_record_unreadable_reported` flag it shares with "
        "`Store.get` — deliberately one bound across every reader, because a Hub outage fails "
        "all of them for all Accounts at once and one incident should cost one annotation, not "
        "one per method per Account. Hand-rolled rather than `log.FirstOnly` for the reason the "
        "module's own `_log` is: `alerts/` is copied into the Space image as a flat package "
        "with no `headstart` to import the seam from. The absent arm never reaches here — it "
        "returns at DEBUG, because a signed-in Account with no Saved set is the common path."
    ),
    "search.py:_warn_unknown_filters": (
        "Bound: 2 per HTTP request, and not against the annotation quota at all — `search.py` "
        "runs only in the deployed Space, which calls no `log.setup()`, so these render through "
        "`logging.lastResort` as bare stderr lines and no `::warning::` is ever produced. The "
        "budget that binds here is request volume, and this is the site that once cost 58 "
        "records a request: `facets.counts` re-entered `build_filter` once per facet option, "
        "driven by a URL parameter, unauthenticated. Hoisting the check to `filter_kwargs` — "
        "the single parse point — made it 2. WARNING rather than INFO is deliberate for the "
        "same reason: `lastResort` carries WARNING and above only, so INFO here is invisible "
        "in the one deployment that serves users."
    ),
    "search.py:__init__": (
        "Bound: 1 per process. The boot line naming which schema columns are dark, so an "
        "un-migrated table cannot silently ignore every `seen_within` filter and `salary` sort "
        "with no record. Same `lastResort` reasoning as above: WARNING or invisible."
    ),
    "harvest.py:scrape_all": (
        "Bounded by `log.FirstOnly` to the FIRST non-transport Board failure per run (the rest "
        "log at INFO). A parse break is systemic — `KeyError: 'title'` raises on every Board of "
        "an ATS — so one stack and one annotation say what broke while `errors` says how far it "
        "reached. Same helper as config.py's board_identity and index_plan.py's keep-set guard."
    ),
    "scrapers/workday.py:<module>": (
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
    "alerts/bot.py:<module>": (
        "`_UPDATE_FAILURE` and `_REPLY_FAILURE`, the two `log.FirstOnly` instances `main`'s "
        "per-update and per-reply arms report through, so the ceiling is two annotations per bot "
        "run however long Telegram's update queue is. Unbounded both are per-*message*, and "
        "neither outage is per-message: a break in `handle` breaks on every update of the same "
        "shape, a bad token fails every reply in the batch. Two instances rather than one "
        "because a shared bound would let whichever fired first silence the other — and the bot "
        "polls every fifteen minutes, so one unbounded line here is 96 runs of annotations a day."
    ),
    "alerts/registry.py:load": (
        "One call per bot run, from `bot.main`: the registry is a single file, read once above "
        "the update walk and never inside it. Loud on purpose — the empty `Registry` this "
        "returns is then *saved over* the stored record, so the line is the only trace that a "
        "read blip erased the master and everyone pending."
    ),
    "alerts/run.py:<module>": (
        "`_SUBSCRIPTION_FAILURE`, the `log.FirstOnly` the per-Subscription catch-all in `main` "
        "reports through: one annotation per alerts run rather than one per Account. "
        "Module-level because the bound has to span the whole Subscription walk, which is the "
        "run. What lands there is usually not per-Subscription anyway — `space_query` raises "
        "`SearchUnavailable` into it and a cold Space is cold for every Account at once — so one "
        "stack names what broke and the `failed` count says how many it took with it."
    ),
    "alerts/run.py:main": (
        "Fires at most once per run: the single after-the-loop line for the whole "
        "`email_not_enabled` set, which the Subscription walk accumulates and never logs from. "
        "The count plus `log.named_sample` carry what N per-Account lines would have said, which "
        "is the ADR-0069 case for warning at all — every Account in it asked for alerts and is "
        "silently getting none."
    ),
    "alerts/space_query.py:<module>": (
        "`_SEARCH_RETRY`, the `log.FirstOnly` `newly_seen`'s cold-start retry ladder reports "
        "through: one annotation per alerts run against the Accounts x 3 retries it costs "
        "unbounded. Module-level for exactly that reason — `newly_seen` is called once per "
        "Account, so an instance attribute would bound one Account's retries and nothing else, "
        "while a Space that is slow to wake is slow for every Account at once."
    ),
    "alerts/store.py:invites": (
        "One call per alerts run: `run.main` reads the allowlist once, above the Subscription "
        "walk, and hands the result down. The Space calls it per sign-in through `allowlist()`, "
        "but the Space is not GitHub Actions and `log._Formatter` renders an annotation only "
        "there — so that path spends no budget and this one is bounded by the run."
    ),
    "alerts/store.py:get": (
        "Bounded to the first occurrence per process by `_record_unreadable_reported`, which "
        "hand-rolls `log.FirstOnly`'s contract because `store.py` cannot import that seam "
        "without breaking the Space image. `get` runs once per Account and a Hub outage fails "
        "every one of them, so the bound turns 40 annotations and 40 stacks into one of each. "
        "Module-level rather than an instance attribute because both callers build a `Store` "
        "per item — per Account in `run.main`'s walk, per request in the Space's `/sets`. It "
        "could not be left to `run.main`'s own per-Subscription bound: this arm returns None "
        "instead of raising, so that catch-all never sees the failure at all. The site this "
        "widening was written to find; it was a per-Account ERROR plus traceback, and neither "
        "check in this file could see it."
    ),
    "spare_egress.py:<module>": (
        "`_TUNNEL_LOST` and `_WARP_OFF`, the two `log.FirstOnly` instances every broken-WARP "
        "line reports through, so the ceiling is two annotations per shard process. The "
        "population being bounded is *requests*, not processes: `rotate` runs once per walled "
        "request under a `_ROTATION_COOLDOWN` of 5.0s — ~720 per 60-minute shard against a "
        "10-per-step quota. `_TUNNEL_LOST` is shared by five sites (`_connect`, `proxy_url`, "
        "`rotate`, `_reregister`, `_restart_daemon`) because one fault fires several of them in "
        "turn rather than one each; measured on a stubbed flapping daemon over 50 rotation "
        "cycles, 150 annotations before the bound and 1 after. `_WARP_OFF` stays separate "
        "because a tunnel that answers but is not tunnelling is a different fault, and sharing "
        "would let whichever fired first silence the silent one. None of the five is reachable "
        "by this file's loop rule — they are two hops from their caller with no loop in this "
        "module to key on — which is why the total check is what has to cover them."
    ),
    "spare_egress.py:reset": (
        "Zero emissions. `reset` re-*arms* the two instances by rebuilding them; it never "
        "reports through them, and these are sites at all only because `_annotation_sites` "
        "counts a `log.FirstOnly` construction as one. It is a test seam with no caller in "
        "`src/`, `scripts/`, `deploy/` or `.github/`, so under Actions it does not run."
    ),
}


#: ``"<path>:<function>"`` -> the bound that makes a looped WARNING or ERROR safe. Package-wide,
#: and deliberately small: the check that feeds it only flags sites lexically inside a loop, so an
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
    "ingest/embed_run.py:_reconcile": (
        "Fires at most once: the `break` below it ends the scan, and the `dropped` count in "
        "the line is the whole tail it is about to discard. The loop is how it finds the first "
        "unparseable metadata line, not how often it can report one — the same shape as "
        "embed_merge.py's `_good_meta_lines` above."
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


def _names_a_logger(receiver: ast.expr) -> bool:
    """Whether the expression a matched attribute hangs off is a logger.

    ``.error`` is a name the stdlib also uses for exception classes and for ``argparse``'s abort,
    and this package holds all three shapes — ``re.error``, ``urllib.error.HTTPError``,
    ``ap.error``. None of them is a log call, and without this guard each would have to be
    hand-waived in an allowlist that claims to record *bounds*.

    Two shapes count, and both are things an ``ast`` walk can read off one expression:

    * a **name** — bare (``_log``) or the last segment of an attribute (``self._log``) — that is
      one of :data:`_LOGGER_NAMES`, the spellings
      :func:`test_the_receiver_guard_knows_every_logger_in_the_package` recovers from the source
      rather than trusting, so a logger constructed under a fifth name fails a test instead of
      going quietly unchecked;
    * a **construction used straight as the receiver** — ``logging.getLogger("x").error(...)``.
      It binds no name at all, so no list of spellings can ever reach it.

    **One shape it cannot see: an alias.** ``_alarm = _log`` copies a binding instead of making
    one, so :func:`_logger_bindings` does not report it either and ``_alarm.error(...)`` fails
    nothing — the same for a local (``shout = _log``) and for an attribute (``_box.reporter =
    _log``). Closing it needs dataflow; the alternative, importing the modules and asking each
    name what it *is*, is what CI's base-deps-only install rules out. So it is written down here
    rather than claimed shut.

    Measured, not assumed, because the obvious guess about this hole is wrong: it is aliasing
    that escapes, not attribute chains. A logger **constructed** into one —
    ``_box.reporter = log.get(...)`` — is recovered under the name ``reporter`` and fails
    :func:`test_the_receiver_guard_knows_every_logger_in_the_package` like any other. The one
    alias this package actually has, :class:`headstart.log.FirstOnly`'s ``self._logger``, is why
    ``_logger`` sits in :data:`_LOGGER_NAMES` by hand rather than by discovery: where the hole is
    closed, it is closed manually.

    So the promise is narrower than "every log call is checked", and is deliberately stated as
    the narrower thing: a logger this package *constructs* is either bound to a name the list
    knows or used inline, and either way its ``.warning``/``.error`` is checked.
    """
    if isinstance(receiver, ast.Name):
        return receiver.id in _LOGGER_NAMES
    if isinstance(receiver, ast.Attribute):
        return receiver.attr in _LOGGER_NAMES
    if isinstance(receiver, ast.Call):
        return ast.unparse(receiver.func) in _LOGGER_FACTORIES
    return False


def _annotation_sites() -> list[tuple[str, str, int]]:
    """Every annotation-level log call in the per-item packages, as ``(path, function, line)``.

    WARNING *and* ERROR, because the formatter renders both as workflow annotations and they draw
    on one budget; the receiver decides which attributes are log calls at all
    (:func:`_names_a_logger`). A bare reference used as a value — workday's
    ``report = _log.warning if ... else _log.info`` — is a call site too, and is caught by the
    ``Attribute`` visit rather than the ``Call`` one.

    ``log.FirstOnly`` counts as one as well: it warns on its first call, so a construction is an
    annotation site even though the word never appears. Without it the check would go blind on
    exactly the sites that adopt the bounded idiom it exists to encourage.
    """
    sites: list[tuple[str, str, int]] = []
    for path in _PER_ITEM_BY_CONSTRUCTION:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        # Innermost enclosing function for every node, so a site inside a nested helper is
        # attributed to the helper and a module-level one to "<module>".
        scope: dict[int, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for child in ast.walk(node):
                    scope[id(child)] = node.name
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in ("warning", "error", "FirstOnly")
                and _names_a_logger(node.value)
            ):
                rel = path.relative_to(_ROOT).as_posix()
                sites.append((rel, scope.get(id(node), "<module>"), node.lineno))
    return sites


def test_every_per_item_annotation_is_bounded_to_one_per_run():
    unlisted = [
        (name, func, line)
        for name, func, line in _annotation_sites()
        if f"{name}:{func}" not in _ALLOWED
    ]
    assert not unlisted, (
        "WARNING and ERROR both render as GitHub workflow annotations, and the budget is 10 per "
        "step / 50 per run — so ADR-0039's amendment forbids either on any line that can fire "
        "once per Board, per shard or per item. Every line in `scrapers/`, `harvest.py` and "
        "`alerts/` can, which is why the check is a total one there. Use `_log.info`, or the "
        "bounded first-occurrence idiom (`log.FirstOnly`) and add the site to `_ALLOWED` in this "
        "file with the reason it is bounded. Unlisted: "
        + ", ".join(f"{n}:{f}:{line}" for n, f, line in unlisted)
    )


def _looped_annotation_sites() -> list[tuple[str, str, int]]:
    """Every annotation-level log call lexically inside a ``for``/``while`` body, package-wide.

    The check above is a total one because everything in the packages it reads runs per item —
    per Board on the scrape path, per Account in ``alerts/``. The rest of ``src/headstart``
    cannot be read that way — most of its WARNINGs fire once per stage and
    are exactly what the annotation budget is *for* — so listing all 58 of them would be 54
    entries reading "once per stage", and an allowlist nobody can read is an allowlist nobody
    maintains. What distinguishes the dangerous ones is shape: a WARNING or ERROR inside a loop
    repeats with the collection it iterates.

    Its honest limit: a per-item WARNING in a function whose loop lives in another module is not
    lexically inside one and is not caught here. That is why the two packages where that shape is
    the norm keep a total check instead — ``alerts/store.py``'s per-Account ERROR, called from
    ``run.main``'s Subscription walk one module away, is exactly the miss this limit predicts.

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
                and node.attr in ("warning", "error")
                and _names_a_logger(node.value)
                and id(node) in looped
            ):
                rel = path.relative_to(_ROOT).as_posix()
                sites.append((rel, scope.get(id(node), "<module>"), node.lineno))
    return sites


def test_no_annotation_repeats_with_the_collection_it_sits_in():
    unlisted = [
        (name, func, line)
        for name, func, line in _looped_annotation_sites()
        if f"{name}:{func}" not in _LOOPED_OK
    ]
    assert not unlisted, (
        "a WARNING or ERROR inside a loop repeats with that loop, and both are GitHub "
        "annotations, capped together at 10 per step / 50 per job. Either bound it with "
        "`log.FirstOnly` (whose `.report` this check deliberately ignores), drop it to "
        "`_log.info` and summarise after the loop, or add it to `_LOOPED_OK` with the bound "
        "that makes it safe. Unlisted: "
        + ", ".join(f"{n}:{f}:{line}" for n, f, line in unlisted)
    )


def test_the_looped_allowlist_names_only_sites_that_still_exist():
    live = {f"{name}:{func}" for name, func, _ in _looped_annotation_sites()}
    assert set(_LOOPED_OK) <= live, (
        "allowlisted looped annotation site(s) no longer exist — delete the entry: "
        + ", ".join(sorted(set(_LOOPED_OK) - live))
    )


def _logger_bindings() -> list[tuple[str, int, str]]:
    """``(path, line, name)`` for every logger this package *constructs* and binds to a name.

    Both assignment shapes, because they are one statement to a reader and two node types to
    ``ast``: ``_log = log.get(...)`` is an :class:`ast.Assign` and the annotated
    ``_log: logging.Logger = log.get(...)`` is an :class:`ast.AnnAssign`. Matching only the first
    is how a logger under a novel name walked past the guard this scan exists to keep honest —
    the shape is rare in this package today, which is exactly why nobody would notice it arrive.

    A binding whose value is not a construction (``_alarm = _log``) is *not* reported; see
    :func:`_names_a_logger` for why that shape is out of reach and what is promised instead.
    """
    bindings: list[tuple[str, int, str]] = []
    for path in sorted(_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign | ast.AnnAssign):
                continue
            # `x: logging.Logger` with no value is an AnnAssign too, and binds nothing.
            if not isinstance(node.value, ast.Call):
                continue
            if ast.unparse(node.value.func) not in _LOGGER_FACTORIES:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name | ast.Attribute):
                    rel = path.relative_to(_ROOT).as_posix()
                    name = getattr(target, "id", None) or target.attr
                    bindings.append((rel, node.lineno, name))
    return bindings


def test_the_receiver_guard_knows_every_logger_in_the_package():
    """The receiver guard is an allowlist of spellings, so it is exactly as good as its list.

    A logger bound to a name that is not on it makes every call on that logger invisible to both
    checks above — a silent hole, and the failure mode the guard was introduced to avoid rather
    than to swap for. So the spellings are recovered from the source instead of trusted.

    Scoped to loggers the package **constructs**, in either assignment shape. An alias of one
    already bound is not a construction and is not recovered here, so this test cannot promise
    "every logger", only "every logger built from ``log.get`` or ``logging.getLogger``" —
    :func:`_names_a_logger` carries the full limit. Claiming the wider thing is what let a
    ``_log: logging.Logger = log.get(__name__)`` under a novel name pass all five tests.
    """
    bindings = _logger_bindings()
    assert bindings, (
        "found no loggers at all — this scan has stopped matching how they are bound"
    )
    unknown = [b for b in bindings if b[2] not in _LOGGER_NAMES]
    assert not unknown, (
        "logger(s) bound to a name `_LOGGER_NAMES` does not know, so `_names_a_logger` reads "
        "every WARNING/ERROR on them as something other than a log call and both checks in this "
        "file go blind on them. Add the name to `_LOGGER_NAMES`: "
        + ", ".join(f"{f}:{line}:{name}" for f, line, name in unknown)
    )


def test_the_allowlist_names_only_sites_that_still_exist():
    """A stale entry is worse than none: it silently re-permits an annotation at a name that has
    been reused, and it hides that the bound it describes was deleted."""
    live = {f"{name}:{func}" for name, func, _ in _annotation_sites()}
    assert set(_ALLOWED) <= live, (
        "allowlisted annotation site(s) no longer exist — delete the entry: "
        + ", ".join(sorted(set(_ALLOWED) - live))
    )
