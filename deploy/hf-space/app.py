"""HeadStart semantic search — HF Space app (ADR-0020).

Pulls the LanceDB ``jobs`` table from the private HF dataset at startup (HF_TOKEN Space
secret), loads the nomic encoder (baked into the image), and serves the shared UI — the
templates and static files under ``headstart.ui`` — over the shared search path,
``job_search.JobSearch`` (ADR-0042). The local dev server (``scripts/ui/serve.py``) is a thin
adapter over the same two modules, so nothing here is duplicated there any more. Both import
``headstart`` the same way: the Space installs it as a real package rather than laying its
modules down flat (ADR-0153), so there is exactly one import path to keep in sync.
"""

from __future__ import annotations

import gzip
import hmac
import ipaddress
import json
import os
import secrets
import threading
import time
import traceback
import urllib.parse
from collections import OrderedDict
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import lancedb
import waitress
from flask import (
    Flask,
    Response,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
)
from flask.sessions import SecureCookieSessionInterface
from huggingface_hub import snapshot_download

import headstart  # only for headstart.__file__, to locate ui/ beside this package (ADR-0153)
from headstart import embedding_conventions, llm_router

# alerts/__init__.py is empty on purpose, so importing it never pulls in the Digest or Resend
# modules, whose dependencies (xlsxwriter, Resend) this image does not install.
from headstart.alerts import access, identity
from headstart.alerts.store import (
    MAX_COMPANIES,
    MAX_PARSES,
    MAX_RESUME_BYTES,
    MAX_RESUMES,
    MAX_SAVED,
    MAX_SETS,
    Profile,
    SavedJob,
    SavedSet,
    Store,
    StoreConflict,
    StoreUnavailable,
    Subscription,
    is_resume_id,
    subscription_id,
)
from headstart.mcp_protocol import streamable_http
from headstart.search_filters import country_filter, fx, india_gazetteer
from headstart.search_filters.compiler import (
    KEYWORD_DEFAULT_SCOPE,
    keyword_scope_options,
)
from headstart.serving import (
    concurrency_limit,
    facets,
    job_search,
    profile_extract,
    rate_limit,
)
from headstart.space_mcp import server as space_mcp_server
from headstart.space_mcp import space_client
from headstart.space_mcp.tools import search_jobs as space_mcp_search_jobs
from headstart.trends import hot_ranking, line_reading, trend_history

DATASET = os.environ.get("HF_DATASET", "imPoseidon/headstart-index")
_STATE = Path("/app/state")


def _pull_index(attempts: int = 5) -> None:
    """Pull the served index, retrying rather than dying on one bad response.

    ``snapshot_download`` fetches ~150 files and gives up if **any** one of them fails, at module
    import, with no retry. On 2026-09-09 that took the Space down for about an hour: a single
    ``data/lancedb/.../_deletions/*.arrow`` answered without an ``X-Repo-Commit`` header and the
    unguarded call turned it into a container that could not start — and could not recover,
    because every restart re-ran the same single attempt
    (``docs/pipeline/2026-09-09_space-outage-unpinned-stack.md``).

    That particular failure was deterministic, so this would not have fixed it; it is not
    presented as that fix. What it addresses is the shape of the fragility — one unreachable
    file out of ~150 costing the whole product, with no self-recovery — which is worth removing
    on its own.

    Retrying is cheap because the download resumes: measured against this dataset, a repeat pull
    of an already-complete directory costs 0.34s against 13.80s cold, and one missing five files
    costs 1.58s. An attempt that dies at file 19 of 150 keeps those 19.

    The backoff is ``2**attempt``, so five attempts add 2+4+8+16 = **30s** at most. That is the
    cost side: against a *deterministic* failure the boot still fails, just 30s later, delaying
    HF's ``RUNTIME_ERROR`` — which is today's only alarm. 30s against a boot that already spends
    longer loading the encoder is a fair trade; much more would not be.

    Catching ``Exception`` rather than a named list is deliberate. The observed failure surfaced
    as ``LocalEntryNotFoundError``, which subclasses ``EntryNotFoundError`` but means "could not
    reach or resolve", not "file absent" — enumerating types here is exactly how that trap
    inverts a guard. A bounded retry is safe whatever the cause: a genuinely unreachable dataset
    exhausts the attempts and still raises, so a real outage is still loud.
    """
    for attempt in range(1, attempts + 1):
        try:
            snapshot_download(
                DATASET,
                repo_type="dataset",
                local_dir=_STATE,
                # an absent pattern downloads nothing rather than failing, so a state without
                # the Trends history hides the tab
                allow_patterns=[
                    "data/lancedb/*",
                    # the Trends history (ADR-0230): one file a tick, and the archive of the
                    # ticks before per-Board counting
                    "data/state/role_trend_board_deltas/*",
                    "data/state/role_trend_index_deltas_before_board_deltas.parquet",
                    # the Company directory (ADR-0185) the Trends picker searches and the Hot
                    # tab ranks (ADR-0230) — ~2 MB, and absent until a run writes one, which
                    # hides both rather than failing the pull
                    "data/state/company_directory.json",
                    # each served Job's role family (ADR-0057), so a Trends category can hand
                    # over to Search as exact ids — ~4 MB, absent until a run writes one
                    "data/state/role_assignments.parquet",
                    # duplicate removals per run and Board (#649), so a company's line can leave
                    # them out exactly — small, and absent until a run writes one
                    "data/state/dedup_evictions.csv",
                    # the ids the latest scrape of their Board missed (ADR-0083), which `/job`
                    # reports (ADR-0277) — ~100 KB, published in the table's own commit
                    "data/state/unconfirmed_ids.txt",
                ],
                token=os.environ.get("HF_TOKEN"),
            )
            return
        except Exception as exc:  # broad on purpose — see the docstring
            if attempt == attempts:
                raise
            wait = 2**attempt
            print(
                f"index pull attempt {attempt}/{attempts} failed "
                f"({type(exc).__name__}: {exc}); retrying in {wait}s",
                flush=True,
            )
            time.sleep(wait)


print(f"pulling index from {DATASET} ...", flush=True)
_pull_index()

print("loading encoder ...", flush=True)
from sentence_transformers import (
    SentenceTransformer,
)

_model = SentenceTransformer(
    embedding_conventions.MODEL, trust_remote_code=True, device="cpu"
)
_table = lancedb.connect(_STATE / "data" / "lancedb").open_table(
    embedding_conventions.PROD_TABLE
)
# The whole query path — parse, whitelist, rank, project — lives behind this one object
# (job_search.JobSearch); its startup scan supplies the ATS dropdown and the first_seen flag.
_searcher = job_search.JobSearch(_model, _table)
# The first page always browses with no filters and asks for the matching facet strip. Build both
# from this process's freshly-opened table before accepting traffic; every pipeline publication
# restarts the Space, so a new table necessarily gets new caches.
_searcher.warm()

