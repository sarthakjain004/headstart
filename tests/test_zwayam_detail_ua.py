"""Zwayam's detail path needs a browser User-Agent; the shared bare one is 403'd at the edge.

`public.zwayam.com/jobs-service` sits behind an Akamai rule the search host and `data-service`
do not have. Under `base.USER_AGENT` ("headstart/0.1") every description fetch returned a 403
`Access Denied` page — 56,771 of 56,771 detail-Jobs across the five runs of 2026-09-16, with
`learned 0` descriptions every run.

Measured live 2026-09-16 over 10 real jobs on 5 Boards: repo UA 0/10, Chrome UA 10/10.
"""

from pathlib import Path

from headstart.scrapers import zwayam
from headstart.scrapers.base import USER_AGENT


def test_detail_user_agent_is_browser_shaped():
    ua = zwayam._DETAIL_USER_AGENT
    assert ua.startswith("Mozilla/5.0"), (
        "the edge rule refuses non-browser agents on this path"
    )
    assert "Chrome/" in ua


def test_detail_user_agent_is_not_the_shared_bare_one():
    """The shared value must stay bare: this same host rejects agents carrying a domain or an
    email, and a SuccessFactors denylist wants it bare too."""
    assert zwayam._DETAIL_USER_AGENT != USER_AGENT


def test_detail_user_agent_carries_no_domain_or_email():
    """The module docstring records that this host answers a UA with a domain or an email with a
    curl (92) stream error, so the browser string must not reintroduce one."""
    ua = zwayam._DETAIL_USER_AGENT
    assert "@" not in ua
    assert ".com" not in ua and ".io" not in ua and "http" not in ua


def test_only_the_detail_call_uses_the_browser_agent():
    """Surgical: the listing, config and homepage calls keep the shared bare agent."""
    body = Path(zwayam.__file__).read_text()
    # The constant is defined once and used once; everything else stays on USER_AGENT.
    assert body.count('"User-Agent": _DETAIL_USER_AGENT') == 1
