"""Browser transport: page-context JSON fetches from a real Chrome, for client-shaped walls.

``http.py`` is the transport for every ordinary host. This is its browser twin, for the hosts
whose wall admits a genuine Chrome and nothing else — darwinbox's Cloudflare configuration blocks
every non-browser client including ``curl_cffi impersonate="chrome"``, from any IP
(`docs/darwinbox/cloudflare-wall.md`, ADR-0056). The measured production shape (arm A of the
2026-08-14 probes): navigate the tenant's page once to clear the wall, then call the JSON API via
an in-page fetch on the warmed tab — same JSON as the HTTP path, so callers' parsing is untouched.

Interface::

    with browser_http.origin("https://acme.darwinbox.in/ms/candidate/careers") as page:
        data = page.post_json("/ms/candidateapi/job/alljobs?companyId=main", body)

Everything else is hidden: one headful Chrome per process (headless is a flat block on darwinbox),
lazily started with a launch retry (Chrome under xvfb dies at startup often enough that 2 of 9
probe legs were lost to it — each failed attempt's OS process and temp profile dir are reaped
before the next attempt starts, rather than leaked for the life of the shard), a dedicated asyncio
loop in a daemon thread so harvest's worker threads can call this synchronously, a tab-count
semaphore (probe-measured width), heavy subresource blocking per tab (no media, no JS — arm A
renders nothing, and Turnstile never runs), and a hard per-board navigation deadline. HTTP answers
are never retried — a retried 403 is not a pass; the one retry is for pydoll's own occasional
evaluate-shape hiccup, a client-side fault.

Requires a display: production wraps the scrape in ``xvfb-run`` (pipeline.yml); locally a real
window opens. Chrome starts only when the first caller actually reaches ``origin()``, so shards
whose boards never hit a wall never pay for it.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import threading
from contextlib import contextmanager
from typing import Any, Self
from urllib.parse import urlsplit

from headstart import log

_log = log.get(__name__)

_NAV_TIMEOUT_S = 20  # per-board deadline, from the shape probe's _DEADLINE
_FETCH_TIMEOUT_S = 30
_TAB_WIDTH = 4  # probe-measured: width 4-6 holds; stay at the safe end
_LAUNCH_ATTEMPTS = 3
# pydoll's default (10s) is a poll deadline for Chrome's own CDP endpoint to come up, not a
# network-only timeout — it races real OS-level browser startup. On a 4-vCPU Actions runner
# already running up to 16 scrape worker threads, that's tight enough to miss on its own, and
# a spare-egress rotation's blocking `systemctl restart warp-svc` (ADR-0063, ~2-4s measured)
# can tip a launch over it: of 28 "Browser failed to start within timeout" hits across 6 runs,
# a rotation directly overlapped the 10s poll window in 9-12 of them, and all 3 launches that
# fully exhausted their 3 retries sat inside shards rotating every ~10-20s throughout. pydoll's
# own maintainers doubled this same default in their docs' example fix for slow starts
# (pydoll#195); Playwright's equivalent default is 30s. 20s keeps 3 retries well under the
# scrape step's budget while giving a launch real headroom against both sources of load.
_CHROME_START_TIMEOUT_S = 20

# CDP Network.setBlockedURLs matches whole URLs with `*` wildcards; query strings need their own
# pattern. "heavy": no media, no JS (Turnstile included) — arm A never renders the app.
_BLOCKED = [
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg", "*.webp", "*.ico",
    "*.woff", "*.woff2", "*.ttf", "*.css", "*.mp4",
    "*.png?*", "*.svg?*", "*.css?*",
    "*static.cloudflareinsights.com*",
    "*.js", "*.js?*", "*challenges.cloudflare.com*",
]  # fmt: skip

# Headful keeps the UA and client hints genuine (pydoll already passes --no-first-run etc.).
_CHROME_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--lang=en-US",
    "--window-size=1400,1000",
    "--no-sandbox",  # GitHub runners restrict unprivileged user namespaces
    "--disable-dev-shm-usage",
]


class BrowserUnavailable(Exception):
    """The browser transport cannot run here — pydoll is not installed.

    Distinct from a launch failure: retrying will never help, and the fix is an install, so this
    skips the retry loop and says so rather than surfacing as "Chrome failed to start 3 times".
    """


# Hand-rolled rather than `log.FirstOnly` on purpose, and not for the reason it first looks:
# an f-string would satisfy `report`'s finished-string signature perfectly well. The difference
# is what happens after the first: `FirstOnly` demotes to INFO and keeps naming every later
# occurrence, which is right when each one carries its own subject. Here they do not — every
# line after the first restates one browser's one broken blocking install, so this goes silent
# instead. Swap it for the helper the day a second thing can fail here.
_blocking_failed = False


async def _install_blocking(tab) -> None:
    """Drop media and scripts for this tab. Best-effort, but *loudly* so.

    Not cosmetic: the wall doc measured an unblocked navigation at 20.6 s, above
    ``_NAV_TIMEOUT_S``. If pydoll's private command API drifts and this silently stops working,
    every walled Board becomes a bare navigation timeout with nothing pointing at the cause — so
    the first failure is logged with its exception, once per process.
    """
    global _blocking_failed
    try:
        from pydoll.commands.network_commands import NetworkCommands

        await tab.enable_network_events()
        await tab._execute_command(NetworkCommands.set_blocked_urls(_BLOCKED))
    except Exception as exc:  # noqa: BLE001 - an optimisation, not a gate: degrade, don't die
        if not _blocking_failed:
            _blocking_failed = True
            _log.warning(
                "subresource blocking unavailable (%s: %s) — navigations will be slower and "
                "may exceed the %ss deadline",
                type(exc).__name__,
                exc,
                _NAV_TIMEOUT_S,
            )


class BrowserHTTPError(Exception):
    """A non-2xx answer from an in-page fetch. Carries the status like an HTTP error would."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"HTTP {status_code}: {body[:200]}")
        self.status_code = status_code
        self.body = body


