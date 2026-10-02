"""Tests for the browser transport (src/headstart/network/browser_http.py, ADR-0056).

Everything runs against a fake Chrome injected through the module's ``_chrome_factory`` seam —
no pydoll, no display, CI-safe. The module is process-global (one Chrome, one loop), so each
test resets that state via the ``fresh`` fixture rather than sharing it.
"""

from __future__ import annotations

import logging

import pytest

from headstart.network import browser_http as bh


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


def test_each_failed_launch_names_its_cause(monkeypatch, caplog):
    """harvest prints only the final RuntimeError, so each attempt's cause must be in the log,
    and the last one in that error's own text."""

    class _DiesOnStart(_FakeChrome):
        async def start(self):
            raise OSError("xvfb had a bad day")

    monkeypatch.setattr(bh, "_chrome_factory", _DiesOnStart)
    monkeypatch.setattr(bh, "_browser", None)
    with (
        caplog.at_level(logging.INFO, logger="headstart"),
        pytest.raises(RuntimeError, match="last: OSError: xvfb had a bad day"),
        bh.origin("https://acme.darwinbox.in/careers"),
    ):
        pass
    failed = [r for r in caplog.records if "chrome launch attempt" in r.message]
    assert len(failed) == bh._LAUNCH_ATTEMPTS
    assert all(r.levelno == logging.INFO for r in failed)
    assert "OSError: xvfb had a bad day" in failed[0].message


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

    INFO rather than WARNING on purpose: the reap runs once per launch attempt on the per-Board
    path, and under Actions a WARNING is a 10-per-step annotation quota (ADR-0039), not a
    severity. The first record's `exc_info` is what makes it worth having — the OSError's own
    errno is the difference between a raced temp dir and a dead process manager — and the rest
    restate that fault, so they carry none.
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
    monkeypatch.setattr(bh, "_teardown_traced", False)
    with (
        caplog.at_level(logging.INFO, logger="headstart"),
        pytest.raises(RuntimeError, match="failed to start"),
        bh.origin("https://acme.darwinbox.in/careers"),
    ):
        pass

    reaps = [r for r in caplog.records if "reaping a failed Chrome launch" in r.message]
    assert len(reaps) == bh._LAUNCH_ATTEMPTS
    assert all(r.levelno == logging.INFO for r in reaps)
    assert [bool(r.exc_info) for r in reaps] == [True] + [False] * (len(reaps) - 1)


@pytest.mark.parametrize("nav_fails", [False, True])
def test_a_tab_that_will_not_close_leaves_a_record(
    fresh, monkeypatch, caplog, nav_fails
):
    """Both close sites, which are separate `except`s reached by opposite outcomes.

    A tab that will not close is how `_TAB_WIDTH` leaks — the slot comes back either way, but
    the tab does not — so the failure has to be recoverable from a verbose run rather than
    swallowed. INFO for the reason the reap above gives: once per walled Board.
    """

    monkeypatch.setattr(bh, "_teardown_traced", False)

    async def _wont_close(self):
        raise RuntimeError("the tab is wedged")

    monkeypatch.setattr(_FakeTab, "close", _wont_close)
    if nav_fails:

        async def _boom(self, url, timeout=None):
            raise TimeoutError("navigation gave up")

        monkeypatch.setattr(_FakeTab, "go_to", _boom)

    with caplog.at_level(logging.INFO, logger="headstart"):
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
    assert closes[0].levelno == logging.INFO and closes[0].exc_info


def test_a_broken_blocking_install_warns_once_then_informs(monkeypatch, caplog):
    """Every Board rides the same broken install, so only the first costs an annotation — but the
    later ones still leave a line, so the log shows how far the slowdown reached."""
    import asyncio

    class _NoNetworkEvents:
        async def enable_network_events(self):
            raise RuntimeError("pydoll API drifted")

    monkeypatch.setattr(bh, "_BLOCKING_FAILURE", bh.log.FirstOnly(bh._log))
    with caplog.at_level(logging.INFO, logger=bh._log.name):
        for _ in range(3):
            asyncio.run(bh._install_blocking(_NoNetworkEvents()))
    levels = [r.levelname for r in caplog.records]
    assert levels == ["WARNING", "INFO", "INFO"]
    assert caplog.records[0].exc_info is not None
    assert all(
        "subresource blocking unavailable" in r.getMessage() for r in caplog.records
    )


@pytest.mark.parametrize("exit_path", ["direct", "harvest", "failed_harvest"])
def test_process_exit_reaps_chrome_before_profile_cleanup(tmp_path, exit_path):
    """Interpreter shutdown disables executor submissions before ordinary atexit hooks.

    pydoll's CDP reconnection resolves a host through that executor. Exercise real interpreter
    finalization, with an adapter that has the same dependency, rather than calling shutdown
    inside pytest where the executor is still alive.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    script = r"""
