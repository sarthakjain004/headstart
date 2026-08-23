"""Tests for the spare-egress lifecycle (headstart.spare_egress).

Every path here is a *degradation* path, which is the point: this module sits on the scrape's
critical path and its whole contract is that a missing, unregistered or broken WARP costs the run
nothing beyond the Boards it was already going to lose. So the assertions are mostly "returned
None, raised nothing" — plus the two behaviours that would be actively harmful to get wrong:
picking VPN mode over proxy mode, and handing out a proxy before the tunnel is up.

`subprocess` is stubbed throughout; nothing here dials Cloudflare.
"""

import subprocess
import threading
import time

import pytest

from headstart import spare_egress


@pytest.fixture(autouse=True)
def _clean():
    """The connect outcome is cached for the process, so each test must start from unresolved."""
    spare_egress.reset()
    yield
    spare_egress.reset()


#: What Eightfold declares as a wall — passed per request, exactly as `http` passes `egress_on`.
_EF_WALL = frozenset({403, 405})


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _canned_trace(monkeypatch):
    """Answer the egress-IP trace from memory, for every stub in this file.

    `_observe_egress_ip` reads Cloudflare's `/cdn-cgi/trace` *through the proxy* on both the
    `rotate` and the `proxy_url` paths, and `_rq` is `curl_cffi`, a base dependency — so
    unstubbed it leaves the machine for real, and does so invisibly in both directions: every
    exception is swallowed as telemetry, while the port these tests stub (`127.0.0.1:40000`) is
    WARP's own, so on a developer's machine the call *succeeds* down the real tunnel and spends
    ~80-110 ms of live latency inside tests that budget milliseconds. Tests that drive the trace
    deliberately override this afterwards.
    """

    class _Trace:  # RFC 5737 TEST-NET-3, so it can never be a real address
        text = "fl=123abc\nip=203.0.113.7\ncolo=SJC\nts=1\nwarp=on\n"

    monkeypatch.setattr(spare_egress._rq, "get", lambda *a, **kw: _Trace())


def _stub(monkeypatch, handler, ready=lambda: True):
    """Route `subprocess.run` through `handler(args) -> _Proc | Exception`, recording the calls.

    ``ready`` stands in for the SOCKS5 handshake — the thing that decides whether a proxy is
    handed out at all. Stubs the trace too: `proxy_url` observes the egress IP on success, so
    this path reaches the network exactly as the rotate path does.
    """
    calls: list[list[str]] = []

    def _run(argv, **kwargs):
        calls.append(argv)
        outcome = handler(argv)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(spare_egress.subprocess, "run", _run)
    monkeypatch.setattr(spare_egress.time, "sleep", lambda *a: None)
    # The SOCKS5 handshake is a real socket connect; left unstubbed the readiness wait spins for
    # _CONNECT_TIMEOUT of wall clock in every test (it took the suite from 6s to 66s).
    monkeypatch.setattr(spare_egress, "_socks5_ready", lambda *a, **k: ready())
    _canned_trace(monkeypatch)
    return calls


def _happy(argv):
    return _Proc()


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


def test_proxy_is_withheld_until_the_socks5_handshake_answers(monkeypatch):
    """`connect` returns as soon as the request is accepted, and "Connected" is a claim about the
    tunnel, not the listener. A proxy handed out before it can carry traffic would turn the first
    Board after the wall into a second failure — so readiness is a real RFC-1928 handshake."""
    monkeypatch.setattr(spare_egress, "_CONNECT_TIMEOUT", 0.05)
    _stub(monkeypatch, _happy, ready=lambda: False)
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


def test_report_rates_rescues_over_walls_not_over_attempts():
    """The headline is `rescued / (rescued + walled)` — of the requests the spare egress was asked
    to rescue, how many it did. Attempts are reported too, but as cost, not as the denominator."""
    spare_egress.mark_walled("eightfold", 405)
    for _ in range(9):
        spare_egress.note_routed("eightfold")  # attempts, including retries
    for status in (200, 200, 200, 405):
        spare_egress.note_settled("eightfold", status, _EF_WALL)  # settled requests
    (line,) = spare_egress.report()
    assert "eightfold: walled" in line
    assert "rescued 3/4 walled request(s) (75%)" in line
    assert "9 attempt(s) carried" in line