# The served Jobs the latest scrape of their Board missed (ADR-0083): still served, and evicted
# only if the next scrape of that Board misses them too. `index_publish` commits this file with
# the table, so it describes exactly the table above. None when the pull found no file, so `/job`
# reports "not known" rather than "not missed" (ADR-0277).
_UNCONFIRMED_FILE = _STATE / "data" / "state" / "unconfirmed_ids.txt"
_UNCONFIRMED: frozenset[str] | None = (
    frozenset(
        line.strip()
        for line in _UNCONFIRMED_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    if _UNCONFIRMED_FILE.is_file()
    else None
)

# Role trends (ADR-0040): everything /trends and the company picker answer from, read by one
# module, `headstart.trends.trend_history` (ADR-0230). Same dark-until-ready shape as the two above:
# the ledgers only exist after a pipeline run has written them, so an absent file hides the panel
# rather than erroring. Read once at startup — the Space restarts after every run, so it is never
# more than one run stale.
_CONFIG = Path(__file__).parent / "config"  # copied in beside this app (ADR-0153)
_HISTORY = trend_history.TrendHistory.load(_STATE / "data" / "state", _CONFIG)
# Search's category hand-off reads the same taxonomy: each watched role's title patterns, and each
# retired family's successor, so a Trends category hands Search the Jobs its line counts.
_WATCH = trend_history.watched_roles(_CONFIG / "role_watchlist.json")
_FAMILY_SUCCESSOR = trend_history.family_successors(_CONFIG / "role_families.json")
# Every family the taxonomy names, retired ones included, so a `strict=1` hand-off can refuse a
# name nothing configures while a configured family with no Jobs assigned yet answers zero rows.
_KNOWN_FAMILIES = frozenset(trend_history.family_labels(_CONFIG / "role_families.json"))
# What every answer read from the history above is versioned by (ADR-0251): this process's boot.
# The history is read once and never changes after, and every pipeline publication and every
# deploy restarts the Space, so one boot names one fixed set of answers. The page sends it back
# as `v=`, and an answer asked for under it may be kept by the browser for good.
_ANSWERS_VERSION = f"{time.time_ns():x}"


def _first_seen_since(stamp: str | None) -> list[tuple[str, str, str | None]] | None:
    """The served postings first seen after ``stamp``, as ``(id, first_seen, posted_at)``, which
    the Hot ranking dates its rows' jobs Opened by (ADR-0351); None with no stamp or no
    `first_seen` column, or where the read fails, so the rows say their postings went unread
    rather than the tab going dark. About 92,000 rows since 2026-09-25, three short columns."""
    if not stamp or not _searcher.capabilities.has_first_seen:
        return None
    where = f"first_seen > '{stamp}'"
    try:
        rows = (
            _table.search()
            .where(where)
            .select(["id", "first_seen", "posted_at"])
            .limit(max(1, _table.count_rows(where)))
            .to_arrow()
        )
    except Exception as exc:  # noqa: BLE001 - an unread date darkens one flag only
        print(
            f"hot ranking: first-seen postings unread ({type(exc).__name__}: {exc})",
            flush=True,
        )
        return None
    return list(
        zip(
            rows["id"].to_pylist(),
            rows["first_seen"].to_pylist(),
            rows["posted_at"].to_pylist(),
            strict=True,
        )
    )


def _rank_hot(history: trend_history.TrendHistory) -> dict:
    """The Hot tab's ranking (``headstart.trends.hot_ranking``, ADR-0230), or ``{}`` to keep it
    dark.

    Ranked once at boot from the history just loaded, so it can never be stale against the ticks
    the Trends tab serves, and served as-is: the answer only changes when a run does, and the
    Space restarts after every run. Dark rather than broken when there is nothing to rank yet.
    Each company's Operator is decided as the history loads the directory (ADR-0238), so a file
    written before an Operator existed ranks as the current list says. Never fatal: Search is
    the product, and a ranking that fails costs this one tab.
    """
    companies = history.companies
    if not companies:
        return {}
    started = time.monotonic()
    try:
        ranked = hot_ranking.rank(
            history,
            companies,
            _first_seen_since(history.trailing_week()["turnover_from"]),
        )
    except Exception as exc:  # noqa: BLE001 - a ranking failure darkens Hot only
        print(f"hot ranking failed ({type(exc).__name__}: {exc})", flush=True)
        return {}
    print(
        f"hot ranking: {ranked.get('counts', {}).get('ranked', 0)} companies ranked "
        f"in {time.monotonic() - started:.1f}s",
        flush=True,
    )
    return ranked


def _derive_from_history(
    history: trend_history.TrendHistory,
) -> tuple[dict[str, tuple[str, ...]], dict]:
    """What boot derives from the Trends history, as ``(company_boards, hot)``: each Board's
    company as every Board of its Company directory entry, keyed case-blind as the follow and
    hide lists compare Boards (Follow and Hide act on a whole company, ADR-0230), and the Hot
    ranking (``_rank_hot``). The one derivation, so a history installed after import (a test's)
    rebuilds the same globals boot built."""
    company_boards = {
        board.lower(): tuple(entry["boards"])
        for entry in history.companies.values()
        for board in entry["boards"]
    }
    return company_boards, _rank_hot(history)


_COMPANY_BOARDS, _HOT = _derive_from_history(_HISTORY)


def _operator_boards(
    history: trend_history.TrendHistory,
) -> dict[str, tuple[str, ...]]:
    """Each Operator's Boards but the employers', from the Company directory the Hot ranking
    reads, so ``operators=`` on /search, /facets and /requirements leaves out the Boards the
    Hiring now tab hides (ADR-0335). Empty without a directory: every Board is then an
    employer's, as ``board_operator.classify`` defaults."""
    boards: dict[str, list[str]] = {}
    for entry in history.companies.values():
        if entry["operator"] != "employer":
            boards.setdefault(entry["operator"], []).extend(entry["boards"])
    return {operator: tuple(named) for operator, named in boards.items()}


_searcher.load_operator_boards(_operator_boards(_HISTORY))


def _with_predecessors(
    family_ids: dict[str, list[str]] | None, successors: dict[str, str]
) -> dict[str, list[str]] | None:
    """``family_ids`` with each family also holding its predecessors' ids (ADR-0220), sorted
    as ``load_family_ids`` sorts them — the families a Trends line for it sums. Search took the
    name as written: "AI, ML & Data Science 410" at Google opened as 0 jobs, and Engineering
    Management's 152 as 131, without the 21 still assigned to Tech Leadership."""
    if family_ids is None:
        return None
    out = dict(family_ids)
    for family in set(successors.values()):
        names = [family, *trend_history.predecessors(family, successors)]
        pools = [family_ids[name] for name in names if name in family_ids]
        if pools and (len(pools) > 1 or family not in family_ids):
            out[family] = sorted((i for pool in pools for i in pool), key=str.lower)
    return out


_FAMILY_IDS = _with_predecessors(
    job_search.load_family_ids(_STATE / "data" / "state" / "role_assignments.parquet"),
    _FAMILY_SUCCESSOR,
)
# The same assignments with the families the taxonomy lists now, retired ones left out: what
# `/requirements` names a sampled Job's category by, and accepts as `family=` (ADR-0324).
_ROLE_ASSIGNMENTS = (
    None
    if _FAMILY_IDS is None
    else job_search.RoleAssignments(
        _FAMILY_IDS, _KNOWN_FAMILIES - frozenset(_FAMILY_SUCCESSOR)
    )
)
# A category across the whole index (`family=` without `board=`) reads each family's rows from
# a table built on its first request (ADR-0322).
if _FAMILY_IDS is not None:
    _searcher.families = job_search.FamilyTables(_table, _FAMILY_IDS)
# Email alerts (ADR-0035) — invite-only, so all three must be set before the panel appears:
# the Google client id the sign-in button needs, and a token scoped to the Subscriptions
# dataset alone (never the index token, which is read-only by design).
_GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID") or ""
_SUBSCRIBERS_REPO = os.environ.get("SUBSCRIBERS_REPO") or ""
_SUBSCRIBERS_TOKEN = os.environ.get("SUBSCRIBERS_TOKEN") or ""
_ALERTS_ON = bool(_GOOGLE_CLIENT_ID and _SUBSCRIBERS_REPO and _SUBSCRIBERS_TOKEN)
# Sign-in wall (ADR-0042): the whole UI sits behind Google sign-in once BOTH secrets exist.
# Same dark-until-ready shape as everything above — a Space without them keeps the old open,
# anonymous behaviour, so this can deploy ahead of its configuration.
_SECRET_KEY = os.environ.get("SECRET_KEY") or ""
_AUTH_ON = bool(_SECRET_KEY and _GOOGLE_CLIENT_ID)
# Saved sets (ADR-0043), Saved jobs (ADR-0044) and Profiles (ADR-0041) need both an identity
# (the wall) and somewhere to keep per-Account records (the Subscriptions dataset) — either
# missing keeps the Matches, Saved and Profile tabs "soon" items. One flag: the three share
# exactly these prerequisites. (A Profile's parse button additionally needs the llm-router at
# request time; that path degrades to a 503 on its own.)
_SETS_ON = _AUTH_ON and bool(_SUBSCRIBERS_REPO and _SUBSCRIBERS_TOKEN)
print(
    f"ready: {_table.count_rows()} jobs across {len(_searcher.capabilities.atses)} ATSes"
    + (
        ""
        if _searcher.capabilities.has_first_seen
        else " (no first_seen column yet — 'new since' hidden)"
    )
    + ("" if _ALERTS_ON else " (alerts secrets unset — email alerts hidden)")
    + ("" if _AUTH_ON else " (SECRET_KEY/GOOGLE_CLIENT_ID unset — sign-in wall off)"),
    flush=True,
)


def _store() -> Store:
    """A Subscriptions store per request — it holds no connection, only ids."""
    return Store(_SUBSCRIBERS_REPO, _SUBSCRIBERS_TOKEN)


# The UI's single source is headstart/ui (templates + static), resolved off the installed
# package (ADR-0153) exactly like scripts/ui/serve.py does — in the Space image, in a repo
# checkout, or under test, `headstart.__file__` always points at the same src/headstart tree.
_UI = Path(headstart.__file__).parent / "ui"
app = Flask(
    __name__,
    template_folder=str(_UI / "templates"),
    static_folder=str(_UI / "static"),
)
# The session is a signed cookie (ADR-0042): Google is verified once at /auth/google, then
# the cookie is the identity for a week — re-sending the ~1h Google token would bounce users
# mid-use. Lax + Secure: it never rides a cross-site POST, and only travels over https.
# Nothing server-side can revoke one cookie: /signout clears only the browser's copy (#593).
# Flask re-signs the cookie on every response, so its own expiry slides with use and a replayed
# copy never lapses; `_require_sign_in` also ends a session _SESSION_LIFETIME after sign-in,
# which bounds a copied cookie however often it is used. Rotating SECRET_KEY signs everyone out
# at once (their cookies stop verifying); nothing else breaks.
_SESSION_LIFETIME = timedelta(days=7)
app.config.update(
    SECRET_KEY=_SECRET_KEY or None,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=_SESSION_LIFETIME,
)


class _AnswersLeaveTheSessionAlone(SecureCookieSessionInterface):
    """The signed-cookie session, except on an answer the browser may keep (ADR-0251).

    Flask re-signs a permanent session's cookie on every response and then marks the response
    `Vary: Cookie`, as it does whenever the wall has read the session. A kept answer is the same
    for every Account, but keyed on a cookie that changes each second it is never found again:
    measured 2026-09-28 with the wall on, every revisit of a kept view went back to the network.
    So such an answer neither re-signs the cookie nor varies by it; every other response still
    re-signs it, so a session still slides forward with use."""

    def save_session(self, app, session, response):
        if g.get("answer_for_everyone"):
            return
        super().save_session(app, session, response)


app.session_interface = _AnswersLeaveTheSessionAlone()

# Paths that must answer signed out: the door itself, and the unsubscribe link every Digest
# already delivered carries — a session wall must never break a mailed link. `/me` answers
# from the caller's own cookie, so it can only tell you what you sent. `/privacy` is the URL
# Google's OAuth consent screen points strangers at before they have an Account. The logo mark
# is the one static file the door loads: its brand mark and its favicon (ADR-0249).
#
# The read routes answer anyone as well, so that anyone can use HeadStart's MCP server
# (ADR-0258): Search and its Facet counts, Trends, Hot, the two company lookups, a Job read
# by id (ADR-0277), a company's locations (ADR-0275) and levels (ADR-0323), and what a role's
# postings ask for (ADR-0324). None writes, and none serves one Account's records to another: a
# signed-in caller's own session still applies its follow/hide clause to /search and /facets
# (`_company_where`), and an anonymous one gets none. Every Account route stays behind the wall,
# and the page at `/` still shows the door until its visitor signs in. Every caller is
# rate-limited on them (`_limit_each_caller`).
_READ_ROUTES = frozenset(
    {
        "/search",
        "/facets",
        "/trends",
        "/hot",
        "/companies/suggest",
        "/companies/lookup",
        "/job",
        "/companies/locations",
        "/companies/levels",
        "/requirements",
    }
)
_PUBLIC_PATHS = {
    "/",
    "/auth/google",
    "/me",
    "/unsubscribe",
    "/privacy",
    "/static/logo_mark.svg",
    "/mcp",  # HeadStart's MCP server, POST only, over the read routes (ADR-0267)
    *_READ_ROUTES,
}

# The public repository, named once *for the Space*. ADR-0112's door, the app's privacy-policy
# links and the `/privacy` redirect all link into it, and "check it yourself" is the
# claim they rest on, so a rename must not leave half of one page's links dead.
# `scripts/ui/serve.py` necessarily keeps its own copy — it is the local renderer and shares no
# config with this module — and PRIVACY.md names the URL in prose.
_REPO = "https://github.com/sarthakjain004/headstart"

# The door's freshness window (ADR-0112). Seven days rather than 24 hours: a single day's
# intake swings with which Boards the run happened to slice, and a tile that halves overnight
# for no reason the visitor can see reads as broken rather than as honest.
_DOOR_NEW_HOURS = 168

#: Set on every answer (#595).
_HARDENING_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}

#: What the page may load and run (#595, ADR-0298), bar `script-src`'s nonce. Scripts: this
#: Space's own files, Google's sign-in library, and an inline script only with this response's
#: nonce, so no inline handler or injected script runs. Styles keep 'unsafe-inline': the page
#: and the résumé builder write style attributes and `<style>` elements, the print frame's among
#: them, and a style cannot run code. Google's origins are the ones its sign-in guide names.
#: Framing is limited, not forbidden: huggingface.co's Space page iframes this app, and its
#: sign-in door is how a visitor there reaches the direct URL.
_CSP_SCRIPTS = "'self' https://accounts.google.com/gsi/client"
_CSP_REST = (
    "style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style",
    "connect-src 'self' https://accounts.google.com/gsi/",
    "frame-src https://accounts.google.com/gsi/",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'self' https://huggingface.co",
)


def _csp_nonce() -> str:
    """This response's script nonce, made the first time a template asks for it."""
    if "csp_nonce" not in g:
        g.csp_nonce = secrets.token_urlsafe(16)
    return g.csp_nonce


@app.context_processor
def _templates_get_the_csp_nonce():
    return {"csp_nonce": _csp_nonce()}


def _content_security_policy(nonce: str | None) -> str:
    scripts = _CSP_SCRIPTS + (f" 'nonce-{nonce}'" if nonce else "")
    return "; ".join(("default-src 'self'", f"script-src {scripts}", *_CSP_REST))


_REQUEST_STARTED_KEY = "headstart.request_started"


# These two are registered before every other hook, so a request's time spans all of them: the
# start is stamped before the wall and the limits can refuse it, and Flask runs the after-hooks
# in reverse, so the line is printed after every other one has run.
@app.before_request
def _note_the_start():
    request.environ[_REQUEST_STARTED_KEY] = time.monotonic()


@app.after_request
def _log_the_request(response):
    """One line per request in the Space run log, as the development server printed and waitress
    does not: that log is how an edge outage is told from the app failing. The path only, since a
    query string carries a search's words, and spelled as in a URL: waitress hands it over
    percent-decoded, and a decoded `%0A` would print a line of its own. A read `/mcp` makes in
    process is not a request anyone sent."""
    if not request.environ.get(space_client.IN_PROCESS_READ):
        path = urllib.parse.quote(request.path, safe="/:@!$&'()*+,;=")
        took_s = time.monotonic() - request.environ[_REQUEST_STARTED_KEY]
        print(
            f'"{request.method} {path}" {response.status_code} {took_s:.3f}s',
            flush=True,
        )
    return response


@app.after_request
def _hardening_headers(response):
    """Headers only: a gzipped body, a 304 and the cache headers pass through untouched."""
    for name, value in _HARDENING_HEADERS.items():
        response.headers.setdefault(name, value)
    response.headers.setdefault(
        "Content-Security-Policy", _content_security_policy(g.get("csp_nonce"))
    )
    return response


def _request_json_object() -> dict:
    """The request's JSON body when it is an object, else ``{}``: a JSON array or scalar
    reached ``.get()`` and answered 500 (#595); as ``{}`` it meets each route's own 400."""
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


@app.before_request
def _end_a_week_old_session():
    """Sign out a session ``_SESSION_LIFETIME`` after its sign-in, on every path (#593). It is
    registered before the wall, so the wall, ADR-0262's limit and ``/me`` all see such a caller
    signed out."""
    if _AUTH_ON and session.get("email") and not _signed_in_recently():
        session.clear()