import asyncio
import logging
import runpy
import sys
from pathlib import Path
from headstart.network import browser_http as bh
from headstart.scrapers import harvest
from headstart.boards.company_ref import CompanyRef
logging.basicConfig(level=logging.INFO)
namespace = runpy.run_path(sys.argv[1])
marker = Path(sys.argv[2])
class Chrome(namespace['_FakeChrome']):
    async def __aexit__(self, *exc):
        await asyncio.to_thread(lambda: None)
        self._browser_process_manager.stop_process()
        self._temp_directory_manager.cleanup()
chrome = Chrome()
def stopped():
    marker.write_text('stopped\n')
def cleaned():
    assert marker.read_text() == 'stopped\n'
    marker.write_text('stopped\ncleaned\n')
chrome._browser_process_manager.stop_process = stopped
chrome._temp_directory_manager.cleanup = cleaned
bh._chrome_factory = lambda: chrome
async def blocking(tab):
    pass
bh._install_blocking = blocking
class Scraper:
    truncated = None
    def fetch(self):
        with bh.origin('https://example.invalid/careers'):
            pass
        return []
harvest.get_scraper = lambda *args, **kwargs: Scraper()
if sys.argv[3] == 'direct':
    Scraper().fetch()
else:
    def callback(*args):
        if sys.argv[3] == 'failed_harvest':
            raise ValueError('original harvest failure')
    try:
        harvest.scrape_all([CompanyRef('x', 'example')], jobs_dir=marker.parent / 'jobs',
                          on_board=callback)
    except ValueError as exc:
        assert str(exc) == 'original harvest failure'
