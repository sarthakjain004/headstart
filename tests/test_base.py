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