def test_a_status_the_origin_never_walled_with_is_left_out_of_the_rate():
    """404s from a Board whose tenant migrated away are not evidence about the egress. Counted
    against it they read as a refused IP range and argue for switching the fallback off."""
    spare_egress.mark_walled("eightfold", 405)
    spare_egress.note_routed("eightfold")
    for status in (200, 404, 404):
        spare_egress.note_settled("eightfold", status, _EF_WALL)
    (line,) = spare_egress.report()
    assert "rescued 1/1 walled request(s) (100%)" in line  # not 1/3
    assert "2 settled non-wall" in line


def test_a_request_that_opted_out_of_marking_cannot_be_scored_as_walled():
    """Eightfold's API-availability probe passes an empty `egress_on`: its steady 403 means "this
    tenant has no API", not "this IP is refused". Scoring it against the *group's* wall shapes
    would put it in `walled` and deflate the rate — the misattribution this metric exists to end.
    """
    spare_egress.mark_walled("eightfold", 403)  # the group is walled on 403...
    spare_egress.note_routed("eightfold")
    spare_egress.note_settled(
        "eightfold", 403, frozenset()
    )  # ...but this request opted out
    counts = spare_egress.traffic()["eightfold"]
    assert counts["walled"] == 0 and counts["other"] == 1


def test_a_request_that_never_settled_is_counted_but_not_blamed():
    """A transport failure through the proxy returns no status at all. Left uncounted it is
    invisible to the rate the exit criterion now reads — and `network` retries went from 250
    fleet-wide to ~55,000 after #172, so this is not a hypothetical path."""
    spare_egress.mark_walled("workday", 429)
    spare_egress.note_settled("workday", None, frozenset({429}))
    counts = spare_egress.traffic()["workday"]
    assert counts["requests"] == 1 and counts["other"] == 1
    assert counts["walled"] == 0


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
    spare_egress.note_routed("eightfold")
    spare_egress.note_settled("eightfold", 200, _EF_WALL)
    spare_egress.reset()
    assert spare_egress.walled_groups() == frozenset()
    assert spare_egress.traffic() == {}
    assert spare_egress.rotation_causes() == {}


# --- how wide, once a budget is spent (#195) -----------------------------------------------------


def test_stream_width_leaves_a_scraper_that_never_opted_in_alone():
    """`group is None` is every scraper with an empty `egress_fallback_on`. Nothing about it can
    ever wall, so narrowing it would spend wall-clock to avoid a wall that does not exist."""
    assert spare_egress.stream_width(None, 100) == 100


def test_stream_width_is_the_callers_ceiling_until_the_group_walls():
    """The clamp is reactive: until an origin has actually refused this shard, the width the
    caller measured for itself stands. A run where nothing walls fans out exactly as it did."""
    assert spare_egress.stream_width("workday", 25) == 25
    spare_egress.mark_walled("eightfold", 403)
    assert spare_egress.stream_width("workday", 25) == 25, (
        "another ATS's wall is not this one's"
    )


def test_stream_width_narrows_a_walled_group():
    spare_egress.mark_walled("workday", 429)
    assert spare_egress.stream_width("workday", 25) == spare_egress._WALLED_STREAM_WIDTH


def test_stream_width_never_widens_a_ceiling_already_below_the_stopgap():
    """The stopgap is a ceiling, not a target: a group that has *just been refused* is the last
    place to answer a narrow call site by widening it.

    No production ceiling is below 12 today — the only two scrapers that can wall (workday,
    eightfold) both resolve 25 — so this is a contract pinned ahead of a caller that needs it,
    not a path any board takes. Trakstar's pinned 4 is *not* that caller: it declares no
    `egress_fallback_on`, so it reaches `stream_width` with `group=None` and is covered by the
    first test above."""
    spare_egress.mark_walled("workday", 429)
    assert spare_egress.stream_width("workday", 4) == 4


