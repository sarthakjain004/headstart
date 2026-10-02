"""Opt-in Avature routes keep one actual-send budget when fallback merges the lanes."""

import asyncio
from itertools import pairwise

import pytest

from headstart.network import http, spare_egress
from headstart.scrapers.avature import AvatureScraper


@pytest.fixture(autouse=True)
def reset_egress():
    spare_egress.reset()
    yield
    spare_egress.reset()


class Clock:
    def __init__(self):
        self.now = 10.0

    def sleep(self, seconds):
        self.now += seconds


class Response:
    def __init__(self, code):
        self.status_code = code
        self.headers = {}


@pytest.mark.parametrize("asynchronous", [False, True])
def test_actual_route_cap_covers_retry_and_converging_lanes(monkeypatch, asynchronous):
    clock = Clock()
    monkeypatch.setattr(http.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(http.time, "sleep", clock.sleep)
    original_sleep = asyncio.sleep

    async def sleep(seconds):
        clock.sleep(seconds)
        await original_sleep(0)

    monkeypatch.setattr(http.asyncio, "sleep", sleep)
    daemon = spare_egress.InMemoryEgressDaemon(proxy="socks5h://test:1")
    spare_egress.use_daemon(daemon)
    pacer = http.RoutePacer(1.0)
    sent = []

    def outcome(kwargs):
        proxy = kwargs.get("proxies", {}).get("https")
        sent.append((clock.now, proxy))
        # Preferred spare succeeds; direct is refused and then must join that same cap.
        return Response(200 if proxy else 406)

    class Session:
        def request(self, method, url, **kwargs):
            return outcome(kwargs)

    class AsyncSession:
        async def request(self, method, url, **kwargs):
            return outcome(kwargs)

    monkeypatch.setattr(http, "session", Session)
    opts = {
        "egress_group": "avature",
        "egress_on": frozenset({406}),
        "retry_on": http.TRANSIENT | {406},
        "request_pacer": pacer,
    }
    if asynchronous:

        async def run():
            session = AsyncSession()
            assert (
                await http.fetch_async(session, "GET", "u", prefer_spare=True, **opts)
            ).status_code == 200
            assert (
                await http.fetch_async(session, "GET", "u", **opts)
            ).status_code == 200
            assert (
                await http.fetch_async(session, "GET", "u", prefer_spare=True, **opts)
            ).status_code == 200

        asyncio.run(run())
    else:
        assert http.fetch("GET", "u", prefer_spare=True, **opts).status_code == 200
        assert http.fetch("GET", "u", **opts).status_code == 200
        assert http.fetch("GET", "u", prefer_spare=True, **opts).status_code == 200
    proxy_starts = [at for at, proxy in sent if proxy]
    assert len(proxy_starts) == 3
    assert all(b - a >= 1 for a, b in pairwise(proxy_starts))
    assert any(proxy is None for _, proxy in sent)
    assert all("socks5h://test:1" == proxy for _, proxy in sent if proxy)


def test_missing_spare_collapses_to_the_direct_budget(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(http.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(http.time, "sleep", clock.sleep)
    pacer = http.RoutePacer(1.0)
    times = []

    class Session:
        def request(self, method, url, **kwargs):
            assert not kwargs.get("proxies")
            times.append(clock.now)
            return Response(200)

    monkeypatch.setattr(http, "session", Session)
    for preferred in [False, True, False, True]:
        http.fetch(
            "GET",
            "u",
            egress_group="avature",
            prefer_spare=preferred,
            request_pacer=pacer,
        )
    assert times == [10.0, 11.0, 12.0, 13.0]


def test_gate_delay_cannot_reuse_old_start_slots(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(http.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(http.time, "sleep", clock.sleep)
    pacer = http.RoutePacer(1.0)
    assert pacer.claim_delay("proxy") == 0
    clock.now = 100.0  # a rotation waited past all earlier slots
    assert pacer.claim_delay("new-proxy") == 0
    assert pacer.claim_delay("proxy") == 1.0
    assert pacer.claim_delay(None) == 0
    assert pacer.claim_delay(None) == 1.0


def test_avature_opt_in_is_explicit_and_shared(monkeypatch):
    monkeypatch.delenv("HEADSTART_AVATURE_DUAL_EGRESS", raising=False)
    default = AvatureScraper("boozallen")
    assert default.detail_streams == 4
    assert "prefer_spare" not in default.detail_request({"id": "1", "url": "u"}).options
    monkeypatch.setenv("HEADSTART_AVATURE_DUAL_EGRESS", "1")
    first, second = AvatureScraper("boozallen"), AvatureScraper("bloomberg")
    assert first.detail_streams == second.detail_streams == 8
    assert first.detail_request({"id": "1", "url": "u"}).options["prefer_spare"]
    assert not first.detail_request({"id": "2", "url": "u"}).options["prefer_spare"]


def test_route_is_resolved_again_after_waiting(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(http.time, "monotonic", lambda: clock.now)
    pacer = http.RoutePacer(1.0)
    assert pacer.claim_delay(None) == 0
    routed = False

    def sleep(seconds):
        nonlocal routed
        clock.sleep(seconds)
        routed = True  # another request walled the group while this caller waited

    monkeypatch.setattr(http.time, "sleep", sleep)
    monkeypatch.setattr(
        spare_egress, "proxy_for", lambda group: "proxy" if routed else None
    )
    assert http._paced_route("avature", False, pacer) == "proxy"
    assert clock.now == 11.0


@pytest.mark.parametrize("asynchronous", [False, True])
def test_preferred_spare_waits_on_rotation_gate_without_marking_a_wall(
    monkeypatch, asynchronous
):
    daemon = spare_egress.InMemoryEgressDaemon(proxy="proxy")
    spare_egress.use_daemon(daemon)
    monkeypatch.setattr(spare_egress, "_CONNECT_TIMEOUT", 0.01)
    monkeypatch.setattr(spare_egress, "_GATE_POLL", 0.001)
    spare_egress._gate.clear()
    if asynchronous:
        assert (
            asyncio.run(spare_egress.proxy_for_async("avature", prefer_spare=True))
            is None
        )
    else:
        assert spare_egress.proxy_for("avature", prefer_spare=True) is None
    assert daemon.calls == []
    assert not spare_egress.walled_groups()
    spare_egress._gate.set()
    if asynchronous:
        assert (
            asyncio.run(spare_egress.proxy_for_async("avature", prefer_spare=True))
            == "proxy"
        )
    else:
        assert spare_egress.proxy_for("avature", prefer_spare=True) == "proxy"
    assert not spare_egress.walled_groups()


def test_preferred_traffic_does_not_inflate_wall_rescue_metrics():
    spare_egress.note_settled("avature", 200, frozenset({406}), preferred=True)
    spare_egress.note_settled("avature", 406, frozenset({406}), preferred=True)
    spare_egress.note_settled("avature", 200, frozenset({406}))
    seen = spare_egress.traffic()["avature"]
    assert seen["preferred_requests"] == 2
    assert seen["preferred_200"] == 1
    assert seen["rescued"] == 1
    assert seen["walled"] == 0
    assert any(
        "preferred spare egress returned HTTP 200 for 1/2" in x
        for x in spare_egress.report()
    )


def test_enabled_avature_boards_share_the_same_actual_start_budget(monkeypatch):
    from headstart.scrapers import avature

    clock = Clock()
    monkeypatch.setattr(http.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(http.time, "sleep", clock.sleep)
    monkeypatch.setattr(avature, "_ROUTE_PACER", http.RoutePacer(1.0))
    monkeypatch.setenv("HEADSTART_AVATURE_DUAL_EGRESS", "1")
    starts = []

    class Session:
        def request(self, method, url, **kwargs):
            starts.append(clock.now)
            assert "request_pacer" not in kwargs
            assert "prefer_spare" not in kwargs
            return Response(200)

    monkeypatch.setattr(http, "session", Session)
    first, second = AvatureScraper("boozallen"), AvatureScraper("bloomberg")
    for scraper in [first, second, first, second]:
        scraper._fetch("GET", "u")
    assert starts == [10.0, 11.0, 12.0, 13.0]


def test_concurrent_async_callers_share_one_cap_when_spare_is_missing(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(http.time, "monotonic", lambda: clock.now)
    original_sleep = asyncio.sleep

    async def sleep(seconds):
        clock.sleep(seconds)
        await original_sleep(0)

    monkeypatch.setattr(http.asyncio, "sleep", sleep)
    pacer = http.RoutePacer(1.0)
    starts = []

    class Session:
        async def request(self, method, url, **kwargs):
            assert not kwargs.get("proxies")
            starts.append(clock.now)
            return Response(200)

    async def run():
        session = Session()
        await asyncio.gather(
            *(
                http.fetch_async(
                    session,
                    "GET",
                    "u",
                    egress_group="avature",
                    prefer_spare=bool(i % 2),
                    request_pacer=pacer,
                )
                for i in range(8)
            )
        )

    asyncio.run(run())
    assert starts == list(range(10, 18))
