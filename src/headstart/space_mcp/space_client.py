"""How the Space MCP server reaches the Space: read routes only, over HTTPS or in process.

:class:`SpaceClient` is built once per tool call, which is what gives a call its **deadline**
(:data:`CALL_DEADLINE_S` in all) and lets it know whether **the app has already answered in this
call**. Its one
method, :meth:`SpaceClient.read`, takes a route from a closed set and query parameters, and
answers the decoded JSON or raises a :class:`SpaceError` whose message is a sentence for the model.
There is no way to name a path, a verb or a body: the client is read-only by its shape, which is
ADR-0137's argument for `resume_mcp.account.Account` applied to the Space.

**No credential.** The routes it reads are public (ADR-0258: the owner wants anyone to be able
to use this server), so it sends none; a 401 means the Space has not deployed that yet.

**Who answered.** The app marks every reply ``X-HeadStart: app; agent-api=N`` (ADR-0253). A reply
without it came from Hugging Face's edge in front of a Space that is booting or asleep — or from an
app older than the marker, which is told apart because it answers with its own JSON 401 (its
sign-in wall). An app reply whose ``agent-api`` is below :data:`AGENT_API` stops the call: an older
Space would silently ignore ``strict=1``, and "stricter than the browser, never looser" is the
promise.

**Waiting.** A boot measured 4 min 13 s (2026-09-28, the Space's own run log), longer than any
agent should sit silently, so a call waits at most ``deadline_s`` and then says the Space is
starting. A timeout *after* the app has answered in this call is a failure, not a boot: a slow
route is not re-sent to a free CPU Space three more times. In process (:func:`wsgi_fetch`) there
is no boot to wait out, and a read still running at the deadline ends the call with a sentence
saying so (ADR-0276).

**Logs** name the route, the status, the attempt and the milliseconds — never a parameter, which
may be someone's search.
"""

from __future__ import annotations

import contextvars
import gzip
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from headstart import log

_log = log.get(__name__)

SPACE_URL = "https://imposeidon-headstart-search.hf.space"

#: The agent contract this server needs (ADR-0253): `strict=1`, `/companies/lookup`, and each
#: suggestion's `match` and `board_keys`; since 2, `counts=total` on `/facets` (ADR-0274); since
#: 3, `country` (ADR-0273); since 4, `/job` and `like=` (ADR-0277); since 5,
#: `/companies/locations` (ADR-0275); since 6, its places by country and `/companies/levels`
#: (ADR-0323); since 7, `/requirements` (ADR-0324); since 8, `/hot`'s `opened_less_closed`
#: lens (ADR-0321); since 9, `family=` without `board=`, `max_age_days`,
#: `required_years_at_least` and `exclude_company` (ADR-0322); since 10, each country's cities
#: on `/companies/locations` (ADR-0331); since 11, `/requirements`' one Job per requisition
#: under `jobs` keys (ADR-0332); since 12, `/trends`' one opened and closed per category in either
#: view (ADR-0336); since 13, `strict=1` refusing a parameter name the Space does not read
#: (ADR-0334); since 14, a sorted `/search` under a query ordering only rows scoring at least
#: `job_search.SORT_FLOOR` (ADR-0338); since 15, `operators`, `operators_left_out` and
#: `/hot`'s `operator_unverified` (ADR-0335); since 16, `work_authorization` on `/search` and
#: `/facets`, and on each `/job` and `/requirements` answer (ADR-0333); since 17, `per_company`
#: on `/search` and `/requirements` (ADR-0352); since 18, `include_non_tech` and `non_tech_left_out`
#: on `/facets` and `/requirements` (ADR-0349); since 19, `/hot`'s `opened_fresh` and
#: `opened_found_late` (ADR-0351); since 20, `may_offer_sponsorship` and offers read against each
#: Job's place and title (ADR-0353); since 21, `/facets`' `places=1` (ADR-0355); since 22,
#: `/trends`' tracked-roles first row on one basis and `/hot`'s `operator_unverified` off a
#: Board's own label (ADR-0366). The app states the one it serves on every reply.
AGENT_API = 22

#: The measured boot, said when a call gives up waiting for one.
BOOT_MEASURED = "a boot measured 4 min 13 s on 2026-09-28"

#: How long one tool call may take in all, over HTTPS or in process (ADR-0276). Claude Code and
#: the MCP Inspector stop waiting for a request at 60 s (measured 2026-09-29), and the hosted
#: route may first wait 10 s for a place, so an answer later than this reaches no one.
CALL_DEADLINE_S = 45.0

