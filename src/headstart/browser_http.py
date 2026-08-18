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
probe legs were lost to it), a dedicated asyncio loop in a daemon thread so harvest's worker
threads can call this synchronously, a tab-count semaphore (probe-measured width), heavy
subresource blocking per tab (no media, no JS — arm A renders nothing, and Turnstile never runs),
and a hard per-board navigation deadline. HTTP answers are never retried — a retried 403 is not a
pass; the one retry is for pydoll's own occasional evaluate-shape hiccup, a client-side fault.

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

from headstart import log

_log = log.get(__name__)

_NAV_TIMEOUT_S = 20  # per-board deadline, from the shape probe's _DEADLINE
_FETCH_TIMEOUT_S = 30
_TAB_WIDTH = 4  # probe-measured: width 4-6 holds; stay at the safe end
_LAUNCH_ATTEMPTS = 3

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
            await browser.__aenter__()
            await browser.start()
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
        except Exception:  # noqa: BLE001, S110
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
            except Exception:
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
                except BaseException:  # noqa: BLE001, S110
                    pass
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
        except Exception:  # noqa: BLE001, S110
            pass