def test_stream_width_asks_nothing_of_warp(monkeypatch):
    """It reads only state something else already computed. Dialling to find out whether a spare
    egress exists would put a WARP connect on the width decision of every walled shard — including
    the ones for which the answer changes nothing (a different IP, not a fresh allowance to spend
    on the decision itself — ADR-0067, pool depth since corrected by ADR-0081)."""
    monkeypatch.setattr(
        spare_egress,
        "proxy_url",
        lambda: pytest.fail("stream_width must not dial the spare egress"),
    )
    spare_egress.mark_walled("workday", 429)
    assert (
        spare_egress.stream_width("workday", 100) == spare_egress._WALLED_STREAM_WIDTH
    )


def test_unavailable_spare_egress_warns_rather_than_whispers(monkeypatch, caplog):
    """A runner that cannot raise a tunnel is a degradation an operator should see; at INFO it
    would sit under the scrape's own output unread."""
    import logging

    caplog.set_level(logging.WARNING, logger="headstart.spare_egress")
    _stub(monkeypatch, lambda argv: FileNotFoundError("warp-cli"))
    assert spare_egress.proxy_url() is None
    assert any("unavailable" in r.getMessage() for r in caplog.records)


# --- rotation ------------------------------------------------------------------------------------
# The half of the design that had no tests, which is exactly why a failed rotation could pin the
# process to the direct route permanently and nothing caught it.


def _rotating(monkeypatch, *, restart_ok=True, comes_back=True):
    """Stub a rotation: sudo/systemctl outcome, whether SOCKS5 answers afterwards, and the trace."""
    calls: list[list[str]] = []

    def _run(argv, **kw):
        calls.append(argv)
        if argv[:2] == ["sudo", "-n"]:
            return _Proc(
                returncode=0 if restart_ok else 1,
                stderr="" if restart_ok else "no sudo",
            )
        return _Proc()

    monkeypatch.setattr(spare_egress.subprocess, "run", _run)
    monkeypatch.setattr(spare_egress.time, "sleep", lambda *a: None)
    monkeypatch.setattr(spare_egress, "_socks5_ready", lambda: comes_back)
    monkeypatch.setattr(spare_egress, "_CONNECT_TIMEOUT", 0.01)
    _canned_trace(monkeypatch)
    return calls


def test_rotate_restarts_the_daemon_rather_than_reconnecting(monkeypatch):
    """A warp-cli disconnect/connect returns the SAME egress IP — the registration is sticky to its
    edge node. Only a daemon restart forces a fresh edge, so that is what rotation must do."""
    spare_egress._proxy = "socks5://127.0.0.1:40000"
    spare_egress._resolved = True
    calls = _rotating(monkeypatch)
    assert spare_egress.rotate() is True
    assert ["sudo", "-n", "systemctl", "restart", "warp-svc"] in calls
    assert not any(c[-1] == "disconnect" for c in calls)
    assert spare_egress.rotations()["succeeded"] == 1


def test_a_failed_rotation_does_not_pin_the_process_to_the_direct_route(monkeypatch):
    """The bug this test exists for: clearing `_proxy` while leaving `_resolved` set made
    `proxy_url()` return None forever, so one bad rotation cost the whole run its spare egress —
    strictly worse than never rotating."""
    spare_egress._proxy = "socks5://127.0.0.1:40000"
    spare_egress._resolved = True
    _rotating(monkeypatch, comes_back=False)
    assert spare_egress.rotate() is False
    assert spare_egress._resolved is False  # a later caller re-dials
    assert spare_egress.rotations()["failed"] == 1


def test_rotation_without_sudo_degrades(monkeypatch):
    spare_egress._proxy = "socks5://127.0.0.1:40000"
    spare_egress._resolved = True
    _rotating(monkeypatch, restart_ok=False)
    assert spare_egress.rotate() is False
    assert spare_egress.rotations()["failed"] == 1


