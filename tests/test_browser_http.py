"""Tests for the browser transport (src/headstart/browser_http.py, ADR-0056).

Everything runs against a fake Chrome injected through the module's ``_chrome_factory`` seam —
no pydoll, no display, CI-safe. The module is process-global (one Chrome, one loop), so each
test resets that state via the ``fresh`` fixture rather than sharing it.
"""

from __future__ import annotations

import logging

import pytest

from headstart import browser_http as bh


class _FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class _FakeRequest:
    def __init__(self, tab) -> None:
        self._tab = tab

    async def post(self, url, json=None):
        self._tab.calls.append(("post", url, json))
        return self._tab.answers.pop(0)

    async def get(self, url):
        self._tab.calls.append(("get", url, None))
        return self._tab.answers.pop(0)


class _FakeTab:
    def __init__(self, browser) -> None:
        self.browser = browser
        self.calls: list = []
        self.answers: list = []
        self.navigated: list[str] = []
        self.closed = False
        self.request = _FakeRequest(self)

    async def enable_network_events(self):
        pass

    async def _execute_command(self, cmd):
        pass

    async def go_to(self, url, timeout=None):
        self.navigated.append(url)

    async def close(self):
        self.closed = True


class _FakeProcessManager:
    """Stands in for pydoll's ``BrowserProcessManager`` — tracks whether it was reaped."""

    def __init__(self) -> None:
        self.stopped = 0

    def stop_process(self) -> None:
        self.stopped += 1


class _FakeTempDirManager:
    """Stands in for pydoll's ``TempDirectoryManager`` — tracks whether it was reaped."""

    def __init__(self) -> None:
        self.cleaned = 0

    def cleanup(self) -> None:
        self.cleaned += 1


class _FakeChrome:
    def __init__(self) -> None:
        self.tabs: list[_FakeTab] = []
        self.started = False
        self._browser_process_manager = _FakeProcessManager()
        self._temp_directory_manager = _FakeTempDirManager()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.started = False

    async def start(self):
        self.started = True

    async def new_tab(self):
        tab = _FakeTab(self)
        self.tabs.append(tab)
        return tab


@pytest.fixture
def fresh(monkeypatch):
    """A fake Chrome behind the factory seam, with the module's globals reset around the test."""
    chrome = _FakeChrome()
    monkeypatch.setattr(bh, "_chrome_factory", lambda: chrome)
    monkeypatch.setattr(bh, "_browser", None)
    yield chrome
    bh.shutdown()


def test_origin_navigates_once_then_fetches_on_the_warmed_tab(fresh):
    with bh.origin("https://acme.darwinbox.in/ms/candidate/careers") as page:
        tab = fresh.tabs[0]
        tab.answers = [_FakeResponse(200, '{"data": [1, 2]}')]
        got = page.post_json("/api/jobs", {"page": 1})
    assert tab.navigated == ["https://acme.darwinbox.in/ms/candidate/careers"]
    # the fetch is same-origin: base derived from the page URL, not passed by the caller
    assert tab.calls == [("post", "https://acme.darwinbox.in/api/jobs", {"page": 1})]
    assert got == {"data": [1, 2]}
    assert tab.closed  # the tab's lifetime is the with-block


def test_non_200_raises_with_the_status_not_a_retry(fresh):
    """HTTP answers are never retried — a retried 403 would not be a pass."""
    with bh.origin("https://acme.darwinbox.in/careers") as page:
        tab = fresh.tabs[0]
        tab.answers = [_FakeResponse(403, "blocked")]
        with pytest.raises(bh.BrowserHTTPError) as excinfo:
            page.post_json("/api/jobs", {"page": 1})
    assert excinfo.value.status_code == 403
    assert len(tab.calls) == 1  # exactly one attempt


def test_client_side_fault_gets_exactly_one_retry(fresh):
    """pydoll's occasional mis-shaped evaluate result is client-side: one stated retry."""
    with bh.origin("https://acme.darwinbox.in/careers") as page:
        tab = fresh.tabs[0]

        flaky = {"first": True}

        async def _post(url, json=None):
            tab.calls.append(("post", url, json))
            if flaky.pop("first", False):
                raise KeyError("result")  # the plumbing hiccup, not an HTTP answer
            return _FakeResponse(200, '{"data": []}')

        tab.request.post = _post
        assert page.post_json("/api/jobs", {"page": 1}) == {"data": []}
    assert len(tab.calls) == 2


def test_chrome_launch_is_retried_then_reported(monkeypatch):
    attempts = []

    class _DiesOnStart(_FakeChrome):
        async def start(self):
            attempts.append(1)
            raise OSError("xvfb had a bad day")

    monkeypatch.setattr(bh, "_chrome_factory", _DiesOnStart)
    monkeypatch.setattr(bh, "_browser", None)
    with (
        pytest.raises(RuntimeError, match="failed to start"),
        bh.origin("https://acme.darwinbox.in/careers"),
    ):
        pass
    assert len(attempts) == bh._LAUNCH_ATTEMPTS