def _default_chrome():
    """Start pydoll's Chrome (imported here so the extra is only needed when a wall is hit)."""
    try:
        from pydoll.browser import Chrome
        from pydoll.browser.options import ChromiumOptions
    except ImportError as exc:  # a missing extra, not a flaky launch — say which
        raise BrowserUnavailable(
            "the browser transport needs pydoll: pip install -e '.[scrape]' "
            "(the pipeline's scrape shards install it; the curated-feed path does not)"
        ) from exc

    options = ChromiumOptions()
    for arg in _CHROME_ARGS:
        options.add_argument(arg)
    options.start_timeout = _CHROME_START_TIMEOUT_S
    return Chrome(options=options)


# Internal seam: tests replace this with a factory returning a fake Chrome. One adapter in
# production, one in tests — the seam is real.
_chrome_factory = _default_chrome

_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_browser = None
_gate: asyncio.Semaphore | None = None
_atexit_registered = False


def _run(coro, timeout: float):
    """Run a coroutine on the browser loop from any thread, cancelling it if we give up.

    ``Future.result(timeout)`` abandons the *caller*; it does not stop the coroutine. Without the
    cancel below, a timed-out ``_open`` goes on to take a semaphore slot and open a tab nobody
    closes — four of those permanently exhaust ``_TAB_WIDTH`` and every later walled Board
    deadlocks. Cancelling delivers ``CancelledError`` into the coroutine, whose own
    ``except BaseException`` hands the slot back.
    """
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    try:
        return future.result(timeout)
    except BaseException:
        future.cancel()
        raise