def _signed_in_recently() -> bool:
    """Whether the session's sign-in is under ``_SESSION_LIFETIME`` old. A cookie from before
    ``signed_in_at`` existed has none, and signs in again."""
    signed_in_at = session.get("signed_in_at")
    return isinstance(signed_in_at, int | float) and (
        time.time() - signed_in_at < _SESSION_LIFETIME.total_seconds()
    )


@app.before_request
def _require_sign_in():
    if not _AUTH_ON or request.path in _PUBLIC_PATHS:
        return None
    if not session.get("email"):
        return jsonify({"error": "sign in first"}), 401
    return None


# How often one caller may read `_READ_ROUTES` (ADR-0262): 60 requests in any 60 s, the read
# routes together. The page's own busiest minute fits: a Search is two requests (/search and
# /facets, again on each page turn), and the Trends tab asks twice for a quick burst of boxes in
# its Source picker; what it asks ahead for is kept, and a kept answer is not counted (ADR-0269).
# The MCP server holds itself to the same 60 a minute, so one server process meets its own
# limit before this one.
_READ_LIMIT_REQUESTS = 60
_LIMIT_WINDOW_S = 60
_READ_LIMIT = rate_limit.RateLimit(_READ_LIMIT_REQUESTS, _LIMIT_WINDOW_S)
# And how often it may write, or list its saved jobs (#592). Every POST, PUT and DELETE is its
# own HF commit on the Subscriptions dataset, and `GET /saved` lists that whole repo, all on one
# token shared by every Account: a looping client would spend the token's commit and API budget
# for everyone. `GET /unsubscribe` commits too, so it counts as a write. A résumé being edited
# pushes at most once every three minutes, plus once on each tab switch or document switch
# (`resume_sync.js`), so the busiest writer is a reader starring jobs, well under 30 a minute.
# These bound one caller, not the total: N Accounts still spend N times the budget.
_WRITE_LIMIT_REQUESTS = 30
_WRITE_LIMIT = rate_limit.RateLimit(_WRITE_LIMIT_REQUESTS, _LIMIT_WINDOW_S)
_SAVED_LIMIT_REQUESTS = 20
_SAVED_LIMIT = rate_limit.RateLimit(_SAVED_LIMIT_REQUESTS, _LIMIT_WINDOW_S)


def _client_address() -> str:
    """The address this request came from. Hugging Face's edge appends the address it accepted
    the connection from to `X-Forwarded-For` and keeps whatever the caller sent to its left
    (measured 2026-09-28, ADR-0262), so only the last entry is the edge's word; an earlier one
    is the caller's to forge. Without the header, as in a local run, the peer is the caller."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.rsplit(",", 1)[-1].strip() or request.remote_addr or ""


def _request_limit() -> tuple[rate_limit.RateLimit, int] | None:
    """The limit this request counts against, and its size; None when it counts against none.
    `/mcp` is a POST that writes nothing, and is limited by its own route (ADR-0267)."""
    if request.path == "/mcp":
        return None
    if request.method in ("POST", "PUT", "DELETE") or request.path == "/unsubscribe":
        return _WRITE_LIMIT, _WRITE_LIMIT_REQUESTS
    if request.path == "/saved":
        return _SAVED_LIMIT, _SAVED_LIMIT_REQUESTS
    if request.path in _READ_ROUTES:
        return _READ_LIMIT, _READ_LIMIT_REQUESTS
    return None


@app.before_request
def _limit_each_caller():
    """A 429 with `Retry-After` for a caller past its limit. A caller is its Account when it has
    a session (#592: sign-up is open, so an address alone would let one client multiply itself
    by signing in), else its address; the two are counted apart. A read the app's own `/mcp`
    tools make in process is not counted again: `/mcp` is limited itself (ADR-0267). Nor is a
    Trends answer already worked out this boot (ADR-0269): the limit is for what a question costs
    the Space, and a kept one costs a lookup, as a static file does. The page asks ahead for
    each drawn view's neighbours, and counted, those alone passed sixty a minute."""
    found = _request_limit()
    if found is None or request.environ.get(space_client.IN_PROCESS_READ):
        return None
    if request.path == "/trends" and _trends_kept(_trends_question(request.args)):
        return None
    limit, requests = found
    if _AUTH_ON and session.get("email"):
        caller, who = "account:" + session["email"], "Account"
    else:
        caller, who = "address:" + _client_address(), "address"
    wait_s = limit.admit(caller)
    if not wait_s:
        return None
    return (
        jsonify(
            error="too many requests",
            detail=f"at most {requests} requests in {_LIMIT_WINDOW_S} s "
            f"from one {who}; retry in {wait_s} s",
        ),
        429,
        {"Retry-After": str(wait_s)},
    )


def _gzip(data: bytes) -> bytes:
    """``data`` gzipped at level 6 (ADR-0251): ~11 ms for the 376 KB `new` view here, where
    level 9 took ~14.5 ms to save 0.6 KB more (measured 2026-09-28)."""
    return gzip.compress(data, compresslevel=6)


def _json_body(obj) -> bytes:
    """``obj`` as exactly the bytes ``jsonify`` sends for it."""
    return app.json.response(obj).get_data()


# Each script and stylesheet gzipped, by path and ETag: compressed once a boot (`_gzip_static`).
_GZIPPED_STATIC: dict[tuple[str, str | None], bytes] = {}


@app.after_request
def _gzip_static(response):
    """The page's scripts and stylesheets gzipped where the browser takes it (ADR-0251): ~0.9 MB
    of them on a first visit, which the Space's proxy passes on uncompressed (measured
    2026-09-28), and the Trends chart waits on app.js. The ETag is weakened, since the gzipped
    copy is not the file byte for byte; a revalidation still matches it and still answers 304.
    Every answer for one names the variants, a 304 as well."""
    if request.endpoint != "static" or response.mimetype not in (
        "text/javascript",
        "application/javascript",
        "text/css",
    ):
        return response
    response.vary.add("Accept-Encoding")
    if response.status_code != 200 or request.accept_encodings.quality("gzip") <= 0:
        return response
    etag, _ = response.get_etag()
    response.direct_passthrough = False
    data = (
        response.get_data()
    )  # read even when kept: it closes the file send_file opened
    key = (request.path, etag)
    if key not in _GZIPPED_STATIC:
        _GZIPPED_STATIC[key] = _gzip(data)
    response.set_data(_GZIPPED_STATIC[key])
    response.headers["Content-Encoding"] = "gzip"
    if etag:
        response.set_etag(etag, weak=True)
    return response


@app.url_defaults
def _static_under_this_boot(endpoint, values):
    """Every static URL the page names carries this boot's version, as the answers do."""
    if endpoint == "static":
        values.setdefault("v", _ANSWERS_VERSION)


@app.after_request
def _keep_static_for_the_boot(response):
    """A static file asked for under this boot's version, kept by the browser until the next
    boot (ADR-0261): only a deploy changes one, and a deploy restarts the Space, which gives the
    page a new version. A revisit then reads app.js and the rest from the browser, where each
    file was revalidated before, a round trip every visit. Like a kept answer, it neither
    re-signs the session cookie nor varies by it (`_AnswersLeaveTheSessionAlone`)."""
    if (
        request.endpoint == "static"
        and response.status_code == 200
        and request.args.get("v") == _ANSWERS_VERSION
    ):
        response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
        g.answer_for_everyone = True
    return response


# The agent contract this app serves (ADR-0253): what an agent may rely on in the public read
# routes (`_PUBLIC_PATHS`). Raised whenever that contract changes, so an agent can tell an app
# too old for it from one that serves it, rather than have a newer argument silently ignored.
# 1: `strict=1` on /search and /facets, `match` and `board_keys` on each /companies/suggest item,
# /companies/lookup, and `newest_tick` on /facets.
# 2: `counts=total` on /facets, the total without any option's count (ADR-0274).
# 3: `country` (an ISO 3166-1 alpha-2 code, ADR-0273) on /search and /facets, refused under
# `strict=1` when unknown.
# 4: /job (a Job read by id, with its description and whether the latest scrape missed it), and
# `like=` on /search and /facets (ADR-0277).
# 5: /companies/locations (ADR-0275).
# 6: each location's country on /companies/locations, and /companies/levels (ADR-0323).
# 7: /requirements, what a sample of a role's or a category's postings ask for (ADR-0324).
# 8: the `opened_less_closed` lens on /hot, its rows' `opened_less_closed` and the count
# `closures_partly_uncounted` (ADR-0321).
# 9: `family=` without `board=` (a category across the whole index), `max_age_days`,
# `required_years_at_least` and `exclude_company` on /search and /facets (ADR-0322).
# 10: /companies/locations lists each country's cities, a place's first city merged across its
# spellings ("Dublin" and "Dublin, Ireland"), not its places as written (ADR-0331).
# 11: /requirements counts one Job per requisition under `jobs` keys, names a Board that names no
# company by the directory, and says its `read`, `distinct`, `sample_size` and `category_window`
# (ADR-0332).
# 12: /trends gives a category's opened and closed as the index's line for it, in the category's
# own view too; only its level lines leave out an extraction change's runs (ADR-0336).
# 13: `strict=1` on /search and /facets refuses a parameter neither reads, naming it (ADR-0334).
# 14: a sorted /search under `q` or `like` orders only rows scoring at least SORT_FLOOR, and
# /requirements groups a short and a long name of one employer as one requisition (ADR-0338).
# 15: `operators` on /search, /facets and /requirements, `operators_left_out` on /facets and
# /requirements, and each /hot row's `operator_unverified` (ADR-0335).
# 16: `work_authorization` (offers_sponsorship, refuses_sponsorship, offers_relocation) on /search
# and /facets, each /job's `work_authorization` stances and mentions, and /requirements'
# `work_authorization` counts, all read from descriptions by rules (ADR-0333).
# 17: `per_company` on /search (a relevance page lists a company's first N before the others',
# marking the rows) and /requirements (counts at most N of one company's), ADR-0352.
# 18: `include_non_tech` on /search, /facets, /requirements, /companies/locations and
# /companies/levels: the jobs the role-family head confidently calls non-tech are left out unless
# it is sent, and /facets and /requirements say how many as `non_tech_left_out` (ADR-0349).
# 19: each /hot row's `opened_fresh` and `opened_found_late`, its served postings first seen since
# turnover began posted within 14 days of first sight and longer before (ADR-0351).
# 20: `may_offer_sponsorship`, and offers read against each Job's place and title (ADR-0353).
# 21: `places=1` on /facets, where every matching job is by country and city, and
# /companies/locations reads every place's country, so it no longer sends `places_unread`
# (ADR-0355).
# 22: /trends' tracked-roles first row adds up only the roles counted from its first run, and
# /hot's `operator_unverified` reads a Board's own label, not its vendor's host (ADR-0366).
# 23: /search under `may_offer_sponsorship` tags each row's `sponsorship` stance and why a possible
# offer is not firm; /job carries `may_offer_because`, `stated_end_date` and `closest` (ADR-0367).
_AGENT_API_VERSION = 23


@app.after_request
def _mark_own_reply(response):
    """Mark every reply this app produces, so a caller can tell it from one HF's edge gives
    in front of a booting or sleeping Space, and read the agent contract it serves. Flask runs
    this on an unhandled-exception 500, a 404 and the wall's 401 too, not only on a route's
    own answer."""
    response.headers["X-HeadStart"] = f"app; agent-api={_AGENT_API_VERSION}"
    return response


def _company_where(args) -> str | None:
    """The signed-in Account's follow/hide clause for this request (ADR-0171), or None.

    Read fresh per request rather than carried in the query string: the lists are Account
    state, so a bookmarked URL or a Saved Set must not be able to pin them to what they were.

    ``mine=1`` narrows to followed Boards. Hidden Boards are excluded on **every** request,
    with or without that flag — hiding a company means not seeing it, not "not seeing it while
    a toggle happens to be on".

    ``board=`` (repeatable) narrows to one company's Boards, the Trends and Hot tabs' hand-off
    (ADR-0185), and ``family=`` beside it to one role family of theirs. Neither needs an
    Account, so both apply with accounts off as well.
    """
    scoped = job_search.with_extra(
        job_search.scoped_boards_clause(args),
        job_search.scoped_jobs_clause(
            args,
            _FAMILY_IDS,
            {n: m["match"] for n, m in _WATCH.items()},
            known_families=_KNOWN_FAMILIES,
        ),
    )
    gate = _account_gate()
    if not gate:
        return scoped
    email, store = gate
    prefs = store.get_companies(subscription_id(email))
    return job_search.with_extra(
        scoped, job_search.request_account_clause(args, prefs.followed, prefs.hidden)
    )