def test_a_failed_launch_reaps_its_process_and_temp_dir(monkeypatch):
    """Each failed attempt must kill its own Chrome process and remove its own temp profile dir
    before the next attempt starts — otherwise a leaked process and dir sit until Python's own
    GC finalizer races a still-dying Chrome for the directory (the "Directory not empty" OSError
    seen in production logs), and the leaked process competes with the retry for CPU/memory.
    """
    instances: list[_FakeChrome] = []

    class _DiesOnStart(_FakeChrome):
        async def start(self):
            instances.append(self)
            raise OSError("xvfb had a bad day")

    monkeypatch.setattr(bh, "_chrome_factory", _DiesOnStart)
    monkeypatch.setattr(bh, "_browser", None)
    with (
        pytest.raises(RuntimeError, match="failed to start"),
        bh.origin("https://acme.darwinbox.in/careers"),
    ):
        pass
    assert len(instances) == bh._LAUNCH_ATTEMPTS
    assert all(i._browser_process_manager.stopped == 1 for i in instances)
    assert all(i._temp_directory_manager.cleaned == 1 for i in instances)


def test_tab_is_closed_even_when_the_caller_raises(fresh):
    with (
        pytest.raises(ValueError, match="caller bug"),
        bh.origin("https://acme.darwinbox.in/careers"),
    ):
        raise ValueError("caller bug")
    assert fresh.tabs[0].closed


def test_a_failed_navigation_returns_its_slot_and_closes_its_tab(fresh, monkeypatch):
    """A tab opened before a failed nav must not outlive it, and the width must come back.

    ``_TAB_WIDTH`` slots are the whole concurrency budget: leak them and every later walled
    Board blocks forever on a semaphore nobody will release.
    """

    async def _boom(self, url, timeout=None):
        raise TimeoutError("navigation gave up")

    monkeypatch.setattr(_FakeTab, "go_to", _boom)
    monkeypatch.setattr(bh, "_TAB_WIDTH", 1)  # one slot: a leak makes the retry hang

    for _ in range(3):
        with (
            pytest.raises(TimeoutError),
            bh.origin("https://acme.darwinbox.in/careers"),
        ):
            pass

    # Three boards, three tabs, all closed — and the third only ran because the first two
    # handed their slot back.
    assert len(fresh.tabs) == 3
    assert all(t.closed for t in fresh.tabs)


def test_missing_pydoll_says_so_instead_of_blaming_chrome_startup(monkeypatch):
    """An uninstalled extra is not a flaky launch: no retries, and a message naming the fix."""

    def _no_pydoll():
        raise bh.BrowserUnavailable("the browser transport needs pydoll")

    monkeypatch.setattr(bh, "_chrome_factory", _no_pydoll)
    monkeypatch.setattr(bh, "_browser", None)
    with (
        pytest.raises(bh.BrowserUnavailable, match="needs pydoll"),
        bh.origin("https://acme.darwinbox.in/careers"),
    ):
        pass


def test_a_reap_that_itself_fails_leaves_a_record(monkeypatch, caplog):
    """The reap's own failure is the very "Directory not empty" race its comment predicts, and
    a bare `pass` made the one symptom the code names unreportable.

    DEBUG rather than WARNING on purpose: the reap runs once per launch attempt on the per-Board
    path, and under Actions a WARNING is a 10-per-step annotation quota (ADR-0039), not a
    severity. `exc_info` is what makes the record worth having — the OSError's own errno is the
    difference between a raced temp dir and a dead process manager.
    """

    class _WontClean(_FakeTempDirManager):
        def cleanup(self) -> None:
            raise OSError("[Errno 39] Directory not empty")

    class _DiesOnStart(_FakeChrome):
        def __init__(self) -> None:
            super().__init__()
            self._temp_directory_manager = _WontClean()

        async def start(self):
            raise OSError("xvfb had a bad day")

    monkeypatch.setattr(bh, "_chrome_factory", _DiesOnStart)
    monkeypatch.setattr(bh, "_browser", None)
    with (
        caplog.at_level(logging.DEBUG, logger="headstart"),
        pytest.raises(RuntimeError, match="failed to start"),
        bh.origin("https://acme.darwinbox.in/careers"),
    ):
        pass

    reaps = [r for r in caplog.records if "reaping a failed Chrome launch" in r.message]
    assert len(reaps) == bh._LAUNCH_ATTEMPTS
    assert all(r.levelno == logging.DEBUG and r.exc_info for r in reaps)


@pytest.mark.parametrize("nav_fails", [False, True])
def test_a_tab_that_will_not_close_leaves_a_record(
    fresh, monkeypatch, caplog, nav_fails
):
    """Both close sites, which are separate `except`s reached by opposite outcomes.

    A tab that will not close is how `_TAB_WIDTH` leaks — the slot comes back either way, but
    the tab does not — so the failure has to be recoverable from a verbose run rather than
    swallowed. DEBUG for the reason the reap above gives: once per walled Board.
    """

    async def _wont_close(self):
        raise RuntimeError("the tab is wedged")

    monkeypatch.setattr(_FakeTab, "close", _wont_close)
    if nav_fails:

        async def _boom(self, url, timeout=None):
            raise TimeoutError("navigation gave up")

        monkeypatch.setattr(_FakeTab, "go_to", _boom)

    with caplog.at_level(logging.DEBUG, logger="headstart"):
        if nav_fails:
            with (
                pytest.raises(TimeoutError),
                bh.origin("https://acme.darwinbox.in/careers"),
            ):
                pass
        else:
            with bh.origin("https://acme.darwinbox.in/careers"):
                pass

    expected = (
        "closing the tab of a failed navigation raised"
        if nav_fails
        else "closing a finished board's tab raised"
    )
    closes = [r for r in caplog.records if r.message == expected]
    assert len(closes) == 1
    assert closes[0].levelno == logging.DEBUG and closes[0].exc_info
