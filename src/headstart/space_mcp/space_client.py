"""How the Space MCP server reaches the Space: read routes only, over HTTPS or in process.

:class:`SpaceClient` is built once per tool call, which is what gives a call its **deadline**
(90 s in all) and lets it know whether **the app has already answered in this call**. Its one
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
route is not re-sent to a free CPU Space three more times.

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
#: suggestion's `match` and `board_keys`; since 2, `counts=total` on `/facets` (ADR-0274). The app
#: states the one it serves on every reply.
AGENT_API = 2

#: The measured boot, said when a call gives up waiting for one.
BOOT_MEASURED = "a boot measured 4 min 13 s on 2026-09-28"

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


@dataclass(frozen=True)
class Reply:
    """One HTTP answer: its status, its headers (names lower-cased) and its body."""

    status: int
    headers: Mapping[str, str]
    body: bytes


#: The port: ``(url, headers, timeout_s) -> Reply``. It answers every HTTP status as a
#: :class:`Reply` and raises ``OSError`` (``TimeoutError`` included) only when no answer came back.
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


def wsgi_fetch(wsgi_app: Callable) -> Fetch:
    """The :data:`Fetch` for this server when the Space itself serves it (ADR-0267): each read is
    a request to ``wsgi_app`` in process, with no cookie, so no Account reaches an answer.
    ``timeout_s`` has no hold on an in-process call; the outer ``/mcp`` request's own limits bound
    it. Werkzeug is imported here, not at the top: the stdio install has no Werkzeug."""
    from werkzeug.test import Client

    client = Client(wsgi_app, use_cookies=False)

    def fetch(url: str, headers: Mapping[str, str], timeout_s: float) -> Reply:
        parts = urllib.parse.urlsplit(url)
        # In an empty context, so the read gets an app context of its own: Flask reuses one
        # already pushed on the thread, which would share the outer `/mcp` request's `g`.
        answer = contextvars.Context().run(
            client.get,
            parts.path,
            query_string=parts.query,
            headers=dict(headers),
            environ_overrides={IN_PROCESS_READ: True},
        )
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
        deadline_s: float = 90.0,
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
