"""The User-Agent every scraper sends, and the three live constraints that pin its shape.

This file exists because a User-Agent reads as cosmetic, so it gets "tidied" — and both times a
host has rejected ours the failure was **silent**: the scrape kept running, the run stayed green,
and the Boards simply returned nothing. The policy these pin is ADR-0115; the measurements are
`docs/successfactors/2026-09-07_user-agent-denylist.md`.

1. **SuccessFactors denylists one exact literal.** ``headstart/0.1 (job-board reader)`` returns 403
   on `careers.te.com` while ``headstart/0.1 (job-board)``, ``headstart/0.1 (reader)``,
   ``curl/8.7.1`` and ``python-requests/2.32.3`` all return 200 on the same URL — so it is a
   denylist entry for that full string, not a heuristic about bots. It cost 102 Boards their whole
   detail pass: 0 jobs each across five consecutive runs.
2. **zwayam rejects a domain or an email.** Four candidates carrying one failed with
   ``curl (92) HTTP/2 stream error``, 2 of 2 attempts each, so no contact URL can live in this
   string.
3. **zwayam blackholes stock agents.** ``curl``'s and ``python-requests``'s own defaults **time
   out** rather than answering, so a caller treating a timeout as transient retries forever.

Those three leave a narrow intersection, and ``headstart/0.1`` is what sits in it. Nothing here
makes a network call — these guard the *properties* the live measurements established, so a later
edit that violates one fails here instead of in another five-run silent data loss.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path

import pytest

import headstart.scrapers as scrapers_pkg
from headstart.scrapers.base import USER_AGENT

#: The exact string a SuccessFactors edge policy denylists. Never send it again, from anywhere.
DENYLISTED = "headstart/0.1 (job-board reader)"

#: A domain or an email anywhere in the string is what zwayam's edge rejects.
#:
#: Deny by *shape*, not by a TLD allowlist. The first draft listed nine TLDs, which was wrong in
#: both directions: only ``.com`` was ever measured, so the other eight were asserted rather than
#: observed, and a real contact URL on ``.sh``/``.app``/``.uk`` would have sailed through the test
#: and still broken zwayam — while ``headstart/0.1.dev`` would have been failed for nothing. A
#: hostname label has to start with a letter, which is what keeps the version number out.
_LOOKS_LIKE_HOST = re.compile(r"://|@|\b[a-z][\w-]*\.[a-z]{2,}\b", re.IGNORECASE)

#: Agents zwayam blackholes outright — it times out on these rather than refusing them.
_STOCK_PREFIXES = (
    "curl/",
    "python-requests/",
    "Wget/",
    "libwww-perl/",
    "Go-http-client/",
)


def test_the_agent_is_not_the_denylisted_literal():
    """The regression itself. This string is the one `careers.te.com` answers 403."""
    assert USER_AGENT != DENYLISTED


def test_the_agent_carries_no_domain_or_email():
    """zwayam's constraint, and the reason there is no contact URL here.

    Putting one back reinstates ``curl (92) HTTP/2 stream error`` on every zwayam Board.
    """
    assert not _LOOKS_LIKE_HOST.search(USER_AGENT), (
        f"{USER_AGENT!r} carries a hostname or email; zwayam's edge rejects those"
    )


@pytest.mark.parametrize(
    ("candidate", "rejected"),
    [
        # measured against zwayam, 2 of 2 attempts each — the four that failed
        ("headstart/0.1 (+https://github.com/o/r)", True),
        ("headstart/0.1 (+github.com/o/r)", True),
        ("headstart/0.1 (github.com/o/r)", True),
        ("headstart/0.1 (contact someone@example.com)", True),
        # and the four that were served
        ("headstart/0.1 (a/b)", False),
        ("headstart/0.1 (contact: sarthak)", False),
        ("headstart/0.1 (a job board reader that is quite long indeed yes)", False),
        ("headstart/0.1", False),
        # a version number is not a hostname — the TLD-allowlist draft failed this one
        ("headstart/0.1.dev", False),
        # ...and these are hostnames the allowlist draft would have let through
        ("headstart/0.1 (+https://headstart.sh)", True),
        ("headstart/0.1 (headstart.app)", True),
    ],
)
def test_the_host_detector_agrees_with_what_zwayam_actually_did(candidate, rejected):
    """The detector is only worth having if it splits the measured cases the way the host did.

    Every row above with ``rejected=True`` in the first block is a string zwayam really refused,
    and every ``False`` one is a string it really served. The last three are not measurements —
    they are the two directions the first draft of this regex got wrong, kept so it cannot
    regress to a TLD allowlist without saying so.
    """
    assert bool(_LOOKS_LIKE_HOST.search(candidate)) is rejected


def test_the_agent_is_not_a_stock_tool_default():
    """zwayam's other constraint, and the more dangerous one.

    A stock default is not refused here, it is *blackholed*: the request hangs until it times out,
    which a retry ladder reads as transient and repeats. Failing loudly would be safer than this.
    """
    assert USER_AGENT
    assert not USER_AGENT.startswith(_STOCK_PREFIXES)


def test_the_agent_identifies_rather_than_impersonates():
    """Identification, not impersonation.

    Unlike the three constraints above this is a **stated policy, not a host measurement** — the
    call was made explicitly when the string last moved, and it is cheap to hold because an honest
    agent was measured sufficient: bare ``headstart/0.1`` clears the SuccessFactors policy on its
    own. A browser string would buy nothing and cost the honesty, so the only way it gets here is
    by someone reaching for it as a reflex. This is the test that stops that.
    """
    assert USER_AGENT.startswith("headstart/")
    for badge in ("Mozilla/", "AppleWebKit", "Chrome/", "Safari/", "Gecko"):
        assert badge not in USER_AGENT


def test_no_scraper_keeps_a_second_copy_of_the_agent():
    """`base.USER_AGENT`'s own comment records that nine scrapers once held this literal locally.

    A second copy that drifts is how one ATS goes on sending a denylisted string after the shared
    one moved. This catches only the narrow form — a module global named ``USER_AGENT`` under
    `headstart.scrapers`; the broader form is
    :func:`test_the_denylisted_literal_survives_nowhere_in_the_tree`.
    """
    divergent = {
        info.name: module.USER_AGENT
        for info in pkgutil.iter_modules(scrapers_pkg.__path__)
        if "USER_AGENT"
        in vars(module := importlib.import_module(f"headstart.scrapers.{info.name}"))
        and module.USER_AGENT != USER_AGENT
    }
    assert not divergent, f"scraper-local User-Agent(s) diverged from base: {divergent}"


#: Files allowed to contain the denylisted literal, because they *discuss* it rather than send it.
#: Anything else naming that string is presumed to be about to put it on the wire.
_MAY_NAME_THE_DENYLISTED_STRING = frozenset(
    {
        "tests/test_user_agent.py",  # this file — DENYLISTED, and the parametrised cases
        "src/headstart/scrapers/base.py",  # the comment explaining why the value moved
        "src/headstart/scrapers/zwayam.py",  # its own docstring's account of the same episode
        "scripts/bench/probe_successfactors_detail.py",  # the harness that found it
    }
)


def test_the_denylisted_literal_survives_nowhere_in_the_tree():
    """The copy the narrow test cannot see.

    `scripts/validate/confirm_smartrecruiters_boards.py` keeps a hand-synced ``UA`` literal that
    is deliberately not imported (it is a standalone urllib script), and two `scripts/discover/`
    files held their own until this change pointed them at the shared constant. None of those is
    a module under `headstart.scrapers`, so the drift test above is blind to every one of them —
    and a literal inlined straight into a headers dict would be invisible to both.

    Scanning the tree for the string itself is the only check that covers all three shapes. Docs
    are excluded deliberately: `docs/` records what was measured and *must* keep quoting it.
    """
    root = Path(__file__).resolve().parents[1]
    offenders = sorted(
        str(relative)
        for path in root.rglob("*.py")
        # Any dotted directory covers `.git`, `.venv` and — the one that actually bit — the agent
        # worktrees under `.claude/`, which are full checkouts of other branches and so hold
        # dozens of legitimate copies of the old string. `experiment/` is R&D capture, not source.
        if not any(part.startswith(".") for part in path.relative_to(root).parts)
        and "experiment" not in path.parts
        and (relative := path.relative_to(root).as_posix())
        not in _MAY_NAME_THE_DENYLISTED_STRING
        and DENYLISTED in path.read_text(encoding="utf-8", errors="replace")
    )
    assert not offenders, (
        f"the denylisted User-Agent literal reappeared in: {offenders}. "
        "SuccessFactors answers that exact string 403; use `base.USER_AGENT`."
    )