def test_a_throttled_caller_waits_for_the_fresh_ip_rather_than_riding_the_spent_one(
    monkeypatch,
):
    """The cooldown still bounds how often the daemon restarts — but a caller that hits it now
    *waits* instead of being handed back the IP it was just refused by.

    Every caller here has been walled *through* the spare egress, so returning early spends an
    attempt on a route already known to be dead. Waiting costs seconds and buys a live one.
    """
    spare_egress._proxy = "socks5://127.0.0.1:40000"
    spare_egress._resolved = True
    _rotating(monkeypatch)
    monkeypatch.setattr(spare_egress, "_ROTATION_COOLDOWN", 0.05)
    monkeypatch.setattr(spare_egress, "_ROTATION_WAIT_CAP", 5.0)

    assert spare_egress.rotate() is True
    armed_at = spare_egress._last_rotation  # the instant the code armed the cooldown
    called_at = time.monotonic()
    second = spare_egress.rotate()
    released_at = time.monotonic()

    assert second is True  # it waited, and came back to a fresh IP
    counts = spare_egress.rotations()
    assert counts["throttled"] == 1  # it was throttled...
    assert counts["attempted"] == 2  # ...and still rotated, after waiting the floor out
    # Two assertions, because they say different things and the old single one said neither
    # reliably. It timed from a `monotonic()` taken *after* the first rotate returned — a second
    # origin, a fraction of a millisecond later than `_last_rotation` — which left the comparison
    # sitting exactly on the boundary. Over 300 measured trials the whole margin came from
    # `Condition.wait` overshoot and its minimum was 0.345 ms, which is how CI produced
    # 0.04998673 against a 0.05 floor. Timing from `armed_at` removes the straddle.
    assert released_at - armed_at >= spare_egress._ROTATION_COOLDOWN
    # ...and that this caller was actually made to block, which the line above cannot show on its
    # own: had the cooldown already been spent, returning at once would satisfy it and still be
    # correct. Here the first rotate armed it, so the second must have waited.
    assert released_at > called_at


def test_the_wait_for_a_fresh_ip_is_bounded(monkeypatch):
    """A Board that every IP refuses must not be able to hold a worker for the shard's budget."""
    spare_egress._proxy = "socks5://127.0.0.1:40000"
    spare_egress._resolved = True
    _rotating(monkeypatch)
    monkeypatch.setattr(spare_egress, "_ROTATION_COOLDOWN", 30.0)
    monkeypatch.setattr(spare_egress, "_ROTATION_WAIT_CAP", 0.05)

    assert spare_egress.rotate() is True
    started = time.monotonic()
    given_up = spare_egress.rotate()  # gives up and reports the current proxy
    assert time.monotonic() - started < 5.0  # not the 30s cooldown

    # waited, but got no fresh IP for it — so `http` must not credit it an attempt
    assert given_up is False
    counts = spare_egress.rotations()
    assert counts["abandoned"] == 1 and counts["attempted"] == 1


def test_a_waiter_is_released_by_a_peers_rotation(monkeypatch):
    """Sixteen workers meeting one wall must not each sit out their own full cooldown: the first
    rotation serves all of them, and the generation counter is how a waiter learns that."""
    spare_egress._proxy = "socks5://127.0.0.1:40000"
    spare_egress._resolved = True
    _rotating(monkeypatch)
    monkeypatch.setattr(spare_egress, "_ROTATION_COOLDOWN", 30.0)
    monkeypatch.setattr(spare_egress, "_ROTATION_WAIT_CAP", 30.0)
    spare_egress.rotate()  # arms the cooldown, so the next caller must wait

    released = threading.Event()
    waiter = threading.Thread(target=lambda: (spare_egress.rotate(), released.set()))
    waiter.start()
    time.sleep(0.2)
    assert not released.is_set()  # genuinely parked on the condition

    with spare_egress._rotated:  # a peer produces a fresh IP and announces it
        spare_egress._rotation_generation += 1
        spare_egress._rotated.notify_all()

    assert released.wait(5)  # milliseconds, not the 30s it was waiting for
    waiter.join()


