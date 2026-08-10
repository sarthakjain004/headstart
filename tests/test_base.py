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


def test_fan_out_async_isolates_failures_and_preserves_input_order():
    # same contract as fan_out; fn ignores the session, so no network is touched
    async def fn(_session, x):
        if x == 2:
            raise RuntimeError("boom")
        return x * 10

    assert BaseScraper.fan_out_async([1, 2, 3], fn, concurrency=2) == [10, None, 30]


def test_fan_out_async_empty_returns_empty():
    async def fn(_session, x):
        return x

    assert BaseScraper.fan_out_async([], fn) == []


def test_async_fanout_enabled_on_by_default(monkeypatch):
    # ADR-0016: async is the default; HEADSTART_ASYNC_FANOUT=0 is the escape hatch to sync
    monkeypatch.delenv("HEADSTART_ASYNC_FANOUT", raising=False)
    assert BaseScraper.async_fanout_enabled() is True
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "0")
    assert BaseScraper.async_fanout_enabled() is False
    monkeypatch.setenv("HEADSTART_ASYNC_FANOUT", "1")
    assert BaseScraper.async_fanout_enabled() is True
