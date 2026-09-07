"""The User-Agent every scraper sends, and the three live constraints that pin its shape.

This file exists because a User-Agent reads as cosmetic, so it gets "tidied" — and both times a
host has rejected ours the failure was **silent**: the scrape kept running, the run stayed green,
and the Boards simply returned nothing. Full measurements:
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

import headstart.scrapers as scrapers_pkg
from headstart.scrapers.base import USER_AGENT

#: The exact string a SuccessFactors edge policy denylists. Never send it again, from anywhere.
DENYLISTED = "headstart/0.1 (job-board reader)"

#: A domain or an email anywhere in the string is what zwayam's edge rejects.
_HAS_HOST = re.compile(r"[\w-]+\.(?:com|org|net|io|ai|co|edu|gov|dev)\b", re.IGNORECASE)

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
    assert not _HAS_HOST.search(USER_AGENT), (
        f"{USER_AGENT!r} carries a hostname; zwayam's edge rejects those"
    )
    assert "@" not in USER_AGENT


def test_the_agent_is_not_a_stock_tool_default():
    """zwayam's other constraint, and the more dangerous one.

    A stock default is not refused here, it is *blackholed*: the request hangs until it times out,
    which a retry ladder reads as transient and repeats. Failing loudly would be safer than this.
    """
    assert USER_AGENT
    assert not USER_AGENT.startswith(_STOCK_PREFIXES)


def test_the_agent_identifies_rather_than_impersonates():
    """Identification, not impersonation — the choice made when this string last moved.

    An honest agent was *measured* sufficient: bare ``headstart/0.1`` clears the SuccessFactors
    policy, so a browser string would buy nothing and cost the honesty.
    """
    assert USER_AGENT.startswith("headstart/")
    for badge in ("Mozilla/", "AppleWebKit", "Chrome/", "Safari/", "Gecko"):
        assert badge not in USER_AGENT


def test_no_scraper_keeps_a_second_copy_of_the_agent():
    """`base.USER_AGENT`'s own comment records that nine scrapers once held this literal locally.

    A second copy that drifts is exactly how one ATS goes on sending a denylisted string after the
    shared one moved — which is the failure this whole file is about, in a form no other test here
    would catch.
    """
    divergent = {
        module.name: mod.USER_AGENT
        for module in pkgutil.iter_modules(scrapers_pkg.__path__)
        if "USER_AGENT"
        in vars(mod := importlib.import_module(f"headstart.scrapers.{module.name}"))
        and mod.USER_AGENT != USER_AGENT
    }
    assert not divergent, f"scraper-local User-Agent(s) diverged from base: {divergent}"