def test_the_report_names_the_boards_that_spent_the_ip_supply(monkeypatch):
    """A count says the supply ran out; only the attribution says which Boards drank it, which is
    what decides between more egress capacity and a Board that should not be scraped."""
    spare_egress._proxy = "socks5://127.0.0.1:40000"
    spare_egress._resolved = True
    _rotating(monkeypatch)
    monkeypatch.setattr(spare_egress, "_ROTATION_COOLDOWN", 0.0)
    spare_egress.mark_walled("workday", 429)
    for _ in range(3):
        spare_egress.rotate("workday:dollartree/dollartreeus")
    spare_egress.rotate("workday:kohls/kohlscareers")

    line = next(x for x in spare_egress.report() if x.startswith("rotation demand"))
    assert "workday:dollartree/dollartreeus 3" in line
    assert "workday:kohls/kohlscareers 1" in line


def test_the_gate_reopens_on_every_failure_path(monkeypatch):
    """A gate left closed would stall every worker behind a rotation that never happened."""
    spare_egress._proxy = "socks5://127.0.0.1:40000"
    spare_egress._resolved = True
    _rotating(monkeypatch, restart_ok=False)
    spare_egress.rotate()
    assert spare_egress._gate.is_set()


def test_report_includes_rotation_counts():
    spare_egress.mark_walled("workday", 429)
    spare_egress._rotations.update({"attempted": 2, "succeeded": 1, "failed": 1})
    lines = spare_egress.report()
    assert any("rotations" in line and "attempted 2" in line for line in lines)


def _tracing(monkeypatch, addresses, *, colos=None):
    """Stub the trace endpoint so successive observations report the given addresses in order.

    A list rather than one value because the whole point of the feature is telling an observation
    that *moved* from one that landed back on the same address.
    """
    seen = iter(addresses)
    colo_seen = iter(colos or ["SJC"] * len(addresses))

    class _Resp:
        def __init__(self, ip, colo):
            self.text = f"fl=123abc\nip={ip}\ncolo={colo}\nts=1\nwarp=on\n"

    def _get(url, **kw):
        return _Resp(next(seen), next(colo_seen))

    monkeypatch.setattr(spare_egress._rq, "get", _get)


def test_a_rotation_that_moves_is_told_apart_from_one_that_does_not(
    monkeypatch, caplog
):
    """A rotation *count* is not a health signal regardless of how often it lands a fresh address
    — only comparing addresses distinguishes a rotation that bought something from one that spent
    ~2s and a closed gate to land on the address it already had. (ADR-0067 measured that as ~11
    times in 30; ADR-0081 corrected the pool it drew that from, but not this reasoning.)"""
    spare_egress.reset()
    spare_egress._proxy = "socks5://127.0.0.1:40000"
    spare_egress._resolved = True
    _rotating(monkeypatch)
    monkeypatch.setattr(spare_egress, "_ROTATION_COOLDOWN", 0.0)
    # The dial that put the first address in service never happened here (`_proxy` is set
    # directly, bypassing `proxy_url`), so seed `_last_egress_ip` the same way a real dial would:
    # via the first observed rotation.
    _tracing(monkeypatch, ["104.28.232.96", "104.28.200.91", "104.28.200.91"])

    with caplog.at_level("WARNING"):
        for _ in range(3):
            spare_egress.rotate()

    text = caplog.text
    assert "104.28.232.96 via SJC (first)" in text
    assert "104.28.200.91 via SJC (moved)" in text
    assert "104.28.200.91 via SJC (SAME as before)" in text

    tallies = spare_egress.egress_ips()
    assert tallies["moved"] == 1
    assert tallies["repeat"] == 1
    assert tallies["ip:104.28.200.91"] == 2


def test_the_initial_dial_seeds_the_first_comparison(monkeypatch, caplog):
    """The dial that puts the first address in service is itself an observation — without it the
    shard's very first rotation has nothing to compare against."""
    spare_egress.reset()
    _rotating(monkeypatch)
    _tracing(monkeypatch, ["9.9.9.9", "9.9.9.9"])

    with caplog.at_level("WARNING"):
        spare_egress.proxy_url()
        monkeypatch.setattr(spare_egress, "_ROTATION_COOLDOWN", 0.0)
        spare_egress.rotate()

    assert "9.9.9.9 via SJC (first)" in caplog.text
    assert "9.9.9.9 via SJC (SAME as before)" in caplog.text