#: How many in-process reads that outlived their call may still be running before no new read
#: starts (ADR-0276). A thread cannot be stopped, so each keeps a CPU busy until it finishes, and
#: the Space has two.
ABANDONED_READS_CAP = 2

_MARKER = "x-headstart"
_AGENT_API_IN_MARKER = re.compile(r"agent-api=(\d+)")


class SpaceRoute(StrEnum):
    """Every route this server may read — all read-only, Account-free and public."""

    SEARCH = "/search"
    FACETS = "/facets"
    TRENDS = "/trends"
    HOT = "/hot"
    COMPANIES_SUGGEST = "/companies/suggest"
    COMPANIES_LOOKUP = "/companies/lookup"
    JOB = "/job"
    COMPANIES_LOCATIONS = "/companies/locations"
    COMPANIES_LEVELS = "/companies/levels"
    REQUIREMENTS = "/requirements"


@dataclass(frozen=True)
class Reply:
    """One HTTP answer: its status, its headers (names lower-cased) and its body."""

    status: int
    headers: Mapping[str, str]
    body: bytes


#: The port: ``(url, headers, timeout_s) -> Reply``. It answers every HTTP status as a
#: :class:`Reply` and raises ``OSError`` (``TimeoutError`` included) only when no answer came back
#: — or a :class:`SpaceError` when it knows why none will: :func:`wsgi_fetch` does, in process.
Fetch = Callable[[str, Mapping[str, str], float], Reply]


def _decoded(headers: Mapping[str, str], body: bytes) -> bytes:
    """``body`` unzipped when the Space gzipped it — it does for a client that asks (ADR-0251),
    which takes a ~400 kB Trends answer to a fraction of that — and as sent otherwise."""
    if headers.get("content-encoding", "").lower() == "gzip":
        return gzip.decompress(body)
    return body


def urllib_fetch(url: str, headers: Mapping[str, str], timeout_s: float) -> Reply:
    """The production :data:`Fetch`: one GET over the standard library."""
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            named = {k.lower(): v for k, v in response.headers.items()}
            return Reply(response.status, named, _decoded(named, response.read()))
    except urllib.error.HTTPError as exc:
        named = {k.lower(): v for k, v in (exc.headers or {}).items()}
        return Reply(exc.code, named, _decoded(named, exc.read()))


#: The WSGI environ key :func:`wsgi_fetch` sets on every read it makes, so the app can tell its
#: own tools' reads from a caller's. A caller sets only the ``HTTP_*`` keys of an environ, through
#: its headers, never this one.
IN_PROCESS_READ = "headstart.space_mcp.in_process_read"


class AbandonedReads:
    """The in-process reads whose call stopped waiting and which are still running (ADR-0276).
    :func:`wsgi_fetch` refuses a new read while ``cap`` of them run, and the Space's `/mcp` route
    starts a description scan only when none does (ADR-0325). A read is known by the
    ``threading.Event`` it sets when it finishes; one lock orders giving up on it against its
    finishing, so a read is counted exactly when its call gave up on it before it finished."""

    def __init__(self, cap: int = ABANDONED_READS_CAP) -> None:
        self.cap = cap
        self._abandoned: set[threading.Event] = set()
        self._changed = threading.Condition()

    @property
    def running(self) -> int:
        with self._changed:
            return len(self._abandoned)

    def full(self) -> bool:
        with self._changed:
            return len(self._abandoned) >= self.cap

    def give_up(self, finished: threading.Event) -> int | None:
        """Count the read that sets ``finished`` as abandoned, unless it has finished meanwhile:
        how many are now running, or None when it had finished."""
        with self._changed:
            if finished.is_set():
                return None
            self._abandoned.add(finished)
            return len(self._abandoned)

    def finish(self, finished: threading.Event) -> bool:
        """Set ``finished``; True when its call had given up on the read, which then stops
        counting."""
        with self._changed:
            finished.set()
            if finished not in self._abandoned:
                return False
            self._abandoned.discard(finished)
            self._changed.notify_all()
            return True

    def wait_until_none(self, timeout_s: float) -> bool:
        """True once no abandoned read is running; False if ``timeout_s`` passed first."""
        with self._changed:
            return self._changed.wait_for(
                lambda: not self._abandoned, timeout=timeout_s
            )