@app.route("/search")
def search_jobs():
    """A thin adapter over the shared search path — parse/filter/rank live in JobSearch."""
    try:
        return jsonify(
            _searcher.run(request.args, extra_where=_company_where(request.args))
        )
    except (ValueError, job_search.ScopeUnavailable) as exc:
        body, status = job_search.refusal(exc)
        return jsonify(body), status


def _company_boards(board: str) -> tuple[str, ...]:
    """Every Board of the company holding ``board``, or ``board`` alone when no entry holds it."""
    return _COMPANY_BOARDS.get(board.lower(), (board,))


def _companies_json(prefs) -> dict:
    """The lists as the page reads them, with how many companies the hidden Boards make up: a
    hidden company is all of its Boards, so Boeing's twelve would read "12 companies hidden"."""
    return {
        "followed": list(prefs.followed),
        "hidden": list(prefs.hidden),
        "hidden_companies": len({_company_boards(b) for b in prefs.hidden}),
    }


@app.route("/companies")
def list_companies():
    """The Account's followed and hidden Boards."""
    gate = _account_gate()
    if not gate:
        return jsonify({"error": "accounts are not configured here"}), 503
    email, store = gate
    return jsonify(_companies_json(store.get_companies(subscription_id(email))))


@app.route("/companies", methods=["POST"])
def set_company():
    """Follow, hide, or clear the company one Board belongs to: every Board of its Company
    directory entry (ADR-0230). The whole record is rewritten, so the two lists cannot drift
    apart — `CompanyPrefs.with_boards` keeps them disjoint."""
    gate = _account_gate()
    if not gate:
        return jsonify({"error": "accounts are not configured here"}), 503
    email, store = gate
    body = _request_json_object()
    board = str(body.get("board") or "").strip()
    action = str(body.get("action") or "").strip()
    if not board or action not in ("follow", "hide", "clear"):
        return jsonify(
            {"error": "board and action (follow|hide|clear) are required"}
        ), 400
    account = subscription_id(email)
    current = store.get_companies(account)
    boards = _company_boards(board)
    if current.would_evict(boards, action):
        # Said on the page as it stands (setCompany), so in a job seeker's words (ADR-0255): the
        # cap counts Boards, which a reader knows as a company's career sites.
        return jsonify(
            {
                "error": f"That list is full: it holds up to {MAX_COMPANIES} company "
                "career sites. Remove a company first."
            }
        ), 409
    prefs = current.with_boards(boards, action)
    store.put_companies(prefs)
    return jsonify(_companies_json(prefs))


def _answer_response(body: bytes, gzipped: bytes | None = None) -> Response:
    """One read-only answer as its response (ADR-0251): gzipped where the browser takes it and a
    gzipped copy is given, since the Space's proxy compresses nothing (measured 2026-09-28), and
    kept by the browser for good when asked for under this boot's ``_ANSWERS_VERSION``, since
    nothing it reads changes until the next boot, which gives the page a new one."""
    zipped = gzipped is not None and request.accept_encodings.quality("gzip") > 0
    response = Response(gzipped if zipped else body, mimetype="application/json")
    if gzipped is not None:
        response.vary.add("Accept-Encoding")
    if zipped:
        response.headers["Content-Encoding"] = "gzip"
    if request.args.get("v") == _ANSWERS_VERSION:
        response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
        g.answer_for_everyone = True  # _AnswersLeaveTheSessionAlone
    return response


@app.route("/hot")
def hot_companies():
    """The actively-hiring companies ranked at boot (``_rank_hot``), or 503 with nothing ranked.

    Served whole rather than paged or filtered server-side: it is four lenses of at most 100
    rows each, so the lens switch and the "show staffing" toggle are instant in the browser and
    cost no round trip. 503 rather than an empty 200, so the tab can tell "not built yet" from
    "built, and nothing qualified".
    """
    if not _HOT:
        return jsonify({"error": "no hot list on this deployment yet"}), 503
    body = _json_body(_HOT)
    return _answer_response(body, _gzip(body))


@app.route("/job")
def read_jobs():
    """Up to five served Jobs by ``id=`` (repeatable), whole enough to read (ADR-0277): each
    search field plus the description (cut at ``description_limit``), department, the raw stated
    experience and ``unconfirmed`` — whether the latest scrape of its Board missed it, or null
    where this deployment does not know. An id the table does not hold is listed in ``missing``,
    not refused: why one may be (`job_absence.WHY_NOT_SERVED`) is an answer, and ``closest``
    names the served id on its Board most like it, where one is close (ADR-0367)."""
    ids = list(
        dict.fromkeys(i.strip() for i in request.args.getlist("id") if i.strip())
    )
    try:
        found = _searcher.jobs_by_id(ids)
    except ValueError as exc:
        return jsonify(error="invalid request", detail=str(exc)), 400
    missing = [i for i in ids if i not in found]
    ticks = _HISTORY.ticks
    return jsonify(
        {
            "jobs": [
                {
                    **found[i],
                    "unconfirmed": None if _UNCONFIRMED is None else i in _UNCONFIRMED,
                }
                for i in ids
                if i in found
            ],
            "missing": missing,
            # A missing id's likeliest mistyping: the id on its Board most like it (ADR-0367).
            "closest": _searcher.closest_ids(missing) if missing else {},
            "description_limit": job_search.JOB_DESCRIPTION_LIMIT,
            "newest_tick": ticks[-1] if ticks else None,
        }
    )


@app.route("/facets")
def search_facets():
    """Per-option result counts for the current filters (issue #275).

    Deliberately its own endpoint rather than a field on ``/search``. Counts are decided by the
    where-clause alone — a vector search ranks the filtered set rather than shrinking it — so
    this needs no query, no encoder call and no vector, and folding it into ``/search`` would
    have coupled ~40 counts to every ranked request and changed that route's response from the
    bare array its clients already read. The browser fires both at once, so the counts cost the
    user nothing beyond the search they were already waiting for.

    ``newest_tick`` is the newest Trends tick, or null (ADR-0253). The pipeline writes the table
    and the tick in one run and this process loaded both at one boot, so it dates the data an
    answer came from. The page does not read it.

    ``counts=total`` counts no option (ADR-0274): the total, ``blocking`` and
    ``description_coverage`` only, for an agent that prints nothing else. The page never sends it.
    """
    try:
        counted = _searcher.facets(
            request.args, extra_where=_company_where(request.args)
        )
    except (ValueError, job_search.ScopeUnavailable) as exc:
        body, status = job_search.refusal(exc)
        return jsonify(body), status
    ticks = _HISTORY.ticks
    return jsonify({**counted, "newest_tick": ticks[-1] if ticks else None})


def _parses(store: Store, account: str) -> int | None:
    """The spent-reads count, or None when it can't be known right now (the caller
    answers 503). ``parses_used`` raises on an unreadable or corrupt counter file rather
    than answering 0 — a transient failure must never reset the lifetime cap."""
    try:
        return store.parses_used(account)
    except Exception as exc:  # noqa: BLE001 — fail closed, whatever the read broke on
        print(f"[profile] parse counter unreadable for {account}: {exc}", flush=True)
        return None


def _profile_out(profile: Profile, used: int) -> dict:
    """The record as the UI reads it, with the spent/remaining arithmetic done here so
    the client never needs to know MAX_PARSES."""
    return {
        **profile.to_dict(),
        "parses_used": used,
        "parses_left": max(0, MAX_PARSES - used),
    }


@app.route("/profile")
def get_profile():
    """This Account's stored Profile (ADR-0041) — a blank one if nothing is stored yet."""
    gate = _account_gate()
    if not gate:
        return jsonify({"error": "profiles are not configured"}), 503
    email, store = gate
    account = subscription_id(email)
    used = _parses(store, account)
    if used is None:
        return jsonify({"error": "profile is temporarily unavailable — try again"}), 503
    profile = store.get_profile(account) or Profile.blank(email)
    return jsonify(_profile_out(profile, used))


@app.route("/profile", methods=["POST"])
def save_profile():
    """Save hand-edited Profile fields. The query sentence is scrubbed here exactly like
    the extracted one — the Query contract (CONTEXT.md) holds whichever door the sentence
    came through — and the parse counter is not this route's to write at all: it lives in
    its own file only the parse route touches, so a stale save cannot regress the cap."""
    gate = _account_gate()
    if not gate:
        return jsonify({"error": "profiles are not configured"}), 503
    email, store = gate
    account = subscription_id(email)
    used = _parses(store, account)
    if used is None:
        return jsonify({"error": "profile is temporarily unavailable — try again"}), 503
    body = _request_json_object()
    body["query"] = profile_extract.scrub_query(str(body.get("query") or ""))
    current = store.get_profile(account) or Profile.blank(email)
    updated = current.revised(body)
    store.put_profile(updated)
    return jsonify(_profile_out(updated, used))


# Accounts with a parse in flight. The cap check reads the counter before the router call and
# writes it after, and waitress serves requests on threads — so parallel parses would all read
# the same count and all pass the cap, each spending a router call. One read per Account at a
# time closes that window; the Space is a single process, so an in-process set is authoritative.
_PARSING: set[str] = set()
_PARSING_LOCK = threading.Lock()


@app.route("/profile/parse", methods=["POST"])
def parse_resume():
    """Paste a Résumé, get the stored Profile it implies — one LLM call (ADR-0041).

    The pasted text is used for this single call and never stored or logged; only the
    extraction is kept. Capped per Account for its lifetime: the cap bounds router spend,
    so any attempt that reached the router counts, even one that extracted nothing — and
    the counter is written *before* the router is asked and before the profile, so a
    failure anywhere after it can only over-count, never under-count (ADR-0041, #596)."""
    gate = _account_gate()
    if not gate:
        return jsonify({"error": "profiles are not configured"}), 503
    email, store = gate
    account = subscription_id(email)
    with _PARSING_LOCK:
        if account in _PARSING:
            return jsonify(
                {"error": "a résumé read is already running — wait for it"}
            ), 429
        _PARSING.add(account)
    try:
        return _run_resume_read(email, store, account)
    finally:
        with _PARSING_LOCK:
            _PARSING.discard(account)


def _run_resume_read(email: str, store: Store, account: str):
    """The parse itself, run while this Account holds its slot in `_PARSING`."""
    used = _parses(store, account)
    if used is None:
        return jsonify({"error": "profile is temporarily unavailable — try again"}), 503
    if used >= MAX_PARSES:
        return jsonify(
            {"error": f"no résumé reads left — this account has used all {MAX_PARSES}"}
        ), 400
    body = _request_json_object()
    # Reserved before the router is asked, and handed back only where ADR-0041 says nothing is
    # spent (#596): a failed reservation is a 503 with the router never reached.
    store.put_parses(account, used + 1)
    try:
        fields = profile_extract.extract(
            str(body.get("text") or ""), ask=llm_router.ask
        )
    except profile_extract.ResumeTooLong as exc:
        store.put_parses(account, used)
        return jsonify({"error": str(exc)}), 413
    except profile_extract.EmptyExtraction as exc:
        # The router answered — the call was spent, so it counts against the cap.
        return jsonify({"error": str(exc)}), 502
    except profile_extract.ResumeError as exc:  # EmptyResume: refused before the router
        store.put_parses(account, used)
        return jsonify({"error": str(exc)}), 400
    except llm_router.RouterUnavailable:
        store.put_parses(account, used)
        # Detail stays in the container log's traceback-free world: the caller only needs
        # "temporarily off", and the reason may name internal hosts.
        return jsonify({"error": "résumé reading is temporarily unavailable"}), 503
    updated = (store.get_profile(account) or Profile.blank(email)).revised(fields)
    store.put_profile(updated)
    return jsonify(_profile_out(updated, used + 1))


@app.route("/profile", methods=["DELETE"])
def delete_profile():
    """Remove the Profile's career record. The parse-counter file stays — deleting must
    not reset the lifetime cap (ADR-0041)."""
    gate = _account_gate()
    if not gate:
        return jsonify({"error": "profiles are not configured"}), 503
    email, store = gate
    store.remove_profile(subscription_id(email))
    return jsonify({"ok": True})


