from headstart.scrapers.base import BaseScraper


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