def wsgi_fetch(wsgi_app: Callable, abandoned: AbandonedReads | None = None) -> Fetch:
    """The :data:`Fetch` for this server when the Space itself serves it (ADR-0267): each read is
    a request to ``wsgi_app`` in process, with no cookie, so no Account reaches an answer.
    Werkzeug is imported here, not at the top: the stdio install has no Werkzeug.

    **``timeout_s`` holds** (ADR-0276). Each read runs on a thread of its own and is waited for at
    most ``timeout_s``; past it the call stops waiting with :class:`DeadlinePassed`. The read
    cannot be stopped, so it runs on to its end with a CPU busy all the while, counted in
    ``abandoned``: while ``abandoned.cap`` such reads are still running, a new read is refused at
    once with :class:`SpaceBusy` instead of being started."""
    from werkzeug.test import Client

    client = Client(wsgi_app, use_cookies=False)
    reads = abandoned or AbandonedReads()

    def fetch(url: str, headers: Mapping[str, str], timeout_s: float) -> Reply:
        path, query = urllib.parse.urlsplit(url)[2:4]
        if reads.full():
            raise SpaceBusy(_STILL_FINISHING)
        finished = threading.Event()
        outcome: list[Any] = []  # the answer, or what the read raised
        started = time.monotonic()

        def read() -> None:
            try:
                # In an empty context, so the read gets an app context of its own: Flask reuses
                # one already pushed on the thread, which would share the outer request's `g`.
                outcome.append(
                    contextvars.Context().run(
                        client.get,
                        path,
                        query_string=query,
                        headers=dict(headers),
                        environ_overrides={IN_PROCESS_READ: True},
                    )
                )
            except Exception as exc:  # noqa: BLE001 — raised again on the waiting thread
                outcome.append(exc)
            finally:
                given_up = reads.finish(finished)
            if given_up:
                _log.warning(
                    "%s: a read past its call's deadline finished after %.0f s",
                    path,
                    time.monotonic() - started,
                )

        threading.Thread(target=read, name="space-mcp-read", daemon=True).start()
        if not finished.wait(timeout_s):
            running = reads.give_up(finished)
            if running is not None:
                _log.warning(
                    "%s: no answer within %.0f s; %d reads past their deadline running",
                    path,
                    timeout_s,
                    running,
                )
                raise DeadlinePassed(_PAST_DEADLINE)
        answer = outcome[0]
        if isinstance(answer, Exception):
            raise answer
        named = {k.lower(): v for k, v in answer.headers.items()}
        return Reply(answer.status_code, named, _decoded(named, answer.get_data()))

    return fetch


class SpaceError(Exception):
    """Why the Space gave no usable answer, in a sentence the model can act on."""


class SpaceWaking(SpaceError):
    pass


class SpaceTooOld(SpaceError):
    pass


class InvalidRequest(SpaceError):
    pass


class NotOnDeployment(SpaceError):
    pass


class SpaceFailed(SpaceError):
    pass


class RateLimited(SpaceError):
    pass


class DeadlinePassed(SpaceError):
    pass


class SpaceBusy(SpaceError):
    pass


#: Said when a call stops waiting for a read at its deadline.
_PAST_DEADLINE = (
    f"HeadStart did not answer within this call's {CALL_DEADLINE_S:g} s, so it stopped "
    "waiting. Narrow the filters, or ask for the concise detail, and try again: a description "
    "keyword over a broad search is the slowest kind."
)

#: Said when too many reads that outlived their call are still running to start another.
_STILL_FINISHING = (
    "HeadStart is still finishing earlier searches that ran past their time limit; try again "
    "in a minute. A description-keyword search that finishes keeps its matches unless there are "
    "very many, so the same search is then usually quick."
)


