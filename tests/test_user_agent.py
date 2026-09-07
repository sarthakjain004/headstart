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

#: A hostname or a bare IPv4 — the shapes that go looking like a host. Applied to the agent's
#: *note* only; see :func:`_carries_a_host` for why that split matters.
#:
#: Deny by shape, never by a TLD allowlist. The first draft listed nine TLDs with only ``.com``
#: measured, so a contact URL on ``.sh`` or ``.app`` would have passed the test and still broken
#: zwayam. The second draft required a hostname label to start with a **letter**, which quietly
#: excluded ``1password.com`` and ``3m.com`` — both real — and every bare IP.
_HOSTISH = re.compile(
    r"\b(?:\d{1,3}(?:\.\d{1,3}){3}|[a-z0-9][\w-]*\.[a-z]{2,})\b", re.IGNORECASE
)


def _carries_a_host(agent: str) -> bool:
    """True when ``agent`` carries a domain, an IP or an email — what zwayam's edge rejects.

    The host shapes are looked for in the **note** only (everything after the first space), never
    in the product token. That split is the whole reason this is a function rather than one regex:
    a version is dot-separated too, so a whole-string scan cannot tell ``headstart/0.1-rc.dev``
    from a hostname, and an earlier draft rejected it for nothing. Splitting on the first space
    removes the ambiguity instead of trying to out-regex it — ``headstart/<version>`` is a shape we
    control, and everything a host could hide in lives in the note.

    ``://`` and ``@`` are checked across the whole string: neither can appear in a version, and a
    scheme or an address is disqualifying wherever it sits.
    """
    if "://" in agent or "@" in agent:
        return True
    return bool(_HOSTISH.search(agent.partition(" ")[2]))


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
    assert not _carries_a_host(USER_AGENT), (
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
        ("headstart/0.1 (contact: maintainer)", False),
        ("headstart/0.1 (a job board reader that is quite long indeed yes)", False),
        ("headstart/0.1", False),
        # a version is not a hostname, however it is punctuated — earlier drafts failed both
        ("headstart/0.1.dev", False),
        ("headstart/0.1-rc.dev", False),
        # hostnames the TLD-allowlist draft would have let through
        ("headstart/0.1 (+https://headstart.sh)", True),
        ("headstart/0.1 (headstart.app)", True),
        # ...and hosts the letter-first draft missed: a digit-leading label, and a bare IP
        ("headstart/0.1 (1password.com)", True),
        ("headstart/0.1 (3m.com)", True),
        ("headstart/0.1 (93.184.216.34)", True),
    ],
)
def test_the_host_detector_agrees_with_what_zwayam_actually_did(candidate, rejected):
    """The detector is only worth having if it splits the measured cases the way the host did.

    Every row above with ``rejected=True`` in the first block is a string zwayam really refused,
    and every ``False`` one is a string it really served. The last three are not measurements —
    they are the two directions the first draft of this regex got wrong, kept so it cannot
    regress to a TLD allowlist without saying so.
    """
    assert _carries_a_host(candidate) is rejected


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
        "scripts/bench/probe_successfactors_detail.py",  # the harness that found it
        # the probe workflow's own header, explaining what it is probing for
        ".github/workflows/probe-successfactors-ua.yml",
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
    scanned, offenders = 0, []
    # `.py` and the workflow formats together: a User-Agent inlined into a workflow's `curl` would
    # be invisible to a Python-only scan, and this branch's own probe workflow names the literal.
    for pattern in ("*.py", "*.yml", "*.yaml"):
        for path in root.rglob(pattern):
            relative = path.relative_to(root)
            # Every check below is on the RELATIVE path. An earlier version tested `"experiment"
            # not in path.parts` — absolute — so a clone living under any directory of that name
            # skipped the entire tree and passed vacuously. Hence `scanned`, asserted at the end.
            if any(
                part.startswith(".") and part != ".github"
                for part in relative.parts[:-1]
            ):
                # `.git`, `.venv`, and the agent worktrees under `.claude/`. `.github` is
                # exempted or the `*.yml` patterns above would match nothing at all — every
                # workflow lives under a dotted directory, which is the only place they can live.
                continue
            if "experiment" in relative.parts:
                continue  # R&D capture, not source
            scanned += 1
            key = relative.as_posix()
            if key in _MAY_NAME_THE_DENYLISTED_STRING:
                continue
            if DENYLISTED in path.read_text(encoding="utf-8", errors="replace"):
                offenders.append(key)
    assert scanned > 100, (
        f"the scan only reached {scanned} file(s) — it is not looking where it thinks it is, "
        "and would pass no matter what the tree contained"
    )
    assert not sorted(offenders), (
        f"the denylisted User-Agent literal reappeared in: {sorted(offenders)}. "
        "SuccessFactors answers that exact string 403; use `base.USER_AGENT`."
    )


def test_the_allowlist_does_not_outlive_what_it_excuses():
    """An allowlist entry that no longer names the string is a hole nobody can see.

    `zwayam.py` was on this list and then stopped containing the literal when its docstring was
    rewritten; the entry survived, silently excusing a file from a check it no longer needed and
    would not have failed. Left alone, the same entry excuses a *real* reintroduction later.
    """
    root = Path(__file__).resolve().parents[1]
    stale = sorted(
        name
        for name in _MAY_NAME_THE_DENYLISTED_STRING
        if not (root / name).exists()
        or DENYLISTED not in (root / name).read_text(encoding="utf-8", errors="replace")
    )
    assert not stale, (
        f"these no longer contain the denylisted literal (or are gone) and should leave "
        f"_MAY_NAME_THE_DENYLISTED_STRING: {stale}"
    )


def test_the_standalone_scripts_copy_tracks_the_shared_agent():
    """The one hand-synced copy the module scan is structurally blind to.

    `confirm_smartrecruiters_boards.py` is deliberately standalone — urllib only, no `headstart`
    import and no sys.path setup — so it keeps its own `UA` literal with a comment asking whoever
    moves `base.USER_AGENT` to move this too. A comment is not a check, and the tree scan above
    only hunts the *old* string, so nothing would notice the next time they diverge. This does.
    """
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts/validate/confirm_smartrecruiters_boards.py"
    ).read_text(encoding="utf-8")
    assert f'UA = "{USER_AGENT}"' in source, (
        "scripts/validate/confirm_smartrecruiters_boards.py's hand-synced UA has drifted from "
        f"base.USER_AGENT ({USER_AGENT!r})"
    )
