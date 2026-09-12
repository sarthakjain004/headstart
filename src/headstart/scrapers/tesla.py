"""Tesla careers scraper — a single-source ats (ADR-0139): ``www.tesla.com`` runs its own
in-house board (branded ``moka-version`` in the DOM — a white-labelled Moka HR front end — but
the API is Tesla's own path, not a shared Moka tenant surface), one company, never a second
tenant.

**No ordinary HTTP client can read this board at all — verified 2026-09-11.** The whole
``www.tesla.com`` origin sits behind Akamai Bot Manager: a bare ``curl`` (no special headers)
gets a straight ``403`` from ``AkamaiGHost`` on *every* path tried, including the plain HTML
homepage and ``/robots.txt``, not just the API. Swapping in ``curl_cffi``'s Chrome TLS
impersonation (this repo's own ``http.py`` transport) gets further — past the raw 403 — but the
listing endpoint (``GET /cua-api/apps/careers/state``) then answers ``429`` with a body of
``{"cpr_chlge":"true","t":"<ts>"}`` even after first visiting the homepage in the same session to
pick up its Akamai cookies (``_abck``, ``bm_s``, ``bm_sc``, …). That is Akamai's behavioural
challenge, not a rate limit in the ordinary sense: **any explicitly-issued request gets it**,
proven with a real, JS-capable, cookie-bearing browser (pydoll driving actual Google Chrome,
headful — headless was not tried, since ADR-0056's darwinbox precedent already found Akamai/
Cloudflare-class walls headless-hostile): navigating to
``https://www.tesla.com/careers/search/`` succeeds and the *page's own* first-load call to the
state endpoint gets a clean ``200`` (captured via the CDP ``Network`` domain), but then issuing a
**second** explicit request to that exact same URL from inside that same warmed browser — via
pydoll's own ``tab.request`` (itself a page-context ``fetch``) — gets the identical
``429 {"cpr_chlge":"true"}``, and so does an explicit request to a *different*, not-yet-hit URL
(the per-job detail endpoint, below). Akamai's sensor is flagging the call site, not the
cookies or the IP: only a request the page issues for itself, on its own first navigation, is
trusted. (A third-party MIT scraper, ``kalil0321/ats-scrapers``, independently reached the same
wall and worked around it with a purpose-built stealth Chromium fork plus a residential proxy —
consistent with, not contradicted by, what a stock pydoll/Chrome session finds here.)

**So this scraper never issues an HTTP request of its own.** It drives a real, headful Chrome
(pydoll, matching ``headstart.browser_http``'s ADR-0056 precedent for a wall that "admits a
genuine Chrome and nothing else" — though the mechanism here is a different shape, see below),
navigates once to the careers search page, and reads the body of that page's *own* network
response for the state endpoint straight off the CDP ``Network`` domain
(``Network.getResponseBody``) — no ``fetch`` of any kind is injected. This is a different
contract from ``browser_http.origin()`` (navigate, then explicitly ``post_json``/``get_json`` on
the warmed tab): here the explicit-request half of that contract is exactly what Tesla's wall
refuses, so ``browser_http`` isn't reusable as-is and this module keeps its own minimal,
single-purpose Chrome lifecycle rather than bending that shared one to a second shape for a
single caller.

**One document, no pagination.** ``/cua-api/apps/careers/state`` returns the *entire* catalog —
8,105 listings measured 2026-09-11 — as ``{"listings": [...], "lookup": {...}}``. Each listing is
already minified onto short keys (presumably to keep this one-shot payload small): ``id``, ``t``
(title), ``dp`` (department id), ``f`` (an internal "function"/team id — 17 distinct values, not
resolvable to a name anywhere in this payload, so it is not carried onto ``Job``), ``l``
(location id), ``y`` (an employment-type id: measured 1=fulltime 7,277, 2=parttime 113,
3=intern 576, 4=seasonal 139 — matching ``lookup.types`` exactly), ``sp`` (a per-listing sort
index, not a real field), and ``pu`` (an ISO application deadline, non-null on only 39 of 8,105 —
kept out; it is not a posting date). ``lookup.locations`` resolves 13,234 ids to "City, Region"
strings (no country) and is not exhaustive: 4 of the 1,309 location ids a live listing set
referenced were absent from it, so a resolved ``location`` can be ``None``.

**No ``posted_at``.** Neither the listing nor the per-job detail payload (below) states when a
posting went up — only ``postUntilDate``, an application deadline, which is a different fact and
is not substituted in.

**The listing carries no description, and a per-job detail pass is not implemented here.** A
per-job detail page (``GET /cua-api/careers/job/{id}``, reached by *navigating* to
``https://www.tesla.com/careers/search/job/{slug}-{id}``) does carry one — verified live on one
job, 200 with ``jobDescription``/``jobResponsibilities``/``jobRequirements`` HTML fields — but
getting it costs a **full browser navigation per job**, not a cheap JSON GET: the same wall that
blocks a second explicit request off the listing page blocks one issued from a job page too
(measured on the same job id). At 8,105 postings that is thousands of navigations every run,
which no other ATS in this repo pays and which would not fit a nightly pipeline's time
budget. ``has_detail_pass`` therefore stays ``False`` and every Tesla ``Job.description`` is
``None`` in this version — a deliberate scope cut, not an oversight, and a natural place to
revisit if per-job descriptions turn out to matter enough to budget for.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from typing import Any

from headstart import log
from headstart.models import Job, is_remote
from headstart.scrapers.base import BaseScraper

_log = log.get(__name__)

#: The one and only tenant (ADR-0139) — fixed, never discovered.
SLUG = "www.tesla.com"
_SEARCH_URL = f"https://{SLUG}/careers/search/"
#: Matched against a response's URL with `.endswith`, since the full path
#: (`/cua-api/apps/careers/state`) is stable but asserting the scheme+host too would just repeat
#: `_SEARCH_URL`'s host for no extra safety.
_STATE_URL_SUFFIX = "apps/careers/state"

_NAV_TIMEOUT_S = 30  # generous: this is one navigation per run, not one per Board
_STATE_WAIT_S = 20  # how long to wait for the page's own state call to complete
_CHROME_START_TIMEOUT_S = 30

# Headful, like `browser_http`'s darwinbox precedent (ADR-0056) — Chrome under CDP automation is
# not exempt from Akamai's sensor here regardless, since even the natural first-load request is
# what this module leans on, not stealth from these flags alone.
_CHROME_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--lang=en-US",
    "--window-size=1400,1000",
    "--no-sandbox",  # GitHub runners restrict unprivileged user namespaces
    "--disable-dev-shm-usage",
]

_TITLE_ID = re.compile(r"^(?P<slug>.+)-(?P<id>\d+)$")


class TeslaBrowserUnavailable(Exception):
    """The browser transport cannot run here — pydoll is not installed.

    Mirrors `headstart.browser_http.BrowserUnavailable`: distinct from a launch failure so the
    caller doesn't retry an install problem three times before saying what it actually is.
    """


def _default_chrome():
    try:
        from pydoll.browser import Chrome
        from pydoll.browser.options import ChromiumOptions
    except ImportError as exc:
        raise TeslaBrowserUnavailable(
            "the Tesla scraper needs pydoll: pip install -e '.[scrape]' "
            "(the pipeline's scrape shards install it; the curated-feed path does not)"
        ) from exc

    options = ChromiumOptions()
    for arg in _CHROME_ARGS:
        options.add_argument(arg)
    options.start_timeout = _CHROME_START_TIMEOUT_S
    return Chrome(options=options)


# Internal seam: tests replace this with a factory returning a fake Chrome, matching
# `browser_http`'s own `_chrome_factory` pattern.
_chrome_factory = _default_chrome

_lock = threading.Lock()
_loop: Any = None
_browser: Any = None


def _run(coro: Any, timeout: float) -> Any:
    """Run a coroutine on the browser's own loop from any thread; cancel it if we give up."""
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    try:
        return future.result(timeout)
    except BaseException:
        future.cancel()
        raise