def _ensure_started() -> None:
    """The process's one Chrome, started on first use, with a launch retry."""
    global _loop, _browser, _atexit_registered
    with _lock:
        if _browser is not None:
            return
        if _loop is None:
            _loop = asyncio.new_event_loop()
            threading.Thread(
                target=_loop.run_forever, name="browser-http", daemon=True
            ).start()

        async def _start():
            global _gate
            _gate = asyncio.Semaphore(_TAB_WIDTH)
            browser = _chrome_factory()
            try:
                await browser.__aenter__()
                await browser.start()
            except BaseException:
                # start() never returned, so nothing else calls stop()/cleanup() on this
                # instance: its OS process and temp profile dir would otherwise leak until
                # Python's own GC finalizer tears the dir down — racing a process that may
                # still be alive and writing into it (the "Directory not empty" OSError seen
                # in production logs). Reap both now, before the next attempt competes with
                # a leaked process for the same CPU/memory.
                try:
                    browser._browser_process_manager.stop_process()
                    browser._temp_directory_manager.cleanup()
                except BaseException:  # noqa: BLE001 - already failing; don't mask the cause
                    # DEBUG, not WARNING: this is per-Board, and an annotation is a quota
                    # (ADR-0039). The reap is the thing whose failure the comment above
                    # predicts, so it must at least be recoverable from a verbose run.
                    _log.debug("reaping a failed Chrome launch raised", exc_info=True)
                raise
            return browser

        last: Exception | None = None
        for _ in range(_LAUNCH_ATTEMPTS):
            try:
                _browser = _run(_start(), timeout=60)
                if not _atexit_registered:
                    atexit.register(shutdown)
                    _atexit_registered = True
                return
            except BrowserUnavailable:
                raise  # an install problem: retrying is theatre
            except Exception as exc:  # noqa: BLE001 - startup is the flaky part; retry it
                last = exc
        raise RuntimeError(
            f"Chrome failed to start after {_LAUNCH_ATTEMPTS} attempts"
        ) from last


def shutdown() -> None:
    """Close the browser. Registered atexit; safe to call twice."""
    global _browser
    with _lock:
        browser, _browser = _browser, None
    if browser is not None and _loop is not None:
        try:
            _run(browser.__aexit__(None, None, None), timeout=15)
        except Exception:  # noqa: BLE001, S110 - shutdown must never mask the run's real outcome
            pass


class _Page:
    """One warmed tab on one origin. ``post_json``/``get_json`` are the whole surface."""

    def __init__(self, tab, base: str) -> None:
        self._tab = tab
        self._base = base

    def post_json(self, path: str, body: dict) -> dict:
        return self._request("post", path, body)

    def get_json(self, path: str) -> dict:
        return self._request("get", path, None)

    def _request(self, method: str, path: str, body: dict | None) -> dict:
        async def _go():
            fn = getattr(self._tab.request, method)
            kwargs = {"json": body} if body is not None else {}
            return await fn(self._base + path, **kwargs)

        # One retry for pydoll's own plumbing (an occasional mis-shaped evaluate result) —
        # never for an HTTP answer: a retried 403 would not be a pass, it would be a lie.
        for attempt in (1, 2):
            try:
                r = _run(_go(), timeout=_FETCH_TIMEOUT_S)
                break
            except Exception:  # client-side fault; one stated retry
                if attempt == 2:
                    raise
        if r.status_code != 200:
            raise BrowserHTTPError(r.status_code, r.text)
        return json.loads(r.text)


