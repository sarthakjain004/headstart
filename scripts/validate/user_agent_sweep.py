"""Prove a User-Agent change is safe across every ATS before it ships — the check that was almost
skipped.

`base.USER_AGENT` is one string shared by every scraper, so moving it is a change to ~20k Board
fetches a run. It has to move sometimes: a SuccessFactors edge policy denylists the literal it held
until 2026-09-07 (`docs/successfactors/2026-09-07_user-agent-denylist.md`), which cost 102 Boards
their whole detail pass, silently, for five consecutive runs.

**This exists because the first candidate replacement broke an ATS.** The obvious fix was
`headstart/0.1 (+https://…)` — honest, contactable, verified against the SuccessFactors policy. Run
through here it came back **WORSE on zwayam**, whose edge answers `curl (92) HTTP/2 stream error` to
any agent carrying a domain or an email. Reasoning would not have found that; only scraping under
both strings did. Without this sweep the fix for one silent multi-run data loss would have shipped
another.

**How it measures.** Live hiring Boards drawn from `data/validate/liveness/`, scraped twice through
the **real** `fetch_raw()` + `parse()` — once under each string — and the Job counts compared.
Boards are drawn small (1-20 jobs) so a full sweep is minutes, and the seed is fixed so a verdict is
reproducible; `--seed` draws a different sample when a second opinion is wanted.

**It does not cover every ATS, and says so on every run.** The sample comes from the liveness
ledgers, so an ATS without one cannot be sampled at all — `sensehq` is registered and has no
ledger, and `join` is in `DISABLED_ATS`. Calling this "every ATS" is the same overstatement
that had to be corrected once already, so the header prints the uncovered names rather than a
count a reader has to trust.

**Two things the output will not tell you, and both matter.**

`USER_AGENT` is imported **by value** into each scraper module, so patching `base` alone is a no-op
and the sweep would silently compare the old string against itself — every row would read SAME and
mean nothing. Every module holding the name is patched, and the count is printed so a reader can
see it happened.

And a row is only evidence if the request reached a host. `DNSError` (personio, zoho) or a
`ValueError` under **both** strings is a Board that was never fetched, not a Board the change left
alone. Those read SAME and are excluded from the verdict for that reason.

Run: python -u scripts/validate/user_agent_sweep.py --new "headstart/0.1"
"""

from __future__ import annotations

import argparse
import csv
import importlib
import pkgutil
import random
import sys
import time
from pathlib import Path

import headstart.scrapers as scrapers_pkg
from headstart.scrapers import registry
from headstart.scrapers.base import USER_AGENT as CURRENT

#: Resolved from this file, not the cwd — the sibling scripts do the same, and a cwd-relative
#: path makes "reproducible from a clone" true only when run from the repo root.
LIVENESS = Path(__file__).resolve().parents[2] / "data/validate/liveness"
#: Small Boards keep a full sweep to minutes; the question is "does the host answer", not "how
#: many jobs", so a 3-job Board settles it as well as a 3,000-job one and costs a thousandth.
_MIN_JOBS, _MAX_JOBS = 1, 20
_SCRAPED_AT = "2026-01-01T00:00:00Z"


def _patchable_modules() -> list:
    """Every module holding a `USER_AGENT` global — see the docstring's by-value warning."""
    modules = [
        importlib.import_module(f"headstart.scrapers.{info.name}")
        for info in pkgutil.iter_modules(scrapers_pkg.__path__)
    ]
    return [module for module in modules if hasattr(module, "USER_AGENT")]


def _set_agent(modules: list, agent: str) -> None:
    for module in modules:
        module.USER_AGENT = agent


def _sample(seed: int, per_ats: int) -> list[tuple[str, str]]:
    """One or more small live Boards per ATS, from the committed liveness ledgers.

    The ledgers are the repo's own authority for liveness (CLAUDE.md: they are committed to git,
    unlike the rest of `data/`), so this needs no HF round-trip and is reproducible from a clone.
    """
    random.seed(seed)
    picks: list[tuple[str, str]] = []
    for path in sorted(LIVENESS.glob("*.csv")):
        rows = [
            (row[0], row[1])
            for row in csv.reader(path.read_text(encoding="utf-8").splitlines())
            if len(row) >= 5
            and row[3] == "live"
            and row[4].isdigit()
            and _MIN_JOBS <= int(row[4]) <= _MAX_JOBS
        ]
        if rows:
            picks.extend(random.sample(rows, min(per_ats, len(rows))))
    return picks