def _ensure_started() -> None:
    """The process's one Chrome, started on first use. No launch retry: this is one board, one
    navigation a run — a retry ladder built for a shard hammering hundreds of Boards is more
    machinery than a single navigation earns."""
    global _loop, _browser
    with _lock:
        if _browser is not None:
            return
        if _loop is None:
            _loop = asyncio.new_event_loop()
            threading.Thread(
                target=_loop.run_forever, name="tesla-browser", daemon=True
            ).start()

        async def _start():
            browser = _chrome_factory()
            await browser.__aenter__()
            await browser.start()
            return browser

        _browser = _run(_start(), timeout=60)


def shutdown() -> None:
    """Close the browser. Not registered atexit (unlike `browser_http`): a single navigation a
    run means the process exits shortly after anyway, and tests call this directly to reset."""
    global _browser
    with _lock:
        browser, _browser = _browser, None
    if browser is not None and _loop is not None:
        try:
            _run(browser.__aexit__(None, None, None), timeout=15)
        except Exception:  # noqa: BLE001, S110 - shutdown must never mask the run's real outcome
            pass


def _fetch_state_json() -> dict[str, Any]:
    """Navigate the careers search page once, and read the body of *its own* first-load call to
    the state endpoint off the CDP Network domain. See the module docstring for why no request is
    ever issued explicitly — every one gets Akamai's ``429 {"cpr_chlge":"true"}`` challenge.
    """
    from pydoll.commands.network_commands import NetworkCommands

    _ensure_started()

    async def _go() -> dict[str, Any]:
        tab = await _browser.new_tab()
        seen: dict[str, Any] = {}
        finished = asyncio.Event()

        async def on_response(event: dict) -> None:
            url = event["params"]["response"]["url"]
            if url.endswith(_STATE_URL_SUFFIX):
                seen["request_id"] = event["params"]["requestId"]
                seen["status"] = event["params"]["response"]["status"]

        async def on_finished(event: dict) -> None:
            if event["params"]["requestId"] == seen.get("request_id"):
                finished.set()

        try:
            await tab.enable_network_events()
            await tab.on("Network.responseReceived", on_response)
            await tab.on("Network.loadingFinished", on_finished)
            await tab.go_to(_SEARCH_URL, timeout=_NAV_TIMEOUT_S)
            await asyncio.wait_for(finished.wait(), timeout=_STATE_WAIT_S)
            if seen.get("status") != 200:
                raise RuntimeError(
                    f"the careers page's own state call answered {seen.get('status')}"
                )
            # `_execute_command` is pydoll's private command API, same as `browser_http`'s own
            # `_install_blocking` — if it drifts, this raises here rather than silently returning
            # nothing, which is why the surrounding `_go` isn't wrapped any looser than it is.
            body = await tab._execute_command(
                NetworkCommands.get_response_body(seen["request_id"])
            )
            return json.loads(body["result"]["body"])
        finally:
            await tab.close()

    return _run(_go(), timeout=_NAV_TIMEOUT_S + _STATE_WAIT_S + 15)