def test_report_names_the_distinct_addresses_not_just_the_rotation_count(monkeypatch):
    """The cross-shard number that matters is distinct addresses, not a rotation count — ADR-0067
    measured it at 11 IPs shared across 30 jobs; ADR-0081 corrected that to 11,007 distinct IPs
    across 12,702 rotations on real traffic. Either way, only a comparison of addresses expresses
    it; a rotation count cannot."""
    spare_egress.reset()
    spare_egress._proxy = "socks5://127.0.0.1:40000"
    spare_egress._resolved = True
    _rotating(monkeypatch)
    monkeypatch.setattr(spare_egress, "_ROTATION_COOLDOWN", 0.0)
    _tracing(
        monkeypatch, ["1.1.1.1", "2.2.2.2", "2.2.2.2"], colos=["LAX", "SJC", "SJC"]
    )
    spare_egress.mark_walled("workday", 429)
    for _ in range(3):
        spare_egress.rotate()

    line = next(x for x in spare_egress.report() if x.startswith("egress addresses"))
    assert "2 distinct" in line
    assert "2 comparison(s)" in line
    assert "1 moved" in line
    assert "1 returned the same IP" in line
    assert "1.1.1.1" in line and "2.2.2.2" in line
    assert "LAX" in line and "SJC" in line


def test_an_unreadable_trace_never_fails_the_rotation(monkeypatch):
    """Telemetry on the retry path. A trace endpoint that is slow or down must cost a log line,
    never a working rotation."""
    spare_egress.reset()
    spare_egress._proxy = "socks5://127.0.0.1:40000"
    spare_egress._resolved = True
    _rotating(monkeypatch)
    monkeypatch.setattr(spare_egress, "_ROTATION_COOLDOWN", 0.0)

    def _boom(*a, **kw):
        raise RuntimeError("trace endpoint down")

    monkeypatch.setattr(spare_egress._rq, "get", _boom)

    assert spare_egress.rotate() is True
    assert spare_egress.egress_ips()["unreadable"] == 1


def test_a_direct_response_from_the_trace_is_not_recorded_as_an_egress_address(
    monkeypatch,
):
    """`warp=off` means the trace never travelled the tunnel — recording its address would log the
    runner's direct IP as though it were the egress the shard is actually using."""
    spare_egress.reset()
    spare_egress._proxy = "socks5://127.0.0.1:40000"
    spare_egress._resolved = True
    _rotating(monkeypatch)
    monkeypatch.setattr(spare_egress, "_ROTATION_COOLDOWN", 0.0)

    class _Resp:
        text = "fl=1\nip=1.2.3.4\ncolo=SJC\nwarp=off\n"

    monkeypatch.setattr(spare_egress._rq, "get", lambda *a, **kw: _Resp())

    assert spare_egress.rotate() is True
    tallies = spare_egress.egress_ips()
    assert tallies["unreadable"] == 1
    assert "ip:1.2.3.4" not in tallies


def test_observing_the_egress_ip_never_holds_the_rotation_gate_or_lock(monkeypatch):
    """The trace is a ~4s network call; running it inside the rotation lock or the gate would
    stall every proxied worker in the shard, not merely the rotators waiting on this rotation."""
    spare_egress.reset()
    spare_egress._proxy = "socks5://127.0.0.1:40000"
    spare_egress._resolved = True
    _rotating(monkeypatch)
    monkeypatch.setattr(spare_egress, "_ROTATION_COOLDOWN", 0.0)

    gate_was_open_during_trace = []

    def _get(url, **kw):
        gate_was_open_during_trace.append(spare_egress._gate.is_set())
        return type("R", (), {"text": "ip=5.5.5.5\ncolo=SJC\nwarp=on\n"})()

    monkeypatch.setattr(spare_egress._rq, "get", _get)
    spare_egress.rotate()

    assert gate_was_open_during_trace == [True]
