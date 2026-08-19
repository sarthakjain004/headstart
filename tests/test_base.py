import logging

from headstart.scrapers.base import BaseScraper


class _StubScraper(BaseScraper):
    """Minimal concrete scraper for exercising BaseScraper instance methods."""

    ats = "stub"

    def url(self):
        return "https://example.invalid/jobs"

    def parse(self, raw, scraped_at):
        return []


def test_report_detail_gaps_logs_missing_counts(caplog):
    caplog.set_level(logging.INFO, logger="headstart.scrapers.stub")
    _StubScraper("acme").report_detail_gaps(["desc", None, None], what="descriptions")
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.name == "headstart.scrapers.stub"
    assert record.levelno == logging.INFO
    assert "stub:acme" in record.getMessage()  # the board key
    assert "2/3 descriptions missing" in record.getMessage()


def test_report_detail_gaps_silent_when_complete(caplog):
    caplog.set_level(logging.INFO, logger="headstart.scrapers.stub")
    _StubScraper("acme").report_detail_gaps(["a", "b"], what="descriptions")
    assert caplog.records == []


def test_fan_out_isolates_failures_and_preserves_input_order():
    def fn(x):
        if x == 2:
            raise RuntimeError("boom")
        return x * 10

    # 2 fails -> default; results align to INPUT order despite out-of-order completion.
    assert BaseScraper.fan_out([1, 2, 3], fn, workers=3) == [10, None, 30]


def test_fan_out_empty_returns_empty():
    assert BaseScraper.fan_out([], lambda x: x) == []


def test_fan_out_uses_given_default():
    def boom(_):
        raise ValueError

    assert BaseScraper.fan_out([1], boom, default={}) == [{}]


def test_fan_out_runs_every_item():
    out = BaseScraper.fan_out(list(range(20)), lambda x: x + 1, workers=4)
    assert sorted(out) == list(range(1, 21))


# The async fan-out tests below call methods on _StubScraper instances rather than the class:
# `fan_out_async` is an instance method because its concurrency falls back to the scraper's own
# `detail_workers` declaration.


def test_fan_out_async_isolates_failures_and_preserves_input_order():
    # same contract as fan_out; fn ignores the session, so no network is touched
    async def fn(_session, x):
        if x == 2:
            raise RuntimeError("boom")
        return x * 10

    out = _StubScraper("x").fan_out_async([1, 2, 3], fn, concurrency=2)
    assert out == [10, None, 30]


def test_fan_out_async_empty_returns_empty():
    async def fn(_session, x):
        return x

    assert _StubScraper("x").fan_out_async([], fn) == []


def _spy_concurrency(monkeypatch):
    """Replace _gather_async with a no-network spy; returns the dict the width lands in."""
    seen = {}

    def spy(items, f, concurrency, default):
        seen["concurrency"] = concurrency

        async def _noop():
            return [default] * len(items)

        return _noop()

    monkeypatch.setattr(BaseScraper, "_gather_async", staticmethod(spy))
    return seen


async def _echo(_session, x):
    return x


def test_fan_out_async_falls_back_to_the_scrapers_own_bound(monkeypatch):
    """The trap ADR-0047 found on Eightfold and that also cost Workday: the sync path bounded
    itself to a handful of workers "since they hit one host" while the async path silently took
    the shared 100-stream default against that same host. A scraper that declares a bound must get
    that bound on both paths without having to remember a keyword at every call site."""
    seen = _spy_concurrency(monkeypatch)
    monkeypatch.delenv("HEADSTART_H2_STREAMS", raising=False)

    class _Bounded(_StubScraper):
        detail_workers = 6

    _Bounded("x").fan_out_async([1], _echo)
    assert seen["concurrency"] == 6, (
        "must inherit the scraper's own politeness bound, not 100"
    )

    class _Measured(_Bounded):
        detail_streams = 25  # measured async headroom overrides the sync bound

    _Measured("x").fan_out_async([1], _echo)
    assert seen["concurrency"] == 25

    # The operator's escape hatch outranks the declaration...
    monkeypatch.setenv("HEADSTART_H2_STREAMS", "7")
    _Measured("x").fan_out_async([1], _echo)
    assert seen["concurrency"] == 7
    # ...and an explicit argument outranks even the escape hatch — a call site that pins its
    # width (trakstar behind DataDome) is stating a host constraint no operator flag may widen.
    _Measured("x").fan_out_async([1], _echo, concurrency=3)
    assert seen["concurrency"] == 3


def test_fan_out_async_keeps_the_global_default_when_nothing_is_declared(monkeypatch):
    """A scraper with no detail pass declares no bound; it must not silently drop to something
    tiny, or every such fan-out gets slower for no reason."""
    from headstart.scrapers.base import _DEFAULT_H2_STREAMS

    seen = _spy_concurrency(monkeypatch)
    monkeypatch.delenv("HEADSTART_H2_STREAMS", raising=False)
    _StubScraper("x").fan_out_async([1], _echo)
    assert seen["concurrency"] == _DEFAULT_H2_STREAMS


def test_async_fanout_enabled_on_by_default(monkeypatch):
    # ADR-0016: async is the default; HEADSTART_ASYNC_FANOUT=0 is the escape hatch to sync
    monkeypatch.delenv("HEADSTART_ASYNC_FANOUT", raising=False)
    assert BaseScraper.async_fanout_enabled() is True
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    assert BaseScraper.async_fanout_enabled() is False
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "1")
    assert BaseScraper.async_fanout_enabled() is True


# --- spare-egress opt-in (ADR-0063) ---------------------------------------------------------------


class _WalledScraper(_StubScraper):
    ats = "walled"
    egress_fallback_on = frozenset({403, 405})


def test_egress_is_empty_unless_the_scraper_opts_in():
    """The default has to be inert: every ATS that has never walled us must keep making exactly
    the call it made before this existed."""
    assert _StubScraper("acme")._egress() == {}


def test_egress_opt_in_keys_on_the_ats_not_the_board():
    # per-Board marking would make each of a shard's Boards spend its own attempts rediscovering
    # a wall the first one already proved (the metering is per origin, across tenants)
    kwargs = _WalledScraper("acme")._egress()
    other = _WalledScraper("other-board")._egress()
    assert kwargs["egress_group"] == other["egress_group"] == "walled"
    assert kwargs["egress_on"] == other["egress_on"] == frozenset({403, 405})


def test_the_board_rides_along_for_attribution_only():
    """`egress_board` lets the shard report name which Boards spent the IP supply. It must not
    change the grouping: two Boards of one ATS still share a budget and a wall."""
    assert _WalledScraper("acme")._egress()["egress_board"] == "walled:acme"
    assert _WalledScraper("other")._egress()["egress_board"] == "walled:other"


def test_eightfold_opts_in_on_the_two_wall_statuses():
    from headstart.scrapers.eightfold import EightfoldScraper

    assert EightfoldScraper.egress_fallback_on == frozenset({403, 405})
    assert EightfoldScraper("x.eightfold.ai")._egress()["egress_group"] == "eightfold"