def _job_url(job_id: str, title: str) -> str:
    """``/careers/search/job/{title-slug}-{id}`` — verified live 2026-09-11 (title-slug is
    cosmetic; Tesla's own app resolves the posting by the trailing id regardless of the words
    before it)."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return f"{_SEARCH_URL}job/{slug}-{job_id}" if slug else f"{_SEARCH_URL}job/{job_id}"


class TeslaScraper(BaseScraper):
    """Tesla's own in-house careers system — a single-source ats (ADR-0139)."""

    ats = "tesla"

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        """Always the fixed host: a single-source board is never discovered, so nothing about
        the ledger row's own columns should be able to move the slug (ADR-0139)."""
        return SLUG

    def url(self) -> str:
        return _SEARCH_URL

    def alias_key(self) -> str | None:
        """This board's own slug: a single-source board has no sibling tenant to alias against
        (ADR-0139), and the base implementation's live probe would hit the same Akamai wall this
        scraper exists to work around."""
        return self.slug

    def fetch_raw(self) -> Any:
        return _fetch_state_json()

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        lookup = raw.get("lookup") or {}
        locations = lookup.get("locations") or {}
        departments = lookup.get("departments") or {}
        types = lookup.get("types") or {}
        jobs: list[Job] = []
        for entry in raw.get("listings") or []:
            job_id = entry.get("id")
            title = (entry.get("t") or "").strip()
            if not job_id or not title:
                continue
            location = locations.get(entry.get("l"))
            jobs.append(
                Job(
                    id=f"{self.ats}:{self.slug}:{job_id}",
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=location,
                    remote=is_remote(location),
                    department=departments.get(entry.get("dp")),
                    url=_job_url(job_id, title),
                    posted_at=None,  # not exposed anywhere on this board (module docstring)
                    scraped_at=scraped_at,
                    employment_type=types.get(str(entry.get("y"))),
                )
            )
        return jobs