@app.errorhandler(StoreUnavailable)
def subscription_store_unavailable(exc):
    return jsonify(
        {"error": "saved settings are temporarily unavailable; try again"}
    ), 503


@app.errorhandler(StoreConflict)
def subscription_store_conflict(exc):
    return jsonify({"error": "settings changed; reload before trying again"}), 409


@app.route("/subscribe", methods=["POST"])
def subscribe():
    """Start email alerts for a Google-verified, allowlisted address (ADR-0035).

    The address is taken from the verified ID token and never from the request body — a
    caller-supplied address would let anyone sign a stranger up."""
    if not _ALERTS_ON:
        return jsonify({"error": "email alerts are not configured"}), 503
    body = _request_json_object()

    query = str(body.get("query") or "").strip()
    if not query:
        return jsonify({"error": "type the role you want first"}), 400
    if _SETS_ON:
        # ADR-0043: the sets endpoints are the only Subscription writer while sets are
        # live — a direct subscribe here could desync the projection from the emailing
        # set. The panel is hidden in this configuration; refuse direct calls too.
        return jsonify({"error": "manage email from the Matches tab"}), 409
    try:
        email = identity.verify(str(body.get("credential") or ""), _GOOGLE_CLIENT_ID)
    except identity.IdentityError as exc:
        return jsonify({"error": str(exc)}), 401

    store = _store()
    if not access.is_allowed(email, store.allowlist()):
        # Deliberately the same answer whether the list is missing or the address is absent.
        return jsonify({"error": "email alerts are invite-only — ask for access"}), 403

    sent = body.get("filters")
    search_filters = sent if isinstance(sent, dict) else {}
    _project_subscription(store, email, query, search_filters, reenable=True)
    return jsonify({"ok": True, "email": email})


@app.route("/unsubscribe")
def unsubscribe():
    """One-click unsubscribe — the token in the link is the only credential it needs, which
    is why a Digest can carry it and no session is involved."""
    if not _ALERTS_ON:
        return "email alerts are not configured", 503
    sub_id = (request.args.get("id") or "").strip()
    token = (request.args.get("token") or "").strip()
    if not sub_id or not token:
        return "that unsubscribe link is incomplete", 400

    store = _store()
    with store.atomic():
        sub = store.get(sub_id)
        if (
            sub
            and sub.unsubscribe_token
            # bytes: compare_digest raises TypeError on a non-ASCII str
            and hmac.compare_digest(sub.unsubscribe_token.encode(), token.encode())
        ):
            store.remove(sub.id)
            # The Subscription id IS the sets namespace for this address, so an unsubscribe
            # can and must clear the emailing flag — otherwise the tab keeps showing ✉ on,
            # and the next edit of that set would silently re-project (re-subscribe) it.
            for saved in store.sets_for(sub.id):
                if saved.emails:
                    store.put_set(replace(saved, emails=False))
            return (
                "<p style='font-family:system-ui'>Unsubscribed. No more job digests "
                "will be sent to this address.</p>"
            )
        return "that unsubscribe link is not valid", 404


def _account_gate() -> tuple[str, Store] | None:
    """The signed-in address and a store, or None when per-Account records can't function
    — shared by the sets and the saved-jobs endpoints, which have the same prerequisites.

    An Account route reaches it only with a session when the wall is on (before_request), so
    None there means the feature is unconfigured — the caller answers 503, mirroring the other
    dark features. `_company_where` also asks it on the public read routes, where None means
    no Account's clause applies: the caller is signed out, or Accounts are off."""
    email = session.get("email") if _AUTH_ON else None
    if not (_SETS_ON and email):
        return None
    return email, _store()


def _project_subscription(
    store: Store,
    email: str,
    query: str,
    search_filters: dict,
    *,
    reenable: bool = False,
) -> None:
    """Write the Subscription — the delivery projection of the emailing set (ADR-0043),
    and the same revise-or-create shape the wall-off /subscribe path uses.

    Revising keeps the Watermark and unsubscribe token (mailed links must survive edits);
    creating starts the Watermark now, so nobody is mailed the backlog. Subscription's own
    filter whitelist drops what a set may carry but a Digest may not (`seen_within`)."""
    existing = store.get(subscription_id(email))
    sub = (
        existing.revised(query, search_filters)
        if existing
        else Subscription.create(email, query, search_filters)
    )
    store.put(sub, reenable=reenable)


@app.route("/sets")
def list_sets():
    gate = _account_gate()
    if not gate:
        return jsonify({"error": "saved sets are not configured"}), 503
    email, store = gate
    with store.atomic():
        account = subscription_id(email)
        sets = store.sets_for(account)
        # Adoption (ADR-0043): an address that subscribed before sets existed has a live
        # Subscription but no set showing ✉ on — the split-brain the projection exists to
        # prevent. Materialize that Subscription as their emailing set, once.
        if not any(s.emails for s in sets) and len(sets) < MAX_SETS:
            sub = store.get(account)
            if sub and sub.email and sub.query:
                adopted = replace(
                    SavedSet.create(
                        email, sub.query[:60], sub.query, dict(sub.search_filters)
                    ),
                    emails=True,
                )
                store.put_set(adopted)
                sets.append(adopted)
        return jsonify([s.to_dict() for s in sets])


@app.route("/sets", methods=["POST"])
def save_set():
    """Create a Saved set, or update one when the body names an ``id`` (ADR-0043).

    Updating the emailing set re-projects the Subscription in the same request, so the
    delivered Digest can never drift from what the tab shows."""
    gate = _account_gate()
    if not gate:
        return jsonify({"error": "saved sets are not configured"}), 503
    email, store = gate
    with store.atomic():
        body = _request_json_object()
        name = str(body.get("name") or "").strip()
        query = str(body.get("query") or "").strip()
        if not name:
            return jsonify({"error": "name the set first"}), 400
        if not query:
            return jsonify({"error": "type the role you want first"}), 400
        sent = body.get("filters")
        filters = sent if isinstance(sent, dict) else {}
        account = subscription_id(email)

        set_id = str(body.get("id") or "")
        if set_id:
            current = store.get_set(account, set_id)
            if not current:
                return jsonify({"error": "no such set"}), 404
            updated = current.revised(name, query, filters)
            store.put_set(updated)
            if updated.emails:
                _project_subscription(
                    store, email, updated.query, updated.search_filters
                )
            return jsonify(updated.to_dict())

        if len(store.sets_for(account)) >= MAX_SETS:
            return jsonify({"error": f"that's the limit — {MAX_SETS} sets"}), 400
        fresh = SavedSet.create(email, name, query, filters)
        store.put_set(fresh)
        return jsonify(fresh.to_dict())


@app.route("/sets/<set_id>", methods=["DELETE"])
def delete_set(set_id: str):
    """Delete a set. Deleting the emailing one also removes its Subscription — a set that
    no longer exists must not keep mailing (ADR-0043; the old unsubscribe links die with
    it, which re-enabling later replaces with fresh ones)."""
    gate = _account_gate()
    if not gate:
        return jsonify({"error": "saved sets are not configured"}), 503
    email, store = gate
    with store.atomic():
        account = subscription_id(email)
        current = store.get_set(account, set_id)
        if not current:
            return jsonify({"error": "no such set"}), 404
        store.remove_set(account, set_id)
        if current.emails:
            store.remove(account)
        return jsonify({"ok": True})


@app.route("/sets/<set_id>/email", methods=["POST"])
def set_email(set_id: str):
    """Turn email on or off for one set — on moves it here from any other set.

    Delivery stays invite-only (ADR-0035): turning ON checks the allowlist; OFF removes
    the Subscription record, so the alerts run simply stops seeing this person."""
    gate = _account_gate()
    if not gate:
        return jsonify({"error": "saved sets are not configured"}), 503
    email, store = gate
    with store.atomic():
        account = subscription_id(email)
        current = store.get_set(account, set_id)
        if not current:
            return jsonify({"error": "no such set"}), 404
        body = _request_json_object()
        turn_on = bool(body.get("on"))

        if turn_on:
            if not access.is_allowed(email, store.allowlist()):
                return jsonify(
                    {"error": "email alerts are invite-only — ask for access"}
                ), 403
            for other in store.sets_for(account):
                if other.emails and other.id != current.id:
                    store.put_set(replace(other, emails=False))
            current = replace(current, emails=True)
            store.put_set(current)
            _project_subscription(
                store, email, current.query, current.search_filters, reenable=True
            )
        else:
            was_emailing = current.emails
            current = replace(current, emails=False)
            store.put_set(current)
            # Only the set that actually carried email may take the Subscription with it —
            # a stale tab toggling OFF on some other set must not stop someone's mail.
            if was_emailing:
                store.remove(account)
        return jsonify(current.to_dict())


@app.route("/saved")
def list_saved():
    """Every job this Account starred, newest star first, each annotated ``open`` —
    whether the posting is still in the index or has closed since (ADR-0042). The record
    is a display copy taken at star time, so a closed job still renders; only the badge
    changes."""
    gate = _account_gate()
    if not gate:
        return jsonify({"error": "saved jobs are not configured"}), 503
    email, store = gate
    jobs = store.saved_for(subscription_id(email))
    listed = _searcher.indexed([j.job_id for j in jobs])
    return jsonify([{**j.to_dict(), "open": j.job_id in listed} for j in jobs])


@app.route("/saved", methods=["POST"])
def star_job():
    """Star one job — the body carries the display copy the result card showed (ADR-0044).

    Starring an already-starred job overwrites its record (the id derives from the job
    id), refreshing the copy and the star time rather than duplicating."""
    gate = _account_gate()
    if not gate:
        return jsonify({"error": "saved jobs are not configured"}), 503
    email, store = gate
    # `job_id`, not `id`: the response's `id` is the RECORD id, and one key meaning two
    # things across request and response was a trap waiting for a caller.
    body = _request_json_object()
    job_id = str(body.get("job_id") or "").strip()
    title = str(body.get("title") or "").strip()
    if not job_id or not title:
        return jsonify({"error": "that job is missing its job_id or title"}), 400
    job = SavedJob.create(
        email,
        job_id,
        title,
        company=str(body.get("company") or ""),
        url=str(body.get("url") or ""),
        location=str(body.get("location") or ""),
        remote=bool(body.get("remote")),
        salary=str(body.get("salary") or ""),
    )
    # One listing answers both checks: a re-star may always overwrite, a new star may not
    # pass the cap. Cheaper than reading every record just to count them.
    held = store.saved_ids(job.account)
    if job.id not in held and len(held) >= MAX_SAVED:
        return jsonify({"error": f"that's the limit — {MAX_SAVED} saved jobs"}), 400
    store.put_saved(job)
    # `open` is answered true without asking the index: the caller drew this row from the
    # live index moments ago, and the Saved tab recomputes on every open (GET /saved), so
    # a star landing just after an Eviction self-corrects the first time it is visible.
    return jsonify({**job.to_dict(), "open": True})


@app.route("/saved/<saved_id>", methods=["DELETE"])
def unstar_job(saved_id: str):
    """Remove one star, by its record id (the star's own id, not the job's)."""
    gate = _account_gate()
    if not gate:
        return jsonify({"error": "saved jobs are not configured"}), 503
    email, store = gate
    account = subscription_id(email)
    if not store.get_saved(account, saved_id):
        return jsonify({"error": "not starred"}), 404
    store.remove_saved(account, saved_id)
    return jsonify({"ok": True})


def _resume_summary(document: dict) -> dict:
    """The listing row for one stored document — enough to show it and open it, never its words.

    The keys are the browser's own (`updatedAt`, not `updated_at`): the record is its export,
    and renaming them here would be the shape-to-shape mapping ADR-0124 refused."""
    return {
        "id": str(document.get("id") or ""),
        "name": str(document.get("name") or ""),
        "layoutId": str(document.get("layoutId") or ""),
        "updatedAt": str(document.get("updatedAt") or ""),
        "rev": int(document.get("rev") or 0),
    }


