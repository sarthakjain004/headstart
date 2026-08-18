"""Tests for the spare-egress lifecycle (headstart.spare_egress).

Every path here is a *degradation* path, which is the point: this module sits on the scrape's
critical path and its whole contract is that a missing, unregistered or broken WARP costs the run
nothing beyond the Boards it was already going to lose. So the assertions are mostly "returned
None, raised nothing" — plus the two behaviours that would be actively harmful to get wrong:
picking VPN mode over proxy mode, and handing out a proxy before the tunnel is up.

`subprocess` is stubbed throughout; nothing here dials Cloudflare.
"""

import subprocess

import pytest

import headstart.spare_egress as spare_egress


@pytest.fixture(autouse=True)
def _clean():
    """The connect outcome is cached for the process, so each test must start from unresolved."""
    spare_egress.reset()
    yield
    spare_egress.reset()


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

    monkeypatch.setattr(spare_egress.subprocess, "run", _run)
    monkeypatch.setattr(spare_egress.time, "sleep", lambda *a: None)
    return calls


def _happy(argv):
    return _Proc(stdout="Status update: Connected") if "status" in argv else _Proc()


def test_connects_in_proxy_mode_and_returns_the_socks_url(monkeypatch):
    calls = _stub(monkeypatch, _happy)
    assert spare_egress.proxy_url() == f"socks5://127.0.0.1:{spare_egress._PORT}"
    flat = [" ".join(c) for c in calls]
    assert any("mode proxy" in c for c in flat)
    assert any(f"proxy port {spare_egress._PORT}" in c for c in flat)
    assert any(c.endswith("connect") for c in flat)


def test_never_selects_vpn_mode(monkeypatch):
    """VPN mode would route the runner's own traffic — artifact upload, HF push, the GitHub API —
    through Cloudflare, so the one mode this must never ask for is the default one."""
    calls = _stub(monkeypatch, _happy)
    spare_egress.proxy_url()
    assert not any(c[-2:] == ["mode", "warp"] for c in calls)


def test_missing_binary_degrades_to_none(monkeypatch):
    _stub(monkeypatch, lambda argv: FileNotFoundError("warp-cli"))
    assert spare_egress.proxy_url() is None  # and, crucially, did not raise


def test_timeout_degrades_to_none(monkeypatch):
    _stub(monkeypatch, lambda argv: subprocess.TimeoutExpired("warp-cli", 30))
    assert spare_egress.proxy_url() is None


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
    assert spare_egress.proxy_url() is None


def test_proxy_is_withheld_until_status_reports_connected(monkeypatch):
    """`connect` returns as soon as the request is accepted. Handing out a proxy that is not
    carrying traffic yet would turn the first Board after the wall into a second failure."""
    monkeypatch.setattr(spare_egress, "_CONNECT_TIMEOUT", 0.05)
    _stub(monkeypatch, lambda argv: _Proc(stdout="Status update: Disconnected"))
    assert spare_egress.proxy_url() is None


def test_outcome_is_cached_across_calls(monkeypatch):
    calls = _stub(monkeypatch, _happy)
    first = spare_egress.proxy_url()
    after_connect = len(calls)
    second = spare_egress.proxy_url()
    assert first == second
    assert len(calls) == after_connect  # the second caller did no work


def test_failure_is_cached_too(monkeypatch):
    """A runner with no WARP must pay the probe once, not once per walled Board."""
    calls = _stub(monkeypatch, lambda argv: FileNotFoundError("warp-cli"))
    assert spare_egress.proxy_url() is None
    probed = len(calls)
    assert spare_egress.proxy_url() is None
    assert len(calls) == probed


# --- observability -------------------------------------------------------------------------------
# Without these counters a shard whose proxy carried everything and one whose proxy carried nothing
# log identically, and "did the fallback work?" is the only question this feature has.


def test_report_gives_the_recovery_rate_per_group():
    spare_egress.mark_walled("eightfold", 405)
    for ok in (True, True, True, False):
        spare_egress.note_routed("eightfold", recovered=ok)
    assert spare_egress.traffic()["eightfold"] == {"routed": 4, "recovered": 3}
    (line,) = spare_egress.report()
    assert "eightfold: walled" in line
    assert "4 request(s)" in line and "3 recovered (75%)" in line


def test_report_names_the_worst_case_a_traffic_counter_would_hide():
    """Walled with zero routed means no spare egress could be raised — those Boards were lost
    exactly as they were before this existed, and that must not read as 'nothing happened'."""
    spare_egress.mark_walled("eightfold", 403)
    assert spare_egress.report() == [
        "eightfold: walled, but no spare egress was available — Boards lost"
    ]


def test_report_is_empty_when_nothing_walled():
    assert spare_egress.report() == []


def test_mark_walled_warns_once_per_group(caplog):
    import logging

    caplog.set_level(logging.WARNING, logger="headstart.spare_egress")
    spare_egress.mark_walled("eightfold", 403)
    spare_egress.mark_walled("eightfold", 405)
    assert len(caplog.records) == 1  # the second Board must not re-announce the wall
    assert "403" in caplog.records[0].getMessage()


def test_reset_clears_traffic_as_well_as_walls():
    spare_egress.mark_walled("eightfold", 403)
    spare_egress.note_routed("eightfold", recovered=True)
    spare_egress.reset()
    assert spare_egress.walled_groups() == frozenset()
    assert spare_egress.traffic() == {}


def test_unavailable_spare_egress_warns_rather_than_whispers(monkeypatch, caplog):
    """A runner that cannot raise a tunnel is a degradation an operator should see; at INFO it
    would sit under the scrape's own output unread."""
    import logging

    caplog.set_level(logging.WARNING, logger="headstart.spare_egress")
    _stub(monkeypatch, lambda argv: FileNotFoundError("warp-cli"))
    assert spare_egress.proxy_url() is None
    assert any("unavailable" in r.getMessage() for r in caplog.records)