class RequestBudget:
    """At most ``limit`` Space requests in any ``window_s`` seconds, per process.

    A normal session never reaches it. It is what the spec's rate-limiting requirement means for
    a server whose only cost is someone else's free CPU Space, and what stops a looping agent."""

    def __init__(
        self,
        limit: int = 60,
        window_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window_s = window_s
        self._clock = clock
        self._sent: deque[float] = deque()
        self._lock = threading.Lock()

    def take(self) -> None:
        now = self._clock()
        with self._lock:
            while self._sent and now - self._sent[0] >= self._window_s:
                self._sent.popleft()
            if len(self._sent) >= self._limit:
                raise RateLimited(
                    f"More than {self._limit} HeadStart requests this minute; wait a minute "
                    "and retry."
                )
            self._sent.append(now)


def _json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except ValueError:
        return None


def _refusal_text(body: bytes) -> str:
    """The app's own sentence from a refusal: ``detail`` where it gives one, else ``error``."""
    decoded = _json(body)
    if isinstance(decoded, dict):
        return str(decoded.get("detail") or decoded.get("error") or "")
    return ""


class SpaceClient:
    """The Space, as one tool call reads it. Built per call; see the module docstring."""

    def __init__(
        self,
        *,
        base: str = SPACE_URL,
        fetch: Fetch = urllib_fetch,
        budget: RequestBudget | None = None,
        deadline_s: float = CALL_DEADLINE_S,
        attempt_timeout_s: float = 20.0,
        waits: Sequence[float] = (5.0, 10.0, 20.0, 30.0),
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": "headstart-space-mcp",
        }
        self._base = base.rstrip("/")
        self._fetch = fetch
        self._budget = budget or RequestBudget(clock=clock)
        self._attempt_timeout_s = attempt_timeout_s
        self._waits = tuple(waits)
        self._clock = clock
        self._sleep = sleep
        self._deadline = clock() + deadline_s
        # Set by the first app reply in this call, from any thread: a later timeout is then the
        # app being slow, not the Space booting. A bool write needs no lock.
        self._app_answered = False

    def read(self, route: SpaceRoute, params: Sequence[tuple[str, str]] = ()) -> Any:
        """The JSON ``route`` answers for ``params`` (repeated keys stay repeated), or a
        :class:`SpaceError`."""
        url = f"{self._base}{SpaceRoute(route).value}"
        if params:
            url += "?" + urllib.parse.urlencode(list(params))
        attempt = 0
        while True:
            attempt += 1
            remaining = self._deadline - self._clock()
            if remaining <= 0:
                if self._app_answered:  # the app is up; this call's reads used the time
                    raise DeadlinePassed(_PAST_DEADLINE)
                raise SpaceWaking(
                    "The HeadStart Space is starting. It restarts after each pipeline run and "
                    f"sleeps when idle, and {BOOT_MEASURED}; try again in a few minutes."
                )
            self._budget.take()
            started = self._clock()
            try:
                reply = self._fetch(
                    url, self._headers, min(self._attempt_timeout_s, remaining)
                )
            except OSError as exc:
                if self._app_answered:
                    raise SpaceFailed(
                        "The HeadStart Space stopped answering partway through this call "
                        f"({type(exc).__name__}); try again."
                    ) from exc
                _log.info(
                    "%s attempt %d: no answer (%s)", route, attempt, type(exc).__name__
                )
            else:
                ms = (self._clock() - started) * 1000
                _log.debug(
                    "%s attempt %d: %d in %.0fms", route, attempt, reply.status, ms
                )
                answer = self._answer(route, reply)
                if answer is not _EDGE:
                    return answer
                _log.info("%s attempt %d: edge %d", route, attempt, reply.status)
            wait = self._waits[min(attempt - 1, len(self._waits) - 1)]
            self._sleep(max(0.0, min(wait, self._deadline - self._clock())))

    def _answer(self, route: SpaceRoute, reply: Reply) -> Any:
        marker = reply.headers.get(_MARKER)
        if marker is None:
            if reply.status == 401 and isinstance(_json(reply.body), dict):
                raise SpaceTooOld(_STILL_WALLED)
            return _EDGE
        self._app_answered = True
        served = _AGENT_API_IN_MARKER.search(marker)
        served_api = int(served.group(1)) if served else 0
        if served_api < AGENT_API or reply.status == 404:
            raise SpaceTooOld(
                f"The HeadStart Space is older than this server (it serves agent contract "
                f"{served_api}, this server needs {AGENT_API}); deploy main to the Space."
            )
        if reply.status == 200:
            decoded = _json(reply.body)
            if decoded is None:
                raise SpaceFailed(
                    f"The HeadStart Space answered {route} with non-JSON."
                )
            return decoded
        text = _refusal_text(reply.body)
        if reply.status == 400:
            raise InvalidRequest(text or "The HeadStart Space refused this request.")
        if reply.status in (401, 403):
            raise SpaceTooOld(_STILL_WALLED)
        if reply.status == 429:
            wait = reply.headers.get("retry-after", "").strip()
            raise RateLimited(
                "The HeadStart Space is limiting how often one client may ask"
                + (
                    f"; retry in {wait} s"
                    if wait.isdigit()
                    else "; wait a minute and retry"
                )
                + "."
            )
        if reply.status == 503:
            raise NotOnDeployment(
                f"Not on this deployment yet: {text or 'the Space has no data for this'}."
            )
        raise SpaceFailed(
            f"The HeadStart Space failed on this request (HTTP {reply.status}); it is "
            "logged there."
        )


#: Said when the app answers a read route with its sign-in wall: the route is public on `main`.
_STILL_WALLED = (
    "The HeadStart Space still asks for sign-in on this route: it predates the public read "
    "routes this server needs; deploy main to the Space."
)

#: What :meth:`SpaceClient._answer` returns for a reply that came from the edge, not the app.
_EDGE = object()