@app.route("/resumes")
def list_resumes():
    """Every Résumé document on this Account, newest edit first — summaries only.

    This is what makes sync worth having rather than merely true: a browser that has never seen
    these documents (a new machine, a cleared cache) finds them here and pulls one down."""
    gate = _account_gate()
    if not gate:
        return jsonify({"error": "account copies are not configured"}), 503
    email, store = gate
    return jsonify(
        [_resume_summary(d) for d in store.resumes_for(subscription_id(email))]
    )


@app.route("/resumes/<doc_id>")
def get_resume(doc_id: str):
    """One stored document, verbatim — the restore path."""
    gate = _account_gate()
    if not gate:
        return jsonify({"error": "account copies are not configured"}), 503
    email, store = gate
    document = store.get_resume(subscription_id(email), doc_id)
    if document is None:
        return jsonify({"error": "no such résumé"}), 404
    return jsonify(document)


@app.route("/resumes/<doc_id>", methods=["PUT"])
def put_resume(doc_id: str):
    """Store one Résumé document — the sync push (ADR-0124 decisions 1 and 4).

    **A conflict is refused, never merged and never overwritten.** The document carries a `rev`
    the client increments, and a push is accepted only when it is exactly one past what is
    stored; anything else answers 409 *with the stored document in the body*, so the client can
    keep both copies. Two devices editing the same résumé is somebody's afternoon, and the one
    thing this must never do is pick a winner quietly. A stored copy that cannot be READ is
    refused the same way: an unanswered Hub read is not evidence that the slot is empty, and
    treating it as one is the same "pick a winner quietly" with no winner chosen at all.

    The check is read-then-write, not a transaction — this is a Git repo, not a database. Two
    pushes landing inside the same few hundred milliseconds can both read the same `rev` and
    both be accepted, the second overwriting the first. The window is the Hub round trip, both
    devices still hold their own copy in the browser, and closing it properly is what ADR-0124
    weighed a transactional store for and declined. It is named rather than hidden."""
    gate = _account_gate()
    if not gate:
        return jsonify({"error": "account copies are not configured"}), 503
    email, store = gate
    if not is_resume_id(doc_id):
        return jsonify({"error": "that is not a résumé id"}), 400

    raw = request.get_data(cache=False)
    if len(raw) > MAX_RESUME_BYTES:
        return jsonify(
            {
                "error": f"that résumé is too large — the limit is {MAX_RESUME_BYTES // 1024} KB"
            }
        ), 413
    try:
        document = json.loads(raw)
    except ValueError:
        document = None
    if not isinstance(document, dict):
        return jsonify({"error": "that is not a résumé"}), 400
    # The document must name itself. Without this one line a client could file document A at
    # document B's path and silently replace it — the id in the URL and the id in the record
    # are two claims about the same thing, and only one of them is the record.
    if document.get("id") != doc_id:
        return jsonify({"error": "that résumé does not match its id"}), 400
    rev = document.get("rev")
    if not isinstance(rev, int) or isinstance(rev, bool) or rev < 1:
        return jsonify({"error": "that résumé is missing its revision"}), 400

    account = subscription_id(email)
    try:
        stored_rev = store.resume_revision(account, doc_id)
    except Exception as exc:  # noqa: BLE001 — any unreadable answer means the same thing here
        # The Hub did not answer, so there is no way to tell a first push from one that would
        # land on top of a newer copy. `get_resume` used to be the reader here and it answers
        # None for both, which took the "nothing to lose" branch below and overwrote another
        # device's work on a single blip. Refused, not accepted: 409 rather than 503 because
        # the client reads 503 as "this deployment keeps no account copies at all", and a 409
        # carrying no `stored` is already its "refused, and nothing here was overwritten" path.
        app.logger.warning(
            f"résumé {account}/{doc_id} unreadable, refusing the push: {exc}"
        )
        return jsonify(
            {"error": "your account could not be read — nothing was changed"}
        ), 409
    if stored_rev is None:
        # Nothing stored means nothing to lose, so any revision is accepted — the counter
        # guards stored content, and there is none. This is also the path a second device
        # takes after the first deleted the record, which a strict `rev == 1` would have
        # turned into an unresolvable conflict against an empty slot.
        held = store.resume_ids(account)
        if doc_id not in held and len(held) >= MAX_RESUMES:
            return jsonify({"error": f"that's the limit — {MAX_RESUMES} résumés"}), 400
    elif rev != stored_rev + 1:
        return jsonify(
            {
                "error": "this résumé changed somewhere else",
                # The refusal is only useful with the losing device's way out in it, so the
                # document is read again here — the revision decided the verdict, this is the
                # copy the client adopts. A read that fails now leaves `stored` null, which
                # the client already treats as "refused, keep what you have".
                "stored": store.get_resume(account, doc_id),
            }
        ), 409

    store.put_resume(account, doc_id, document)
    return jsonify({"ok": True, "rev": rev})


@app.route("/resumes/<doc_id>", methods=["DELETE"])
def delete_resume(doc_id: str):
    """Take one Résumé document off the Account.

    Gone from the tree immediately; gone from the repository's history when the scheduled
    squash next runs (`.github/workflows/squash-subscribers-history.yml`). ADR-0124 states the
    window as 30 days and the product says so — that sentence is true only while that workflow
    exists, which is why the workflow shipped in the same change as this route."""
    gate = _account_gate()
    if not gate:
        return jsonify({"error": "account copies are not configured"}), 503
    email, store = gate
    account = subscription_id(email)
    if doc_id not in store.resume_ids(account):
        return jsonify({"error": "no such résumé"}), 404
    store.remove_resume(account, doc_id)
    return jsonify({"ok": True})


@app.route("/trends")
def trends():
    """Role counts over time (ADR-0040, ADR-0051), answered by ``headstart.trends.trend_history``
    (ADR-0230), whose ``TrendHistory.unnetted_answer`` documents every parameter and field, and
    read by ``headstart.trends.line_reading``: its ``reading`` holds every figure the page shows,
    and the page only formats and draws (ADR-0233).

    ``?metric=`` ``stock`` or ``new``; ``?family=`` with ``&split=`` ``bands``, ``roles`` or
    ``company``; ``?since=`` / ``?until=`` / ``?base=`` (ISO-8601); ``?coverage=`` ``all`` or
    ``comparable``; ``?ats=`` and ``?company=`` (both repeatable). A bad question is a 400; a
    deployment without the ledger yet, or without the company directory a pick needs, is a 503.
    """
    try:
        body, gzipped = _served_trends(_HISTORY, _trends_question(request.args))
    except trend_history.TrendsUnavailable as exc:
        return jsonify(error=str(exc)), 503
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return _answer_response(body, gzipped)


def _trends_question(args) -> trend_history.TrendQuestion:
    """The ``/trends`` question ``args`` ask, as the page and the MCP tools spell it."""
    return trend_history.TrendQuestion(
        metric=args.get("metric", "stock"),
        coverage=args.get("coverage", "all"),
        family=args.get("family"),
        split=args.get("split", "bands"),
        companies=tuple(args.getlist("company")),
        since=args.get("since"),
        until=args.get("until"),
        base=args.get("base"),
        ats=tuple(args.getlist("ats")),
    )


def _trends_kept(question: trend_history.TrendQuestion) -> bool:
    """Whether ``question``'s answer is already worked out and kept this boot."""
    return (_HISTORY, _HISTORY.answer_key(question)) in _TRENDS_ANSWERED


# Each /trends body this boot has answered, least recently asked for first, and the one lock
# answering takes (`_served_trends`). At most _TRENDS_KEPT answers of at most ~0.6 MB, JSON and
# gzip together: room for the ~190 answered ahead (`_answer_views_a_click_away`) and several
# hundred more a boot's readers ask for.
_TRENDS_ANSWERED: OrderedDict[tuple, tuple[bytes, bytes]] = OrderedDict()
_TRENDS_ANSWERING = threading.Lock()
_TRENDS_KEPT = 512


def _served_trends(
    history: trend_history.TrendHistory, question: trend_history.TrendQuestion
) -> tuple[bytes, bytes]:
    """``question``'s ``/trends`` body over ``history``, as JSON and gzipped, answered once per
    boot (ADR-0251): a history never changes once loaded, so neither does any answer read from
    it, and the index-wide one costs ~1.5 s of the Space's CPU. Keyed on the history as well, so
    one loaded in its place answers afresh; a question that raises is not kept.

    One answer is worked out at a time. Under the GIL two at once finish no sooner, and a click
    that repeats a prefetch still in flight then waits for that answer rather than working it
    out a second time beside it. An answer already kept is read without the lock, so it never
    waits behind one being worked out.

    Kept by what the answer reads of the question (`TrendHistory.answer_key`, ADR-0261): a
    preset window's `since` is a new millisecond on every click, but every click between the
    same two ticks gets the answer the first one did. The least recently asked for goes first,
    so windows that do differ cannot push out the opening views everyone asks for."""
    key = (history, history.answer_key(question))
    kept = _TRENDS_ANSWERED.get(key)
    if kept is not None:
        try:
            _TRENDS_ANSWERED.move_to_end(key)
        except (
            KeyError
        ):  # let go by an answer worked out meanwhile; it is still this one
            pass
        return kept
    with _TRENDS_ANSWERING:
        if key not in _TRENDS_ANSWERED:
            body = _json_body(
                _trends_payload(history.unnetted_answer(question), question)
            )
            _TRENDS_ANSWERED[key] = (body, _gzip(body))
            if len(_TRENDS_ANSWERED) > _TRENDS_KEPT:
                _TRENDS_ANSWERED.popitem(last=False)
        return _TRENDS_ANSWERED[key]


def _trends_payload(answer: dict, question: trend_history.TrendQuestion) -> dict:
    """The answer with its line reading (ADR-0233), which holds every figure the page shows.

    A reading is never an error (decision 6). One that does not reconcile is served all the
    same, saying so, and logged; one that cannot be read at all is served as null with why,
    logged with its traceback. Either way the page draws the lines and says its figures do not
    fully reconcile, rather than the tab failing."""
    try:
        payload, reading = line_reading.trends_payload(answer)
    except Exception as exc:  # noqa: BLE001 - a reading that fails costs its figures only
        error = f"{type(exc).__name__}: {exc}"
        print(
            f"trends reading failed for {question}: {error}\n{traceback.format_exc()}",
            flush=True,
        )
        return line_reading.unread_trends_payload(answer, error)
    if not reading.reconciles:
        print(
            f"trends reading does not reconcile for {question}: "
            + "; ".join(reading.violations[:5]),
            flush=True,
        )
    return payload


#: How many of a view's lines the page charts, and so how many a click can open: app.js's
#: CHART_MAX, which a test holds this to. Only a charted category drills.
_CHART_MAX = 8
#: The Trends tab's date presets in days, app.js's `#trends-range`, each measured back from the
#: history's newest tick (`trends_newest_tick` in the page's config), not from the click's
#: moment, so a preset is one URL all boot (ADR-0269).
_PRESET_DAYS = (7, 30, 90)


def _answer_opening_views() -> None:
    """The view every Trends visit opens on, under both Measures, answered before the first
    visitor asks (ADR-0251), and each charted category's levels under it, which are what a
    click on the opening view opens (ADR-0261): 18 answers at boot, the boot log says how long,
    rather than one on each first click. Never fatal: a question that fails here fails the
    same way when asked, and is answered there."""
    started = time.monotonic()
    try:
        for metric in ("stock", "new"):
            body, _ = _served_trends(
                _HISTORY, trend_history.TrendQuestion(metric=metric)
            )
            for line in json.loads(body)["series"][:_CHART_MAX]:
                _served_trends(
                    _HISTORY,
                    trend_history.TrendQuestion(metric=metric, family=line["name"]),
                )
    except Exception as exc:  # noqa: BLE001 - the request path reports its own failure
        print(
            f"trends: opening views not answered ({type(exc).__name__}: {exc})",
            flush=True,
        )
        return
    print(
        f"trends: opening views answered in {time.monotonic() - started:.1f}s",
        flush=True,
    )