"""
    marker = tmp_path / "reaped"
    environment = dict(os.environ, PYTHONPATH=str(Path(bh.__file__).parents[2]))
    result = subprocess.run(
        [sys.executable, "-c", script, __file__, str(marker), exit_path],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "cannot schedule new futures" not in result.stderr, result.stderr
    assert "shutdown raised" not in result.stderr, result.stderr
    assert marker.read_text() == "stopped\ncleaned\n"


def test_shutdown_releases_the_loop_and_allows_a_new_lifetime(fresh):
    with bh.origin("https://example.invalid/careers"):
        pass
    loop, thread = bh._loop, bh._loop_thread
    bh.shutdown()
    bh.shutdown()
    assert loop.is_closed()
    assert not thread.is_alive()
    with bh.origin("https://example.invalid/careers"):
        pass
    assert len(fresh.tabs) == 2
    assert bh._loop is not loop


def test_shutdown_releases_the_loop_after_failed_start(monkeypatch):
    class DiesOnStart(_FakeChrome):
        async def start(self):
            raise OSError("launch failed")

    monkeypatch.setattr(bh, "_chrome_factory", DiesOnStart)
    with (
        pytest.raises(RuntimeError, match="failed to start"),
        bh.origin("https://example.invalid"),
    ):
        pass
    loop, thread = bh._loop, bh._loop_thread
    bh.shutdown()
    assert loop.is_closed()
    assert not thread.is_alive()


def test_lazy_shutdown_does_not_create_an_event_loop(monkeypatch):
    bh.shutdown()
    monkeypatch.setattr(
        bh, "_chrome_factory", lambda: pytest.fail("Chrome should stay lazy")
    )
    bh.shutdown()
    assert bh._loop is None


def test_interrupted_harvest_cannot_launch_chrome_from_an_abandoned_worker(
    fresh, monkeypatch, tmp_path
):
    from threading import Event

    from headstart.boards.company_ref import CompanyRef
    from headstart.scrapers import harvest

    entered, released, finished = Event(), Event(), Event()
    failures = []

    class Scraper:
        truncated = None

        def __init__(self, slug):
            self.slug = slug

        def fetch(self):
            if self.slug == "complete":
                assert entered.wait(2)
                return []
            entered.set()
            assert released.wait(2)
            try:
                with bh.origin("https://example.invalid/careers"):
                    pass
            except RuntimeError as exc:
                failures.append(str(exc))
            finally:
                finished.set()
            return []

    monkeypatch.setattr(
        harvest, "get_scraper", lambda ats, slug, *args, **kwargs: Scraper(slug)
    )

    def interrupt(*args):
        raise ValueError("original harvest failure")

    try:
        with pytest.raises(ValueError, match="original harvest failure"):
            harvest.scrape_all(
                [
                    CompanyRef("greenhouse", "complete"),
                    CompanyRef("greenhouse", "late"),
                ],
                jobs_dir=tmp_path,
                max_workers=2,
                on_board=interrupt,
            )
    finally:
        released.set()
        assert finished.wait(2)
    assert failures == ["browser transport stopped with this harvest"]
    assert not fresh.started
    # The stale worker is refused; a new caller still owns a new browser lifetime.
    with bh.origin("https://example.invalid/careers"):
        pass
    assert fresh.started


def test_shutdown_cancels_a_navigation_and_returns_its_tab_slot(fresh, monkeypatch):
    import asyncio
    from concurrent.futures import CancelledError
    from threading import Event, Thread

    entered = Event()
    errors = []

    async def stalled(self, url, timeout=None):
        entered.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(_FakeTab, "go_to", stalled)

    def worker():
        try:
            with bh.worker_scope(), bh.origin("https://example.invalid/careers"):
                pass
        except CancelledError:
            errors.append("cancelled")

    thread = Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(2)
        gate = bh._gate
        bh.shutdown()
    finally:
        thread.join(timeout=2)
    assert not thread.is_alive()
    assert errors == ["cancelled"]
    assert fresh.tabs[0].closed
    assert gate._value == bh._TAB_WIDTH
    assert bh._loop is None


def test_failed_chrome_exit_reaps_without_masking_the_harvest_error(
    fresh, monkeypatch, tmp_path
):
    from headstart.boards.company_ref import CompanyRef
    from headstart.scrapers import harvest

    async def failed_exit(*args):
        raise RuntimeError("CDP teardown failed")

    monkeypatch.setattr(fresh, "__aexit__", failed_exit)

    class Scraper:
        truncated = None

        def fetch(self):
            with bh.origin("https://example.invalid/careers"):
                pass
            return []

    monkeypatch.setattr(harvest, "get_scraper", lambda *args, **kwargs: Scraper())

    def failed_callback(*args):
        raise ValueError("original harvest failure")

    with pytest.raises(ValueError, match="original harvest failure"):
        harvest.scrape_all(
            [CompanyRef("greenhouse", "example")],
            jobs_dir=tmp_path,
            on_board=failed_callback,
        )
    assert fresh._browser_process_manager.stopped == 1
    assert fresh._temp_directory_manager.cleaned == 1
    assert bh._loop is None


def test_worker_dispatched_before_shutdown_cannot_adopt_a_later_generation(
    fresh, monkeypatch, tmp_path
):
    """A dispatched worker can be paused before scope entry, outside in_flight tracking.

    cancel_futures cannot cancel its already-running Future. Bind the scope when the harvest
    builds its worker function, before this delayed entry, so it cannot join a new lifetime.
    """
    from contextlib import contextmanager
    from threading import Event, Lock

    from headstart.boards.company_ref import CompanyRef
    from headstart.scrapers import harvest

    entered, released, finished = Event(), Event(), Event()
    failures = []
    scope_lock = Lock()
    entered_scopes = 0
    original_scope = bh.worker_scope

    def delayed_scope():
        bound = original_scope()

        @contextmanager
        def delay():
            nonlocal entered_scopes
            with scope_lock:
                entered_scopes += 1
                second = entered_scopes == 2
            if second:
                entered.set()
                assert released.wait(2)
            with bound._recreate_cm():
                yield

        return delay()

    monkeypatch.setattr(bh, "worker_scope", delayed_scope)

    class Scraper:
        truncated = None

        def __init__(self, slug):
            self.slug = slug

        def fetch(self):
            if self.slug == "complete":
                assert entered.wait(2)
                return []
            try:
                with bh.origin("https://example.invalid/careers"):
                    pass
            except RuntimeError as exc:
                failures.append(str(exc))
            finally:
                finished.set()
            return []

    monkeypatch.setattr(
        harvest, "get_scraper", lambda ats, slug, *args, **kwargs: Scraper(slug)
    )

    def interrupt(*args):
        raise ValueError("original harvest failure")

    try:
        with pytest.raises(ValueError, match="original harvest failure"):
            harvest.scrape_all(
                [
                    CompanyRef("greenhouse", "complete"),
                    CompanyRef("greenhouse", "late"),
                ],
                jobs_dir=tmp_path,
                max_workers=2,
                on_board=interrupt,
            )
    finally:
        released.set()
        assert finished.wait(2)
    assert failures == ["browser transport stopped with this harvest"]
    assert not fresh.started
    # Independent callers and a subsequent batch still own a fresh lifetime.
    monkeypatch.setattr(bh, "worker_scope", original_scope)
    with bh.origin("https://example.invalid/careers"):
        pass
    assert fresh.started
    bh.shutdown()
    result = harvest.scrape_all(
        [CompanyRef("greenhouse", "late")],
        jobs_dir=tmp_path / "later",
        max_workers=1,
    )
    assert result.boards == 1 and not result.errors
    assert len(fresh.tabs) == 2
    assert failures == ["browser transport stopped with this harvest"]