def _count(ats: str, slug: str) -> int | str:
    """Jobs the real scraper returns, or the exception's name — both are outcomes worth comparing."""
    try:
        scraper = registry.get_scraper(ats, slug, slug)
        return len(scraper.parse(scraper.fetch_raw(), _SCRAPED_AT))
    except Exception as exc:  # noqa: BLE001 - classifying the failure IS the measurement
        return type(exc).__name__


def _verdict(old: int | str, new: int | str) -> str:
    """How one Board's two scrapes compare.

    ``UNREACHED`` is its own outcome and not a flavour of ``SAME``, which is the distinction that
    matters most here: a Board that raised under *both* strings (a dead host, a DNS failure) says
    nothing at all about the change, and counting it as "unchanged" inflates the evidence that the
    change is safe. An earlier writeup did exactly that.
    """
    if not isinstance(old, int) and not isinstance(new, int):
        return "UNREACHED"
    if old == new:
        return "SAME"
    if isinstance(new, int) and (not isinstance(old, int) or new > old):
        return "BETTER"
    return "WORSE"


def _sweep(
    modules: list,
    picks: list[tuple[str, str]],
    old_agent: str,
    new_agent: str,
    verdicts: dict[str, int],
    worse: list[str],
) -> None:
    """Scrape every picked Board under both agents, printing each row as it lands."""
    for ats, slug in picks:
        counts: dict[str, int | str] = {}
        for label, agent in (("old", old_agent), ("new", new_agent)):
            _set_agent(modules, agent)
            counts[label] = _count(ats, slug)
            time.sleep(0.2)
        old, new = counts["old"], counts["new"]
        verdict = _verdict(old, new)
        verdicts[verdict] += 1
        if verdict == "WORSE":
            worse.append(f"{ats}:{slug} ({old} -> {new})")
        print(
            f"{ats:<17} {slug[:37]:<38} {old!s:>10} {new!s:>10}  {verdict}",
            flush=True,
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--new", required=True, help="the candidate User-Agent")
    ap.add_argument(
        "--old", default=CURRENT, help="what to compare against (default: in-tree)"
    )
    ap.add_argument("--per-ats", type=int, default=2, help="Boards sampled per ATS")
    ap.add_argument("--seed", type=int, default=20260907)
    args = ap.parse_args()

    modules = _patchable_modules()
    picks = _sample(args.seed, args.per_ats)
    covered = {ats for ats, _ in picks}
    # Named, not counted. An ATS missing from the sample is a hole in the evidence, and a bare
    # "20 ATSes" reads as complete when the registry holds 22.
    uncovered = sorted(set(registry.SCRAPERS) - covered)
    print(
        f"patching USER_AGENT in {len(modules)} module(s); "
        f"{len(picks)} Board(s) across {len(covered)} of {len(registry.SCRAPERS)} ATS(es)\n"
        f"  old: {args.old!r}\n  new: {args.new!r}\n"
        f"  NOT covered (no liveness ledger, or disabled): {', '.join(uncovered) or 'none'}\n",
        flush=True,
    )
    print(f"{'ats':<17} {'board':<38} {'old':>10} {'new':>10}  verdict", flush=True)

    verdicts: dict[str, int] = {"SAME": 0, "BETTER": 0, "WORSE": 0, "UNREACHED": 0}
    worse: list[str] = []
    try:
        _sweep(modules, picks, args.old, args.new, verdicts, worse)
    finally:
        # In a `finally` because the interesting exit is an exception: a Ctrl-C or a scraper
        # blowing up mid-sweep would otherwise leave every scraper module holding the CANDIDATE
        # agent for the rest of the process, and anything run afterwards in the same interpreter
        # would quietly measure the wrong string.
        _set_agent(modules, CURRENT)

    print(
        "\n"
        + "  ".join(f"{name} {count}" for name, count in verdicts.items())
        + "\n  (UNREACHED rows never contacted a host under either string — not evidence)",
        flush=True,
    )
    if worse:
        print("\nWORSE:", flush=True)
        for row in worse:
            print(f"  {row}", flush=True)
    return 1 if worse else 0


if __name__ == "__main__":
    sys.exit(main())
