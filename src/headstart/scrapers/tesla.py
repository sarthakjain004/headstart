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

**The listing carries no description; the detail pass reads it in batches from one warmed tab**
(ADR-0228, issue #553). A per-job detail (``GET /cua-api/careers/job/{id}``) carries
``jobDescription``, ``jobResponsibilities``, ``jobRequirements`` and ``jobCompensationAndBenefits``
as HTML. One navigation per job would be thousands a run, but the wall is not per request:
measured live 2026-09-22 and 2026-09-25 (captures kept locally, not committed), a tab
that has *navigated to one job page* can then fetch other ids with page-JS ``fetch()`` — 200 on
every id of batches of 10, 25 and 50 in 1.5-2.0 s each — while the same fetches from the search
page, or after ten idle seconds, answer 404 (the control). So the pass navigates to a job page,
then sends ``detail_batch_size`` ids per in-page ``Promise.all``, and navigates again only when a
batch comes back mostly non-200 (the trust lapsed).

**Over the batch bound the origin refuses the IP, not the request.** A batch of 100 answered 403
on 69 ids and the next, of 200, on all 200; minutes later ``/careers/search/`` itself was a hard
403 from that IP, so the listing dies with the details. The bound is therefore small, batches are
paced, and any 403/429 is a wall: the Chrome restarts on the spare egress
(:mod:`headstart.spare_egress`, ``--proxy-server``) and the batch is retried, rotating the IP on
each further wall. When no route is left the pass stops with what it has
(:class:`~headstart.scrapers.base.DetailBatchWalled`).
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

from headstart import log, spare_egress
from headstart.models import Job, html_to_text, is_remote
from headstart.scrapers.base import (
    BaseScraper,
    DetailBatchWalled,
    DetailLost,
    DetailRequest,
)

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

_DETAIL_URL = f"https://{SLUG}/cua-api/careers/job/"
#: Ids per in-page ``Promise.all`` (ADR-0228). 10, 25 and 50 answered 200 on every id; 100 drew 403
#: on 69 of 100 and got the IP refused. Half of the largest size that worked, since 50 was
#: measured once.
_BATCH_SIZE = 25
#: Pause after each batch. Unmeasured insurance: back-to-back batches of 10/25/50 passed, but the
#: cost of being wrong is the whole origin for an IP.
_BATCH_PAUSE_S = 0.5
_SETTLE_S = 3  # after a job-page navigation, before the tab's fetches are tried
_BATCH_TIMEOUT_S = 60
#: Statuses that mean the origin refused the IP — ``404`` is not among them: the un-warmed tab
#: answers 404, and so does a posting that closed.
_WALL_STATUSES = frozenset({403, 429})
#: The walls one operation rides out before giving up: the first moves onto the spare egress, each
#: further one rotates it.
_EGRESS_ATTEMPTS = 3
#: :mod:`headstart.spare_egress`'s key for this origin's wall.
_GROUP = "tesla"

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
    if _route:
        # Chrome takes `socks5://`, not `socks5h://`, and resolves through a SOCKS5 proxy anyway.
        options.add_argument(
            f"--proxy-server={_route.replace('socks5h://', 'socks5://')}"
        )
    return Chrome(options=options)


# Internal seam: tests replace this with a factory returning a fake Chrome, matching
# `browser_http`'s own `_chrome_factory` pattern.
_chrome_factory = _default_chrome

_lock = threading.Lock()
_loop: Any = None
_browser: Any = None
#: The tab the detail batches run in, and the proxy the browser was launched on (None: direct).
#: Chrome fixes its proxy at launch, so a route change is a browser restart, never a setting.
#: Not guarded beyond `_lock` on start/stop: one Board, one scrape thread, drives them.
_tab: Any = None
_route: str | None = None


class TeslaWalled(Exception):
    """The origin refused this route: a 403/429, or a page that never answered."""

    def __init__(self, status: int, message: str | None = None) -> None:
        # `message` for a wall read from silence: `status` still drives the egress logic, but
        # the text must not claim an answer the origin never gave.
        super().__init__(message or f"the origin answered {status}")
        self.status = status


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
    global _loop, _browser, _route
    with _lock:
        if _browser is not None:
            return
        # Resolved here, off the browser's loop: `proxy_for` blocks while WARP dials.
        _route = spare_egress.proxy_for(_GROUP)
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
    global _browser, _tab
    with _lock:
        browser, _browser = _browser, None
        _tab = None
    if browser is not None and _loop is not None:
        try:
            _run(browser.__aexit__(None, None, None), timeout=15)
        except Exception:  # noqa: BLE001, S110 - shutdown must never mask the run's real outcome
            pass


def _with_egress(operation: Callable[[], Any]) -> Any:
    """Run ``operation``, and when the origin walls this route, retry it on the spare egress.

    The first wall moves the Chrome onto the spare egress (:func:`spare_egress.mark_walled`, then a
    relaunch with ``--proxy-server``); each further one rotates its IP. Raises the last
    :class:`TeslaWalled` when the attempts are spent or no spare egress can be brought up. The
    rotation runs outside ``riding_the_tunnel``, which it waits on to drain.
    """
    attempt = 0
    while True:
        _ensure_started()
        try:
            with spare_egress.riding_the_tunnel(_route):
                return operation()
        except TeslaWalled as wall:
            attempt += 1
            if attempt > _EGRESS_ATTEMPTS:
                raise
            spare_egress.mark_walled(_GROUP, wall.status)
            was_on_spare = _route is not None
            shutdown()
            if was_on_spare:
                if not spare_egress.rotate(SLUG):
                    raise
            elif spare_egress.proxy_for(_GROUP) is None:
                raise


def _fetch_state_json() -> dict[str, Any]:
    return _with_egress(_read_state_json)


def _read_state_json() -> dict[str, Any]:
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
            try:
                await asyncio.wait_for(finished.wait(), timeout=_STATE_WAIT_S)
            except TimeoutError:
                # A refused IP gets a hard 403 page whose own state call never fires (measured
                # 2026-09-25), so silence is the wall's usual shape here, not a slow load.
                raise TeslaWalled(
                    403, f"no state call within {_STATE_WAIT_S}s (read as a wall)"
                ) from None
            if seen.get("status") in _WALL_STATUSES:
                raise TeslaWalled(seen["status"])
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


_BATCH_JS = """
(async () => {
  const urls = %s;
  const out = await Promise.all(urls.map(async u => {
    try {
      const r = await fetch(u, {credentials: 'include'});
      return {s: r.status, t: r.status === 200 ? await r.text() : ''};
    } catch (e) { return {s: -1, t: String(e)}; }
  }));
  return JSON.stringify(out);
})()
"""


class _BatchResponse:
    """What :meth:`TeslaScraper.read_detail` reads off a batch member: ``status_code``, ``text``
    and ``json()``, the shape of a curl response."""

    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text

    def json(self) -> Any:
        return json.loads(self.text)


def _mostly_refused(rows: Sequence[dict[str, Any]]) -> bool:
    return sum(r["s"] == 200 for r in rows) * 2 < len(rows)


def _read_batch(urls: Sequence[str], page_url: str) -> list[dict[str, Any]]:
    """The status and body of each of ``urls``, fetched in one in-page ``Promise.all`` from a tab
    that has navigated to ``page_url`` (a job page — the navigation is what earns the tab its
    trust, ADR-0228). Raises :class:`TeslaWalled` on any 403/429.

    A batch that comes back mostly non-200 with no wall status means the tab's trust lapsed: it
    navigates again and tries the same batch once more. A second mostly-non-200 answer is treated
    as a wall (:class:`TeslaWalled` 404), so the spare egress is tried before the pass gives up.
    """

    async def evaluate() -> list[dict[str, Any]]:
        result = await _tab.execute_script(
            _BATCH_JS % json.dumps(list(urls)),
            return_by_value=True,
            await_promise=True,
        )
        rows = json.loads(result["result"]["result"]["value"])
        if len(rows) != len(urls):
            raise RuntimeError(
                f"sent {len(urls)} ids and read {len(rows)} answers back"
            )
        walled = next((r["s"] for r in rows if r["s"] in _WALL_STATUSES), None)
        if walled is not None:
            raise TeslaWalled(walled)
        return rows

    async def navigate() -> None:
        await _tab.go_to(page_url, timeout=_NAV_TIMEOUT_S)
        await asyncio.sleep(_SETTLE_S)

    async def go() -> list[dict[str, Any]]:
        global _tab
        if _tab is None:
            _tab = await _browser.new_tab()
            await navigate()
        rows = await evaluate()
        if _mostly_refused(rows):
            # Otherwise silent: a lapse the second navigation cures leaves no other trace.
            _log.info(
                f"{sum(r['s'] != 200 for r in rows)} of {len(rows)} in a batch refused without a "
                "wall status — tab trust lapsed, navigating again"
            )
            await navigate()
            rows = await evaluate()
            if _mostly_refused(rows):
                # An untrusted tab answers 404 (the measured shape of the un-warmed fetch), so a
                # second mostly-404 batch is a wall that came without a wall status, not a batch
                # of closed postings: stop navigating twice per batch through the whole board.
                raise TeslaWalled(404)
        return rows

    return _run(go(), timeout=2 * (_NAV_TIMEOUT_S + _SETTLE_S + _BATCH_TIMEOUT_S))


class TeslaScraper(BaseScraper):
    """Tesla's own in-house careers system — a single-source ats (ADR-0139)."""

    COMPANY = "Tesla"

    ats = "tesla"
    has_detail_pass = True  # per-Job fetch fills `description` (ADR-0050, ADR-0228)
    detail_batch_size = _BATCH_SIZE
    # single-source ats (ADR-0139) — one tenant, so the host is a literal, not a wildcard.
    # scraper builds f"{_SEARCH_URL}job/{title-slug}-{id}" (job_url below, via _job_url).
    url_shape = r"https://www\.tesla\.com/careers/search/job/[\w-]+-\d+"

    @staticmethod
    def slug_from(tenant: str, url: str) -> str:
        """Always the fixed host: a single-source board is never discovered, so nothing about
        the ledger row's own columns should be able to move the slug (ADR-0139)."""
        return SLUG

    def url(self) -> str:
        return _SEARCH_URL

    def job_url(self, job_id: str, title: str) -> str:
        """Delegates to the module-level :func:`_job_url`, which does the real construction and
        is exercised directly by ``tests/test_tesla.py`` with no scraper instance in hand."""
        return _job_url(job_id, title)

    def alias_key(self) -> str | None:
        """This board's own slug: a single-source board has no sibling tenant to alias against
        (ADR-0139), and the base implementation's live probe would hit the same Akamai wall this
        scraper exists to work around."""
        return self.slug

    def fetch_raw(self) -> Any:
        state = _fetch_state_json()
        listings = state.get("listings") or []
        departments = (state.get("lookup") or {}).get("departments") or {}
        self._job_pages = {
            _DETAIL_URL + str(e["id"]): _job_url(str(e["id"]), e.get("t") or "")
            for e in listings
            if e.get("id")
        }
        # Details supply the description only, and `parse` reads title and department off the
        # listing (ADR-0166 §3 makes the tech gate exact here). ADR-0048's skip is taken: a held
        # description is not fetched again. Reported, not marked truncated — a missing detail
        # costs a Job its description, not its place in the list (ADR-0053).
        if self.have_details is None:
            # Not the pipeline: the tech gate and the held skip are both off, so the pass would
            # fetch every posting, ~330 batches from one IP, and one overshoot blocks the origin.
            return state
        details = self.run_detail_pass(
            listings,
            key_of=lambda e: str(e["id"]) if e.get("id") else None,
            what="detail pages",
            title_of=lambda e: e.get("t"),
            department_of=lambda e: departments.get(e.get("dp")),
            skip_held=True,
        )
        state["details"] = dict(details)
        return state

    def detail_request(self, item: dict) -> DetailRequest:
        if not item.get("id"):
            raise DetailLost("no job id")
        return DetailRequest(_DETAIL_URL + str(item["id"]))

    def read_detail(self, item: dict, response: Any) -> dict:
        detail = response.json()
        sections = (
            html_to_text(detail.get(key))
            for key in (
                "jobDescription",
                "jobResponsibilities",
                "jobRequirements",
                "jobCompensationAndBenefits",
            )
        )
        description = "\n\n".join(s for s in sections if s)
        if not description:
            raise DetailLost("no description")
        return {"description": description}

    def fetch_detail_batch(self, requests: Sequence[DetailRequest]) -> list[Any]:
        urls = [request.url for request in requests]
        try:
            rows = _with_egress(lambda: _read_batch(urls, self._job_pages[urls[0]]))
        except TeslaWalled as wall:
            raise DetailBatchWalled(f"{wall} on every route tried") from wall
        time.sleep(_BATCH_PAUSE_S)
        return [
            RuntimeError(row["t"])
            if row["s"] == -1
            else _BatchResponse(row["s"], row["t"])
            for row in rows
        ]

    def parse(self, raw: Any, scraped_at: str) -> list[Job]:
        listings = raw.get("listings")
        if listings is None and "listings" not in raw:
            # A 200 with a body that isn't the state document's usual shape — plausibly Akamai
            # answering behind our back with something that isn't the app's real payload. Not
            # marked truncated: what a missing `listings` key means here hasn't been measured,
            # and ADR-0053's exclusion has no drain, so guessing wrong would hold this Board out
            # of eviction scope forever (same call ashby.py's own `note_unreadable_board` makes
            # for the analogous missing-container case).
            self.note_unreadable_board(
                "a payload with a `listings` array", "no `listings` key"
            )
            return []
        if not listings:
            # Present but empty. This board has never measured anywhere near zero — 8,105 and
            # 8,115 across the two live runs this scraper's docs record — so a captured state
            # response with none of that scale's postings is a capture defect, not Tesla
            # genuinely hiring nobody, and there is no stated total to measure a shortfall
            # against (module docstring): the second excluded shape `mark_truncated_unless_
            # negligible`'s own docstring names, so this goes straight to `mark_truncated`
            # rather than being silently accepted as this Board's real, authoritative state —
            # which would otherwise evict every already-indexed Tesla job.
            self.mark_truncated(
                "captured a 200 state response with zero listings — treating that as "
                "authoritative would evict every already-indexed Tesla job"
            )
            return []
        lookup = raw.get("lookup") or {}
        locations = lookup.get("locations") or {}
        departments = lookup.get("departments") or {}
        types = lookup.get("types") or {}
        details = raw.get("details") or {}
        jobs: list[Job] = []
        for entry in listings:
            job_id = entry.get("id")
            title = (entry.get("t") or "").strip()
            if not job_id or not title:
                continue
            location = locations.get(entry.get("l"))
            jobs.append(
                Job(
                    id=self.job_id(job_id),
                    ats=self.ats,
                    company=self.company,
                    title=title,
                    location=location,
                    remote=is_remote(location),
                    department=departments.get(entry.get("dp")),
                    url=self.job_url(job_id, title),
                    posted_at=None,  # not exposed anywhere on this board (module docstring)
                    scraped_at=scraped_at,
                    description=(details.get(str(job_id)) or {}).get("description"),
                    employment_type=types.get(str(entry.get("y"))),
                )
            )
        self.note_unread_rows(
            len(listings) - len(jobs), len(listings), "with no id/title"
        )
        return jobs

    def _salary_field(self, raw: Any) -> str | None:
        # Not yet measured: no structured compensation field has been looked for in the state
        # document's `listings` entries. Needs its own measurement pass before this can claim
        # more.
        return None
