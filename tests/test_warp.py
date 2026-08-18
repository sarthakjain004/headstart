"""Tests for the spare-egress lifecycle (headstart.warp).

Every path here is a *degradation* path, which is the point: this module sits on the scrape's
critical path and its whole contract is that a missing, unregistered or broken WARP costs the run
nothing beyond the Boards it was already going to lose. So the assertions are mostly "returned
None, raised nothing" — plus the two behaviours that would be actively harmful to get wrong:
picking VPN mode over proxy mode, and handing out a proxy before the tunnel is up.

`subprocess` is stubbed throughout; nothing here dials Cloudflare.
"""

import subprocess

import pytest

import headstart.warp as warp


@pytest.fixture(autouse=True)
def _clean():
    """The connect outcome is cached for the process, so each test must start from unresolved."""
    warp.reset()
    yield
    warp.reset()


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _stub(monkeypatch, handler):
    """Route `subprocess.run` through `handler(args) -> _Proc | Exception`, recording the calls."""
    calls: list[list[str]] = []

    def _run(argv, **kwargs):
        calls.append(argv)
        outcome = handler(argv)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(warp.subprocess, "run", _run)
    monkeypatch.setattr(warp.time, "sleep", lambda *a: None)
    return calls


def _happy(argv):
    return _Proc(stdout="Status update: Connected") if "status" in argv else _Proc()


def test_connects_in_proxy_mode_and_returns_the_socks_url(monkeypatch):
    calls = _stub(monkeypatch, _happy)
    assert warp.proxy_url() == f"socks5://127.0.0.1:{warp._PORT}"
    flat = [" ".join(c) for c in calls]
    assert any("mode proxy" in c for c in flat)
    assert any(f"proxy port {warp._PORT}" in c for c in flat)
    assert any(c.endswith("connect") for c in flat)


def test_never_selects_vpn_mode(monkeypatch):
    """VPN mode would route the runner's own traffic — artifact upload, HF push, the GitHub API —
    through Cloudflare, so the one mode this must never ask for is the default one."""
    calls = _stub(monkeypatch, _happy)
    warp.proxy_url()
    assert not any(c[-2:] == ["mode", "warp"] for c in calls)


def test_missing_binary_degrades_to_none(monkeypatch):
    _stub(monkeypatch, lambda argv: FileNotFoundError("warp-cli"))
    assert warp.proxy_url() is None  # and, crucially, did not raise


def test_timeout_degrades_to_none(monkeypatch):
    _stub(monkeypatch, lambda argv: subprocess.TimeoutExpired("warp-cli", 30))
    assert warp.proxy_url() is None


def test_a_failing_step_degrades_to_none(monkeypatch):
    # e.g. an unregistered client: `connect` exits non-zero and nothing should be handed out
    _stub(
        monkeypatch,
        lambda argv: (
            _Proc(returncode=1, stderr="not registered")
            if argv[-1] == "connect"
            else _Proc()
        ),
    )
    assert warp.proxy_url() is None


def test_proxy_is_withheld_until_status_reports_connected(monkeypatch):
    """`connect` returns as soon as the request is accepted. Handing out a proxy that is not
    carrying traffic yet would turn the first Board after the wall into a second failure."""
    monkeypatch.setattr(warp, "_CONNECT_TIMEOUT", 0.05)
    _stub(monkeypatch, lambda argv: _Proc(stdout="Status update: Disconnected"))
    assert warp.proxy_url() is None


def test_outcome_is_cached_across_calls(monkeypatch):
    calls = _stub(monkeypatch, _happy)
    first = warp.proxy_url()
    after_connect = len(calls)
    second = warp.proxy_url()
    assert first == second
    assert len(calls) == after_connect  # the second caller did no work


def test_failure_is_cached_too(monkeypatch):
    """A runner with no WARP must pay the probe once, not once per walled Board."""
    calls = _stub(monkeypatch, lambda argv: FileNotFoundError("warp-cli"))
    assert warp.proxy_url() is None
    probed = len(calls)
    assert warp.proxy_url() is None
    assert len(calls) == probed