def _answer_views_a_click_away() -> None:
    """Every top-level view the tab's controls reach, and each charted category's levels under
    each, answered in the background once the opening views are (ADR-0269): both Measures,
    both Job sites and every date preset, 16 views and 128 drills. Then every Source but one
    under each Measure, what a first untick in the Source picker asks for, in the order the
    page lists them. The page asks ahead for a drawn view's neighbours and for the Source box
    the pointer rests on, and these are they, so a click on any of those controls is read
    from the browser, and the asking ahead from these kept answers. A thread, so the Space
    starts serving without waiting for them; one answer at a time under the one lock, so a
    reader's own new question waits behind at most one. Never fatal, as the opening views."""
    started = time.monotonic()
    newest = datetime.fromisoformat(_HISTORY.ticks[-1])
    windows = [None, *((newest - timedelta(days=d)).isoformat() for d in _PRESET_DAYS)]
    views = [
        trend_history.TrendQuestion(
            metric=metric,
            coverage=coverage,
            since=window if coverage == "all" else None,
            base=window if coverage == "comparable" else None,
        )
        for window in windows
        for coverage in ("all", "comparable")
        for metric in ("stock", "new")
    ]
    try:
        for view in views:
            _served_trends(_HISTORY, view)
        for view in views:
            body, _ = _served_trends(_HISTORY, view)
            for line in json.loads(body)["series"][:_CHART_MAX]:
                _served_trends(_HISTORY, replace(view, family=line["name"]))
        atses = _searcher.capabilities.atses
        for left_out in atses:
            for metric in ("stock", "new"):
                every_but_one = tuple(ats for ats in atses if ats != left_out)
                _served_trends(
                    _HISTORY,
                    trend_history.TrendQuestion(metric=metric, ats=every_but_one),
                )
    except Exception as exc:  # noqa: BLE001 - the request path reports its own failure
        print(
            f"trends: views a click away not answered ({type(exc).__name__}: {exc})",
            flush=True,
        )
        return
    print(
        f"trends: views a click away answered in {time.monotonic() - started:.1f}s",
        flush=True,
    )


if _HISTORY.ticks:
    _answer_opening_views()
    threading.Thread(
        target=_answer_views_a_click_away, name="trends-ahead", daemon=True
    ).start()


@app.route("/companies/suggest")
def suggest_companies():
    """Directory companies matching ``?q=`` for the Trends company picker (ADR-0185), best
    first, or 503 until the pipeline has written a directory.

    Each carries its current tech openings and Board count, so a real company is told apart
    from a one-posting slug collision of the same name, its Board keys, and how it matched the
    typed name (``match``, ADR-0253). ``?limit=`` defaults to 8, at most 20. A suggestion is
    only a candidate: nothing here resolves a typed name to a company.
    """
    if not _HISTORY.companies:
        return jsonify(error="no company directory on this deployment yet"), 503
    try:
        limit = max(1, min(int(request.args.get("limit", 8)), 20))
    except ValueError:
        return jsonify(error="limit must be an integer"), 400
    return _answer_response(
        _json_body(
            {"companies": _HISTORY.suggest_companies(request.args.get("q", ""), limit)}
        )
    )


# The most Board keys one /companies/lookup names: an agent reads at most ten companies at once.
_MAX_LOOKUP_BOARDS = 10


@app.route("/companies/lookup")
def lookup_companies():
    """The directory company holding each ``?board=`` (repeatable, at most ten), for an agent
    holding a Board key, such as the ``ats:slug`` prefix of a result id (ADR-0253).

    Any Board of a company names it, case-blind, as ``/trends`` accepts any Board of a pick.
    Each company comes once, in the order first named, shaped as a ``/companies/suggest`` item
    without ``match``. A key no directory company holds is a 400 naming it; no directory on this
    deployment yet is a 503.
    """
    if not _HISTORY.companies:
        return jsonify(error="no company directory on this deployment yet"), 503
    boards = [b.strip() for b in request.args.getlist("board") if b.strip()]
    if not boards or len(boards) > _MAX_LOOKUP_BOARDS:
        return jsonify(
            error="invalid lookup",
            detail=f"name 1 to {_MAX_LOOKUP_BOARDS} Board keys with board=; "
            f"got {len(boards)}",
        ), 400
    keys = [_HISTORY.company_of(board) for board in boards]
    unknown = [board for board, key in zip(boards, keys) if key is None]
    if unknown:
        return jsonify(
            error="unknown company",
            detail=f"no directory company holds {', '.join(unknown)}",
        ), 400
    return jsonify(companies=_HISTORY.describe_companies(list(dict.fromkeys(keys))))


@app.route("/companies/locations")
def company_locations():
    """The locations the served jobs on the ``?board=`` Boards (repeatable, 1 to 200) name most,
    most first, for an agent's company profile (ADR-0275): ``JobSearch.locations`` documents
    the answer. ``?limit=`` defaults to 10, at most 50. Scoped by Boards alone, so no Account's
    follow or hide list reaches it; a request naming no Board is a 400."""
    try:
        return jsonify(_searcher.locations(request.args))
    except ValueError as exc:
        body, status = job_search.refusal(exc)
        return jsonify(body), status


@app.route("/companies/levels")
def company_levels():
    """How many served jobs on the ``?board=`` Boards (repeatable, 1 to 200) are in each Trends
    level band, for an agent's company profile (ADR-0323): ``JobSearch.levels`` documents the
    answer. Scoped by Boards alone, as ``/companies/locations`` is; naming no Board is a 400."""
    try:
        return jsonify(_searcher.levels(request.args))
    except ValueError as exc:
        body, status = job_search.refusal(exc)
        return jsonify(body), status


@app.route("/requirements")
def role_requirements():
    """What a sample of the served jobs for a role (``q=``) and/or a category (``family=``) ask
    for, for an agent's requirements view (ADR-0324): ``JobSearch.requirements`` documents the
    sample and ``requirement_counts`` the counts. Takes every search filter and ``board=``. A
    Board that names no company is named by the Company directory (ADR-0323). Scoped by Boards
    and filters alone, so no Account's follow or hide list reaches it. Counts only: no
    description text is served."""
    try:
        answer = _searcher.requirements(
            request.args, _ROLE_ASSIGNMENTS, _HISTORY.board_and_name_of_job
        )
    except (ValueError, job_search.ScopeUnavailable) as exc:
        body, status = job_search.refusal(exc)
        return jsonify(body), status
    ticks = _HISTORY.ticks
    return jsonify({**answer, "newest_tick": ticks[-1] if ticks else None})


# HeadStart's MCP server, hosted (ADR-0267): the tools of `headstart.space_mcp` over Streamable
# HTTP, each reading the routes above in process, with no cookie. Anyone may add it to Claude by
# URL. The Origins it answers: none (a server-side client such as claude.ai's connector or Claude
# Code), Claude's two web origins, and this Space's own; any other is a page on another site,
# refused with a 403, because HF's edge reflects every Origin in its CORS preflight. The reads a
# call stopped waiting for run on, counted in `_MCP_ABANDONED_READS` (ADR-0276, ADR-0325).
_MCP_ABANDONED_READS = space_client.AbandonedReads()
_MCP_SERVER = space_mcp_server.build_server(
    env={}, fetch=space_client.wsgi_fetch(app, _MCP_ABANDONED_READS)
)
_MCP_ORIGINS = frozenset(
    {"https://claude.ai", "https://claude.com", space_client.SPACE_URL}
)

# How often one caller may ask `/mcp` (ADR-0267): 30 requests in any 60 s from one address,
# counted as ADR-0262 counts the read routes. A tool call reads two to five routes in process,
# none of them counted again, so 30 is already more reading than the 60 route requests one
# address may make directly, and a person's chat makes a few calls a minute. Every claude.ai
# user arrives from Anthropic's one published range, so that range shares 300 as one caller.
_MCP_LIMIT_REQUESTS = 30
_MCP_LIMIT = rate_limit.RateLimit(_MCP_LIMIT_REQUESTS, _LIMIT_WINDOW_S)
_ANTHROPIC_NETWORK = ipaddress.ip_network("160.79.104.0/21")
_ANTHROPIC_LIMIT_REQUESTS = 300
_ANTHROPIC_LIMIT = rate_limit.RateLimit(_ANTHROPIC_LIMIT_REQUESTS, _LIMIT_WINDOW_S)

# Connecting is counted apart from calling (ADR-0334). A client sends `initialize`, a
# notification and `tools/list` (or `server/discover`) before its first call, and pings between;
# counted against the 30 above, a caller that had spent them on calls got an empty tool list,
# and a model with no tools then invented HeadStart figures (round-3 critique P1-4). Each is
# answered in about a millisecond from constants built at start-up (212 of the 365 `/mcp` POSTs
# in the run log of 2026-09-29), so it waits for no place below either. Limited, not exempt:
# the endpoint takes no credential. 120 a minute is a connection every two seconds from one
# address, a campus NAT's worth; the range gets ten times that, as it does for calls.
_MCP_HANDSHAKE_REQUESTS = 120
_MCP_HANDSHAKE_LIMIT = rate_limit.RateLimit(_MCP_HANDSHAKE_REQUESTS, _LIMIT_WINDOW_S)
_ANTHROPIC_HANDSHAKE_REQUESTS = 1200
_ANTHROPIC_HANDSHAKE_LIMIT = rate_limit.RateLimit(
    _ANTHROPIC_HANDSHAKE_REQUESTS, _LIMIT_WINDOW_S
)

# At most 4 `/mcp` requests at once across every caller, on the Space's 2 vCPU: each fans out to
# two to four reads on threads, so 4 costs about what four people searching in the page at once
# do. At most 2 of them from one caller (ADR-0276), counted as the request limit counts it, so
# Anthropic's range is one caller: a call can hold its place for its whole 45 s deadline, and
# one caller's slow searches must not hold every place. One more waits up to 10 s for a place,
# then is told to retry.
#
# ADR-0334 measured these against the live Space on 2026-09-29 and kept 4: its reads are CPU-bound
# on the 2 vCPUs (country counts took 1.3 s alone and 2.4-2.8 s four at once; ranked searches 0.1
# s alone and 0.6 s six at once), so a fifth place would slow every call it runs beside and
# answer no more of them. What it changed is the range's share: it stands for every claude.ai user,
# so it may hold 3 of the 4, and one place is always left for a caller outside it.
_MCP_AT_ONCE = 4
_MCP_AT_ONCE_EACH = 2
_ANTHROPIC_AT_ONCE = 3
_MCP_PLACES = concurrency_limit.ConcurrencyLimit(
    _MCP_AT_ONCE, _MCP_AT_ONCE_EACH, {"anthropic": _ANTHROPIC_AT_ONCE}
)
_MCP_PLACE_WAIT_S = 10

# A description-keyword search takes one place of its own, and there is one (ADR-0325). It is
# CPU-bound: 16-18 s alone on the Space and 29-36 s beside another (measured 2026-09-29), so a
# second at once finishes neither sooner, and under a cold cache both pass the 45 s deadline.
# Out of the 4 places above, it never holds one for a fast call to queue behind: a fast search
# took 5.2 s alone and 7.7 s beside a scan. It waits 10 s like any call, then is told to retry
# in about the time one scan takes. Nor does a scan start while any read is running past its
# call's deadline: a call answers at 45 s, but its reads run on (ADR-0276), and a scan started
# then would share the CPU with them. It waits for them within the same 10 s, then is told to
# retry in a minute, as `wsgi_fetch` tells a read refused at its cap.
_MCP_SCANS_AT_ONCE = 1
_MCP_SCAN_PLACES = concurrency_limit.ConcurrencyLimit(
    _MCP_SCANS_AT_ONCE, _MCP_SCANS_AT_ONCE
)
_MCP_SCAN_RETRY_S = 20
_MCP_FINISHING_RETRY_S = 60

# Each distinct Origin `/mcp` has received this boot, logged once, so the first real connection
# shows what Anthropic's clients send. Bounded, since the header is the caller's to write.
_MCP_ORIGINS_SEEN: set[str] = set()
_MCP_ORIGINS_LOGGED = 50


def _from_anthropic(address: str) -> bool:
    try:
        return ipaddress.ip_address(address) in _ANTHROPIC_NETWORK
    except ValueError:
        return False


def _note_mcp_origin(origin: str | None, address: str) -> None:
    key = "(none)" if origin is None else origin
    if key in _MCP_ORIGINS_SEEN or len(_MCP_ORIGINS_SEEN) >= _MCP_ORIGINS_LOGGED:
        return
    _MCP_ORIGINS_SEEN.add(key)
    allowed = origin is None or origin in _MCP_ORIGINS
    print(
        f"[mcp] first request with Origin {key!r}: "
        f"{'allowed' if allowed else 'refused'}, "
        f"from Anthropic's range: {_from_anthropic(address)}",
        flush=True,
    )