@contextmanager
def origin(page_url: str):
    """Navigate a fresh blocked tab to ``page_url`` (clearing the wall), yield a :class:`_Page`.

    Each darwinbox tenant is its own subdomain, hence its own origin: clearance earned on one
    board cannot apply to the next, so every board pays exactly one navigation.
    """
    from urllib.parse import urlparse

    parsed = urlparse(page_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    _ensure_started()

    async def _open():
        await _gate.acquire()
        tab = None
        try:
            tab = await _browser.new_tab()
            await _install_blocking(tab)
            await tab.go_to(page_url, timeout=_NAV_TIMEOUT_S)
            return tab
        except BaseException:
            # Hand back the slot *and* the tab: a tab opened before a failed navigation would
            # otherwise sit open for the life of the browser.
            if tab is not None:
                try:
                    await tab.close()
                except BaseException:  # noqa: BLE001 - already failing; don't mask the cause
                    # DEBUG for the reason the reap above gives: once per walled Board.
                    _log.debug(
                        "closing the tab of a failed navigation raised", exc_info=True
                    )
            _gate.release()
            raise

    async def _close(tab):
        try:
            await tab.close()
        finally:
            _gate.release()

    tab = _run(_open(), timeout=_NAV_TIMEOUT_S + 40)
    try:
        yield _Page(tab, base)
    finally:
        try:
            _run(_close(tab), timeout=15)
        except Exception:  # noqa: BLE001 - a tab that won't close must not fail the board
            # DEBUG for the reason the reap above gives: once per walled Board. A tab that
            # will not close is also how `_TAB_WIDTH` leaks, so it must leave a trace.
            _log.debug("closing a finished board's tab raised", exc_info=True)


class _FetchResult:
    """The slice of ``curl_cffi``'s ``Response`` surface a :class:`headstart.fetcher.Fetcher`
    caller needs — ``.status_code``, ``.json()``, ``.raise_for_status()`` — so a scraper can
    treat a browser answer exactly like a curl one."""

    def __init__(self, status_code: int, data: Any, text: str = "") -> None:
        self.status_code = status_code
        self._data = data
        self.text = text

    def json(self) -> Any:
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise BrowserHTTPError(self.status_code, self.text)


class BrowserFetcher:
    """A :class:`headstart.fetcher.Fetcher` backed by one warmed tab on one origin (ADR-0056,
    deepened for ADR-0153). Implements ``fetch`` only — a browser tab is one session, not a
    multiplexed pool, and nothing calls its async half (see ``headstart.fetcher``'s module
    docstring) — and only for requests inside the origin it was opened on: every darwinbox
    tenant is its own subdomain, so one instance never needs to cover two. It leaves
    ``clear_cookies`` (ADR-0199) unimplemented too: the tab's cookies are the clearance its
    navigation earned, so a reset would re-wall it, and a no-op would claim a reset that never
    happened.

    A context manager, not a bare object, because the tab :func:`origin` opens must close
    deterministically — this wraps that contract rather than replacing it::

        with BrowserFetcher(f"{host}/ms/candidate/careers") as browser:
            response = browser.fetch("POST", f"{host}/ms/candidateapi/...", json=body)
            response.raise_for_status()
            data = response.json()

    ``fetch``'s ``url`` must share ``page_url``'s origin — that origin is the one thing the
    navigation on entry actually clears (ADR-0056), so a request outside it would be asking a
    tab for a wall it was never shown, not merely a bug in the caller's bookkeeping.
    """

    def __init__(self, page_url: str) -> None:
        self._page_url = page_url
        parts = urlsplit(page_url)
        self._origin = f"{parts.scheme}://{parts.netloc}"
        self._cm: Any = None
        self._page: _Page | None = None

    def __enter__(self) -> Self:
        self._cm = origin(self._page_url)
        self._page = self._cm.__enter__()
        return self

    def __exit__(self, *exc_info: object) -> None:
        cm, self._cm, self._page = self._cm, None, None
        if cm is not None:
            cm.__exit__(*exc_info)

    def fetch(
        self, method: str, url: str, *, json: dict | None = None, **_ignored: Any
    ) -> _FetchResult:
        if self._page is None:
            raise RuntimeError("BrowserFetcher.fetch() called outside its `with` block")
        if not url.startswith(self._origin):
            raise ValueError(
                f"{url!r} is outside this tab's cleared origin {self._origin!r}"
            )
        path = url[len(self._origin) :]
        try:
            data = (
                self._page.get_json(path)
                if method.upper() == "GET"
                else self._page.post_json(path, json or {})
            )
        except BrowserHTTPError as exc:
            return _FetchResult(exc.status_code, None, text=exc.body)
        return _FetchResult(200, data)