def _scans_descriptions(body: bytes) -> bool:
    """Whether this `/mcp` POST is a search_jobs call matching its keyword in descriptions
    (ADR-0325), read before `streamable_http.answer` judges the request."""
    called = streamable_http.tool_call(body)
    return (
        called is not None
        and called.name == space_mcp_search_jobs.TOOL.name
        and space_mcp_search_jobs.scans_descriptions(called.arguments)
    )


def _mcp_refusal(body: bytes, status: int, message: str, wait_s: int):
    """A JSON-RPC error carrying the request's id, with `Retry-After` (ADR-0276): an MCP client
    shows its message to the model, in either protocol era."""
    status, headers, out = streamable_http.refusal(body, status, message)
    return Response(out, status, {**headers, "Retry-After": str(wait_s)})


@app.route("/mcp", methods=["POST"])
def mcp():
    """One MCP message over Streamable HTTP, answered by `streamable_http.answer` (ADR-0267)."""
    address = _client_address()
    _note_mcp_origin(request.headers.get("Origin"), address)
    body = request.stream.read(streamable_http.MAX_BODY_BYTES + 1)
    handshake = streamable_http.is_handshake(body)
    anthropic = _from_anthropic(address)
    caller, who = (
        ("anthropic", "Anthropic's range") if anthropic else (address, "one address")
    )
    # Connecting is counted apart from calling (ADR-0334), the range apart from one address.
    limit, per_minute = {
        (True, True): (_ANTHROPIC_HANDSHAKE_LIMIT, _ANTHROPIC_HANDSHAKE_REQUESTS),
        (True, False): (_ANTHROPIC_LIMIT, _ANTHROPIC_LIMIT_REQUESTS),
        (False, True): (_MCP_HANDSHAKE_LIMIT, _MCP_HANDSHAKE_REQUESTS),
        (False, False): (_MCP_LIMIT, _MCP_LIMIT_REQUESTS),
    }[anthropic, handshake]
    wait_s = limit.admit(caller)
    if wait_s:
        asked = "connection requests" if handshake else "requests"
        return _mcp_refusal(
            body,
            429,
            f"Too many {asked}: at most {per_minute} in {_LIMIT_WINDOW_S} s from {who}; "
            f"retry in {wait_s} s.",
            wait_s,
        )
    if handshake:
        status, headers, out = streamable_http.answer(
            request.headers, body, _MCP_SERVER, _MCP_ORIGINS
        )
        return Response(out, status, headers)
    scan = _scans_descriptions(body)
    places = _MCP_SCAN_PLACES if scan else _MCP_PLACES
    asked = time.monotonic()
    refused = places.take(caller, _MCP_PLACE_WAIT_S)
    if refused and scan:
        return _mcp_refusal(
            body,
            503,
            f"HeadStart runs {_MCP_SCANS_AT_ONCE} description-keyword search at a time, and "
            f"another is running; retry in about {_MCP_SCAN_RETRY_S} s, or match the keyword "
            "in titles (keyword_in: title), which is fast.",
            _MCP_SCAN_RETRY_S,
        )
    if refused is concurrency_limit.Refused.CALLER:
        return _mcp_refusal(
            body,
            429,
            f"Too many requests at once: at most {places.share(caller)} at a time from {who}; "
            "retry when one of them is answered.",
            _MCP_PLACE_WAIT_S,
        )
    if refused:
        return _mcp_refusal(
            body, 503, "HeadStart is busy; retry shortly.", _MCP_PLACE_WAIT_S
        )
    left_s = max(0.0, _MCP_PLACE_WAIT_S - (time.monotonic() - asked))
    if scan and not _MCP_ABANDONED_READS.wait_until_none(left_s):
        places.give_back(caller)
        return _mcp_refusal(
            body,
            503,
            "HeadStart is still finishing an earlier search that ran past its time limit, "
            "and starts a description-keyword search only once it has; retry in about a "
            "minute, or match the keyword in titles (keyword_in: title), which is fast.",
            _MCP_FINISHING_RETRY_S,
        )
    try:
        status, headers, out = streamable_http.answer(
            request.headers, body, _MCP_SERVER, _MCP_ORIGINS
        )
    finally:
        places.give_back(caller)
    return Response(out, status, headers)


@app.route("/auth/google", methods=["POST"])
def auth_google():
    """Trade a verified Google credential for the session cookie (ADR-0042).

    Sign-up is open: any Google-verified address gets a session. The costly features keep
    their own gates — this wall is identity, not entitlement."""
    if not _AUTH_ON:
        return jsonify({"error": "sign-in is not configured"}), 503
    body = _request_json_object()
    try:
        email = identity.verify(str(body.get("credential") or ""), _GOOGLE_CLIENT_ID)
    except identity.IdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    session.permanent = True
    session["email"] = email
    session["signed_in_at"] = time.time()
    return jsonify({"ok": True, "email": email})


@app.route("/signout", methods=["POST"])
def signout():
    # Guarded like /auth/google: with no SECRET_KEY the session is Flask's NullSession,
    # and clearing that raises rather than no-ops.
    if not _AUTH_ON:
        return jsonify({"error": "sign-in is not configured"}), 503
    session.clear()
    return jsonify({"ok": True})


@app.route("/me")
def me():
    """The identity behind the caller's own cookie — null when signed out or the wall is off.

    The page header reads this to show who is signed in."""
    return jsonify(
        {"auth": _AUTH_ON, "email": session.get("email") if _AUTH_ON else None}
    )


@app.route("/privacy")
def privacy():
    """The privacy policy — one canonical copy, `PRIVACY.md` in the repository."""
    return redirect(f"{_REPO}/blob/main/PRIVACY.md")


@app.route("/")
def index():
    capabilities = _searcher.capabilities
    if _AUTH_ON and not session.get("email"):
        # The door states what this is and proves it before asking for an identity
        # (ADR-0112). Every number is read rather than written, and every one is EXACT —
        # a tile that can only be approximated does not go on this page. Two table
        # queries: the row count the signed-in header already makes, and the freshness
        # window (~5 ms each, ADR-0084's primitive), both over the Jobs a search lists, so the
        # tiles and a search agree (ADR-0349). `n_new` is None on a table with no
        # `first_seen` column, and the template drops the tile rather than guess.
        return render_template(
            "signin.html",
            google_client_id=_GOOGLE_CLIENT_ID,
            njobs=f"{_searcher.n_served():,}",
            n_atses=len(capabilities.atses),
            n_new=_searcher.n_seen_within(_DOOR_NEW_HOURS),
            new_days=_DOOR_NEW_HOURS // 24,
            repo=_REPO,
        )
    scopes = keyword_scope_options()  # the Keyword filter's one map (ADR-0104)
    return render_template(
        "base.html",
        # the one blob the static JS reads (window.CFG); everything else is template-side
        cfg={
            "google_client_id": _GOOGLE_CLIENT_ID,
            # The Keyword filter's scopes (ADR-0104), scope -> "carries the description
            # disclaimer", plus the default the JS omits from a request — both read off the
            # same map the <select> below is rendered from, so the three cannot drift apart.
            "keyword_scopes": {value: needs for value, _, needs in scopes},
            "keyword_default_scope": KEYWORD_DEFAULT_SCOPE,
            # A no-query browse orders by `first_seen` only when the column exists; without
            # it the fallback is `id`, which is not a date at all. The line naming what the
            # user is looking at must not claim "newest first" on the second one.
            "has_first_seen": capabilities.has_first_seen,
            # The salary bracket's rate table (ADR-0117), so the page can print what a row
            # in another currency comes to in the one the user asked in — the SAME table the
            # where-clause was compiled from, never a second lookup, so the label beside a row
            # cannot disagree with the query that returned it. `None` when the table is
            # unreadable, and the page then converts nothing, exactly as `build_filter` does.
            "fx": fx.table(),
            # The most Boards one Search hand-off may name, so the Trends tab can say so
            # rather than send a request the route refuses.
            "max_scoped_boards": job_search.MAX_SCOPED_BOARDS,
            # Whether a Trends category can hand over as its exact Jobs, and up to how many.
            "family_handoff": _FAMILY_IDS is not None,
            "max_family_ids": job_search.MAX_FAMILY_IDS,
            # What the Trends, Hot and company-picker requests send as `v=`, so the browser may
            # keep their answers until the next boot (ADR-0251).
            "answers_version": _ANSWERS_VERSION,
            # What the Trends tab's date presets are measured back from (ADR-0269).
            "trends_newest_tick": _HISTORY.ticks[-1] if _HISTORY.ticks else None,
        },
        njobs=f"{_searcher.n_served():,}",
        atses=capabilities.atses,
        country_opts=country_filter.options(),
        india_opts=india_gazetteer.dropdown_options(),
        has_first_seen=capabilities.has_first_seen,
        # the Keyword filter (ADR-0104): its scopes from the one map, and whether the served
        # table carries the description column yet — description-bearing scopes are disabled
        # until it does
        keyword_scopes=scopes,
        keyword_default_scope=KEYWORD_DEFAULT_SCOPE,
        has_description=capabilities.has_description,
        # the "Highest salary" sort option — dark until the ADR-0082 columns exist on the
        # served table, the same rule `run` applies to the value the control would send
        has_min_salary=capabilities.has_min_salary_annual,
        # the salary bracket's currency picker (issue #275) — only the currencies the served
        # table actually carries, and the same list `build_filter` whitelists against
        currencies=capabilities.currencies,
        # The salary bracket converts across currencies (ADR-0117); the rail prints the date
        # of the rates it used, so a stale table is visible rather than silent.
        # Both facts, because the tip needs the second one: `as_of` says the table parsed,
        # but conversion only happens where the served currencies HAVE rates. Guarding the
        # claim on the date let a deployment with no comparable currencies still promise it.
        fx_as_of=fx.as_of(),
        fx_converts=_searcher.salary_bracket_converts,
        # the recency dropdowns, from the same tuples headstart.serving.facets counts (ADR-0084)
        seen_opts=facets.SEEN_OPTIONS,
        posted_opts=facets.POSTED_OPTIONS,
        repo=_REPO,  # links into the public repository, the privacy policy among them
        resume_sync_on=_SETS_ON,
        trends_on=bool(_HISTORY.ticks),
        hot_on=bool(_HOT),
        alerts_on=_ALERTS_ON,
        sets_on=_SETS_ON,
        companies_on=_SETS_ON,  # same prerequisites — the lists are per-Account records
        saved_on=_SETS_ON,  # same prerequisites — see the _SETS_ON comment
        profile_on=_SETS_ON,  # likewise (the parse button 503s on its own if the router is down)
    )


# How `python app.py` (start.sh) serves (#595, ADR-0279): waitress, where it used to be
# Werkzeug's development server. One process, on purpose: the résumé-read guard (`_PARSING`),
# every `RateLimit`, the `/mcp` places and the kept Trends and facet answers live in this
# process's memory, and a second worker process would keep a second copy of each.
#
# 16 threads on the Space's 2 vCPUs. No more than two requests can compute at once, so the other
# threads are there to wait: on the router for a résumé read, on an HF commit for each Saved set
# or Profile write, and up to `_MCP_PLACE_WAIT_S` for a `/mcp` place. ADR-0279 works the
# count out from `_MCP_AT_ONCE` and `_MCP_SCANS_AT_ONCE`, so a change to either revisits it.
#
# `clear_untrusted_proxy_headers` is off because waitress 3 otherwise deletes `X-Forwarded-For`
# whenever no trusted proxy is named, and `_client_address` reads the caller from that header
# (ADR-0262): without it, every caller would count as the edge's one address.
_WAITRESS_SETTINGS = {
    "host": "0.0.0.0",
    "port": 7860,
    "threads": 16,
    "clear_untrusted_proxy_headers": False,
}


if __name__ == "__main__":
    print(
        f"serving on port {_WAITRESS_SETTINGS['port']} with waitress, "
        f"{_WAITRESS_SETTINGS['threads']} threads",
        flush=True,
    )
    waitress.serve(app, **_WAITRESS_SETTINGS)
