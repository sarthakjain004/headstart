"""HeadStart semantic search — HF Space app (ADR-0020).

Pulls the LanceDB ``jobs`` table from the private HF dataset at startup (HF_TOKEN Space
secret), loads the nomic encoder (baked into the image), and serves the shared UI — the
templates and static files under ``headstart.ui`` — over the shared search path,
``search.JobSearch`` (ADR-0042). The local dev server (``scripts/ui/serve.py``) is a thin
adapter over the same two modules, so nothing here is duplicated there any more. Both import
``headstart`` the same way: the Space installs it as a real package rather than laying its
modules down flat (ADR-0153), so there is exactly one import path to keep in sync.
"""

from __future__ import annotations

import csv
import hmac
import json
import os
import threading
import time
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import lancedb
from flask import Flask, jsonify, redirect, render_template, request, session
from huggingface_hub import snapshot_download

import headstart  # only for headstart.__file__, to locate ui/ beside this package (ADR-0153)
from headstart import (
    company_match,
    embedding_conventions,
    facets,
    fx,
    geo,
    llm_router,
    profile_extract,
    search,
)

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
from headstart.board_identity import ats_of
from headstart.search_filter_compiler import (
    KEYWORD_DEFAULT_SCOPE,
    keyword_scope_options,
)

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
                # the trends ledger is ~3.4 MB of Parquet holding ~2.5M rows (ADR-0120 — it was
                # 172 MB of CSV, and this download ran on every cold start), and matches nothing
                # until the first pipeline run writes it — an absent pattern downloads nothing
                # rather than failing
                allow_patterns=[
                    "data/lancedb/*",
                    "data/state/role_trends.parquet",
                    "data/state/role_trend_board_deltas/*",
                    # the methodology epochs the Trends chart marks (ADR-0164) — a few rows
                    "data/state/trends_epochs.csv",
                    # the hot list (hot_boards) — a few tens of KB, and absent until a run
                    # writes one, which hides the tab rather than failing the pull
                    "data/state/hot_boards.json",
                    # the Trends company picker's directory (ADR-0185) — ~2 MB, and absent
                    # until a run writes one, which hides the picker rather than failing
                    "data/state/company_directory.json",
                    # each served Job's role family (ADR-0057), so a Trends category can hand
                    # over to Search as exact ids — ~4 MB, absent until a run writes one
                    "data/state/role_assignments.parquet",
                    # duplicate removals per run and Board (#649), so a company's line can leave
                    # them out exactly — small, and absent until a run writes one
                    "data/state/dedup_evictions.csv",
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
# (search.JobSearch); its startup scan supplies the ATS dropdown and the first_seen flag.
_searcher = search.JobSearch(_model, _table)
# The first page always browses with no filters and asks for the matching facet strip. Build both
# from this process's freshly-opened table before accepting traffic; every pipeline publication
# restarts the Space, so a new table necessarily gets new caches.
_searcher.warm()

# Role trends (ADR-0040). Same dark-until-ready shape as the two above: the ledger only exists
# after a pipeline run has written it, so an absent file hides the panel rather than erroring.
# Read once at startup — the Space restarts after every run, so it is never more than one run
# stale, and the file is a few dozen rows per run.
_NON_TECH = "non-tech"  # reserved diagnostic series — mirrors headstart.roles.NON_TECH
_NEW_WINDOW_DAYS = (
    7  # the `new` flow window — mirrors ingest.role_trends.NEW_WINDOW_DAYS
)


def _load_trends(path: Path) -> list[dict]:
    if not path.exists():
        return []
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    cols = {name: table.column(name).to_pylist() for name in table.schema.names}
    return [
        {
            "ts": ts.isoformat(timespec="seconds"),
            "version": int(version),
            "metric": metric,
            "family": family,
            "band": band,
            "ats": ats,
            "count": int(count),
        }
        for ts, version, metric, family, band, ats, count in zip(
            cols["ts"],
            cols["version"],
            cols["metric"],
            cols["family"],
            cols["band"],
            cols["ats"],
            cols["count"],
        )
    ]


def _load_board_deltas(path: Path) -> list[dict]:
    """Read the append-only Board-group deltas used for dynamic comparable coverage."""
    if not path.exists():
        return []
    import pyarrow.parquet as pq

    rows = []
    for file in sorted(path.glob("*.parquet")):
        table = pq.read_table(file)
        version = int((table.schema.metadata or {}).get(b"centroid_version", b"-1"))
        rows.extend({**row, "version": version} for row in table.to_pylist())
    return rows


def _family_labels(path: Path) -> dict[str, str]:
    """Display names, from the curated map under config/ (ADR-0040). The ledger stores slugs
    so a label can be reworded without breaking a series; this resolves them."""
    if not path.exists():
        return {}
    spec = json.loads(path.read_text(encoding="utf-8"))
    return {f["name"]: f.get("label", f["name"]) for f in spec["families"]}


_WATCH_PREFIX = "watch:"  # mirrors headstart.roles.WATCH_PREFIX (ADR-0051)


def _watch_meta(path: Path) -> dict[str, dict[str, str]]:
    """``{watch:name: {label, parent}}`` from the curated watchlist under config/ (ADR-0051),
    like the family map. Missing file means no watch roles — older deploys."""
    if not path.exists():
        return {}
    spec = json.loads(path.read_text(encoding="utf-8"))
    return {
        _WATCH_PREFIX + r["name"]: {
            "label": r.get("label", r["name"]),
            "parent": r["parent"],
        }
        for r in spec["roles"]
    }


def _load_hot(path: Path) -> dict:
    """The pre-ranked hot list (``headstart.ingest.hot_boards``), or ``{}`` until it exists.

    Read once at startup and served as-is. The ranking is a pipeline product, not a query: the
    ledgers behind it are tens of megabytes and the answer only changes when a run does, so
    re-deriving it per request would buy nothing and cost the Space its memory headroom.

    Empty on a deployment whose pipeline has not written it yet — the tab is then hidden rather
    than shown broken, the same dark-until-ready shape the Trends tab uses.
    """
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A half-written artifact must not take the Space down at import, which is the failure
        # mode `_pull_index` already exists to prevent for the index itself.
        return {}


_EPOCH_LABELS = (
    ("centroid_version", "role taxonomy refit"),
    ("family_map_fingerprint", "role family map edited"),
    ("tech_filter_version", "tech filter changed"),
    ("derivations_version", "experience/salary extraction changed"),
    ("dedup_version", "duplicate removal changed"),
    ("family_rules_fingerprint", "role family title rules changed"),
)


def _load_epochs(path: Path) -> list[dict]:
    """Methodology boundaries (ADR-0164): every row after the first names what changed since
    the row before it, so a chart can mark the point and a reader isn't left decoding raw
    version integers. The first recorded row is a baseline, not a boundary — there is nothing
    before it to contrast against, so it names nothing and is dropped rather than emitted empty.
    """
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for previous, row in zip([None, *rows], rows):
        if previous is None:
            continue
        # .get: a file from before a column existed lacks it until the next tick upgrades it
        moved = [
            (key, label)
            for key, label in _EPOCH_LABELS
            if row.get(key) != previous.get(key)
        ]
        if moved:
            # `fields` beside the labels, so code can key on what moved (the Trends tab asks
            # whether duplicate removal did) without matching prose someone may reword.
            out.append(
                {
                    "ts": row["ts"],
                    "changed": [label for _, label in moved],
                    "fields": [key for key, _ in moved],
                }
            )
    return out


def _load_evictions(path: Path) -> dict[str, list[tuple[str, int]]]:
    """``board -> [(ts, rows removed)]`` from the duplicate-removal ledger (#649), or empty.

    Its ``ts`` is the run's own stamp, the one role_trends writes, so a removal lands exactly
    on a charted run. Rows removed as duplicates are not closures, and a company's line leaves
    them out; the rule that removed them does not matter to that, so it is summed away."""
    if not path.exists():
        return {}
    out: dict[str, Counter] = defaultdict(Counter)
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                out[row["board"]][row["ts"]] += int(row["count"])
    except (OSError, ValueError, KeyError) as exc:
        print(f"dedup evictions unreadable ({exc}); none left out", flush=True)
        return {}
    return {board: sorted(by_ts.items()) for board, by_ts in out.items()}


_TRENDS = _load_trends(_STATE / "data" / "state" / "role_trends.parquet")
_CONFIG = Path(__file__).parent / "config"  # copied in beside this app (ADR-0153)
_WATCH = _watch_meta(_CONFIG / "role_watchlist.json")
_FAMILY_LABELS = _family_labels(_CONFIG / "role_families.json")
_EPOCHS = _load_epochs(_STATE / "data" / "state" / "trends_epochs.csv")
# The seniority bands `headstart.roles.band` writes, as a reader says them: the Level view's
# legend read "mid", "senior", "unspecified".
_BAND_LABELS = {
    "intern": "Internships",
    "entry": "Entry level (0–1 yrs)",
    "mid": "Mid level (2–4 yrs)",
    "senior": "Senior (5–7 yrs)",
    "staff": "Staff and above (8+ yrs)",
    "unspecified": "Experience not stated",
}
_EVICTIONS = _load_evictions(_STATE / "data" / "state" / "dedup_evictions.csv")
# A refit re-bases every series (ADR-0040), so never plot two versions on one axis: keep the
# newest only. Older rows stay in the ledger, they just aren't charted.
if _TRENDS:
    _live_version = max(r["version"] for r in _TRENDS)
    _TRENDS = [r for r in _TRENDS if r["version"] == _live_version]
_TREND_DELTAS = _load_board_deltas(
    _STATE / "data" / "state" / "role_trend_board_deltas"
)
_HOT = _load_hot(_STATE / "data" / "state" / "hot_boards.json")


def _load_directory(path: Path) -> dict[str, dict]:
    """The company directory (ADR-0185) as ``{company key: {name, boards}}``, or ``{}``.

    A company's key is its first board_key, and any of its Boards resolves to it through
    ``_COMPANY_OF``, so a Hot-tab row or a search result links to its company by the Board it
    already carries. Absent or half-written means no picker, never a failed boot.
    """
    if not path.exists():
        return {}
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))["companies"]
        return {entry["boards"][0]: entry for entry in entries}
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return {}


def _board_openings(deltas: list[dict], version: int | None) -> Counter[str]:
    """Each Board's current tech openings: its `stock` deltas summed at the live version.

    The directory carries no counts on purpose (ADR-0185); the delta ledger already holds
    them, and its first tick is a baseline of every Board's whole stock, so the sum is the
    level now. `non-tech` is not an opening a picker should count, and `watch:` rows re-count
    Jobs already counted in their family (ADR-0051).
    """
    openings: Counter[str] = Counter()
    for row in deltas:
        if _is_tech_stock(row, version):
            openings[row["board"]] += row["delta"]
    return openings


def _is_tech_stock(row: dict, version: int | None) -> bool:
    """A delta row counting tech openings at the live version: `stock`, not `non-tech`, and not
    a `watch:` row, which re-counts Jobs already counted in their family (ADR-0051)."""
    return (
        row["version"] == version
        and row["metric"] == "stock"
        and row["family"] != _NON_TECH
        and not row["family"].startswith(_WATCH_PREFIX)
    )


def _board_arrivals(
    deltas: list[dict], version: int | None
) -> dict[str, tuple[str, int]]:
    """Each Board's first tick at the live version, and the tech openings it arrived with.

    A Board's first delta is its whole stock at once (ADR-0143), so a Board found after a
    company's line began lands in that line as one step (ADR-0185). Measured 2026-09-24: 254 of
    852 multi-Board companies carry one, Hyatt's +1,048 over 83 Boards the largest.
    """
    first: dict[str, str] = {}
    for row in deltas:
        if row["version"] == version and row["metric"] == "stock":
            first[row["board"]] = min(first.get(row["board"], row["ts"]), row["ts"])
    arrived: Counter[str] = Counter()
    for row in deltas:
        if _is_tech_stock(row, version) and row["ts"] == first[row["board"]]:
            arrived[row["board"]] += row["delta"]
    return {board: (ts, arrived[board]) for board, ts in first.items()}


def _new_holds(arrivals: dict[str, tuple[str, int]]) -> dict[str, str]:
    """When each Board may count toward `new`: its first tick plus the flow window (ADR-0185).

    Every Board, the first tick's baseline included. Measured 2026-09-24: Amazon's `new` held at
    ~8,600 for exactly seven days from the ledger's first tick and then fell to 1,371, Google's
    1,690 to 489, so the ledger's first week reads a Board's whole backlog as new wherever the
    Board was found.
    """
    return {
        board: (
            datetime.fromisoformat(ts) + timedelta(days=_NEW_WINDOW_DAYS)
        ).isoformat(timespec="seconds")
        for board, (ts, _) in arrivals.items()
    }


def _build_candidates(
    companies: dict[str, dict], openings: Counter[str]
) -> list[company_match.Candidate]:
    return [
        company_match.Candidate(
            key=key,
            name=entry["name"],
            words=tuple(company_match.normalize(entry["name"])),
            openings=_company_openings(entry, openings),
        )
        for key, entry in companies.items()
    ]


def _company_openings(entry: dict, openings: Counter[str]) -> int:
    return sum(openings[board] for board in entry["boards"])


def _company_atses(entry: dict) -> list[str]:
    return sorted({ats_of(board) for board in entry["boards"]})


_FAMILY_IDS = search.load_family_ids(
    _STATE / "data" / "state" / "role_assignments.parquet"
)
_COMPANIES = _load_directory(_STATE / "data" / "state" / "company_directory.json")
_COMPANY_OF = {
    board: key for key, entry in _COMPANIES.items() for board in entry["boards"]
}
_LIVE_VERSION = _TRENDS[-1]["version"] if _TRENDS else None
_OPENINGS = _board_openings(_TREND_DELTAS, _LIVE_VERSION)
_CANDIDATES = _build_candidates(_COMPANIES, _OPENINGS)
_BOARD_ARRIVALS = _board_arrivals(_TREND_DELTAS, _LIVE_VERSION)
_NEW_HOLD = _new_holds(_BOARD_ARRIVALS)
# The first tick of the Board-delta ledger, before which no per-Board count exists.
_LEDGER_START = min((ts for ts, _ in _BOARD_ARRIVALS.values()), default=None)
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
# the cookie is the identity for weeks — re-sending the ~1h Google token would bounce users
# mid-use. Lax + Secure: it never rides a cross-site POST, and only travels over https.
# Rotating SECRET_KEY signs everyone out (their cookies stop verifying); nothing else breaks.
app.config.update(
    SECRET_KEY=_SECRET_KEY or None,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

# Paths that must answer signed out: the door itself, and the unsubscribe link every Digest
# already delivered carries — a session wall must never break a mailed link. `/me` answers
# from the caller's own cookie, so it can only tell you what you sent. `/privacy` is the URL
# Google's OAuth consent screen points strangers at before they have an Account.
_PUBLIC_PATHS = {"/", "/auth/google", "/me", "/unsubscribe", "/privacy"}

# The public repository, named once *for the Space*. Both trust surfaces (ADR-0112's door,
# ADR-0113's Data tab) and the `/privacy` redirect link into it, and "check it yourself" is the
# claim they rest on, so a rename must not leave half of one page's links dead.
# `scripts/ui/serve.py` necessarily keeps its own copy — it is the local renderer and shares no
# config with this module — and PRIVACY.md names the URL in prose.
_REPO = "https://github.com/sarthakjain004/headstart"

# The door's freshness window (ADR-0112). Seven days rather than 24 hours: a single day's
# intake swings with which Boards the run happened to slice, and a tile that halves overnight
# for no reason the visitor can see reads as broken rather than as honest.
_DOOR_NEW_HOURS = 168

# The Digest generator is the one caller with no Google identity to offer: it is a
# scheduled run, not a person, and it must reach /search for every Subscription
# (ADR-0035; ADR-0042's amendment records why the wall admits it). So it carries a shared
# secret, compared in constant time exactly as the unsubscribe token is. Scoped to /search
# alone — that is the whole of what the alerts run needs, so a leaked token buys a search
# rather than a session. Unset admits nobody, as in alerts.access.
_ALERTS_TOKEN = (
    (os.environ.get("ALERTS_TOKEN") or "").strip().encode("latin-1", "replace")
)
_SERVICE_PATHS = {"/search"}


def _service_caller() -> bool:
    if not _ALERTS_TOKEN or request.path not in _SERVICE_PATHS:
        return False
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    # Bytes, not str: headers decode as latin-1, and compare_digest raises TypeError on a
    # non-ASCII str — which would turn a rejected credential into a 500 from in here. Both
    # sides encode latin-1 so a token set identically really does compare equal; encoding
    # the config as utf-8 instead would make any non-ASCII token 401 forever.
    return scheme == "Bearer" and hmac.compare_digest(
        token.strip().encode("latin-1", "replace"), _ALERTS_TOKEN
    )


@app.before_request
def _require_sign_in():
    if not _AUTH_ON or request.path in _PUBLIC_PATHS:
        return None
    if _service_caller():
        return None
    if not session.get("email"):
        return jsonify({"error": "sign in first"}), 401
    return None


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
    scoped = search.with_extra(
        search.scoped_boards_clause(args),
        search.scoped_family_clause(args, _FAMILY_IDS),
    )
    gate = _account_gate()
    if not gate:
        return scoped
    email, store = gate
    prefs = store.get_companies(subscription_id(email))
    return search.with_extra(
        scoped, search.request_account_clause(args, prefs.followed, prefs.hidden)
    )


@app.route("/search")
def search_jobs():
    """A thin adapter over the shared search path — parse/filter/rank live in JobSearch."""
    try:
        return jsonify(
            _searcher.run(request.args, extra_where=_company_where(request.args))
        )
    except ValueError:
        return jsonify({"error": "invalid filter"}), 400


@app.route("/companies")
def list_companies():
    """The Account's followed and hidden Boards."""
    gate = _account_gate()
    if not gate:
        return jsonify({"error": "accounts are not configured here"}), 503
    email, store = gate
    prefs = store.get_companies(subscription_id(email))
    return jsonify({"followed": list(prefs.followed), "hidden": list(prefs.hidden)})


@app.route("/companies", methods=["POST"])
def set_company():
    """Follow, hide, or clear one Board. The whole record is rewritten, so the two lists
    cannot drift apart — `CompanyPrefs.with_board` keeps them disjoint."""
    gate = _account_gate()
    if not gate:
        return jsonify({"error": "accounts are not configured here"}), 503
    email, store = gate
    body = request.get_json(silent=True) or {}
    board = str(body.get("board") or "").strip()
    action = str(body.get("action") or "").strip()
    if not board or action not in ("follow", "hide", "clear"):
        return jsonify(
            {"error": "board and action (follow|hide|clear) are required"}
        ), 400
    account = subscription_id(email)
    current = store.get_companies(account)
    if current.would_evict(board, action):
        return jsonify(
            {"error": f"at most {MAX_COMPANIES} companies in each list"}
        ), 409
    prefs = current.with_board(board, action)
    store.put_companies(prefs)
    return jsonify({"followed": list(prefs.followed), "hidden": list(prefs.hidden)})


@app.route("/hot")
def hot_companies():
    """The pre-ranked actively-hiring list, or 503 until the pipeline has written one.

    Served whole rather than paged or filtered server-side: it is three lenses of at most 100
    rows each, so the lens switch and the "show staffing" toggle are instant in the browser and
    cost no round trip. 503 rather than an empty 200, so the tab can tell "not built yet" from
    "built, and nothing qualified".
    """
    if not _HOT:
        return jsonify({"error": "no hot list on this deployment yet"}), 503
    return jsonify(_HOT)


@app.route("/facets")
def search_facets():
    """Per-option result counts for the current filters (issue #275).

    Deliberately its own endpoint rather than a field on ``/search``. Counts are decided by the
    where-clause alone — a vector search ranks the filtered set rather than shrinking it — so
    this needs no query, no encoder call and no vector, and folding it into ``/search`` would
    have coupled ~40 counts to every ranked request and changed that route's response from the
    bare array its clients already read. The browser fires both at once, so the counts cost the
    user nothing beyond the search they were already waiting for.
    """
    try:
        return jsonify(
            _searcher.facets(request.args, extra_where=_company_where(request.args))
        )
    except ValueError:
        return jsonify({"error": "invalid filter"}), 400


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
    body = request.get_json(silent=True) or {}
    body["query"] = profile_extract.scrub_query(str(body.get("query") or ""))
    current = store.get_profile(account) or Profile.blank(email)
    updated = current.revised(body)
    store.put_profile(updated)
    return jsonify(_profile_out(updated, used))


# Accounts with a parse in flight. The cap check reads the counter before the router call and
# writes it after, and `app.run` serves requests on threads — so parallel parses would all read
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
    the counter is written *before* the profile, so a crash between the two writes can
    only over-count, never under-count."""
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
    body = request.get_json(silent=True) or {}
    try:
        fields = profile_extract.extract(
            str(body.get("text") or ""), ask=llm_router.ask
        )
    except profile_extract.ResumeTooLong as exc:
        return jsonify({"error": str(exc)}), 413
    except profile_extract.EmptyExtraction as exc:
        # The router answered — the call was spent, so it counts against the cap.
        store.put_parses(account, used + 1)
        return jsonify({"error": str(exc)}), 502
    except profile_extract.ResumeError as exc:  # EmptyResume: refused before the router
        return jsonify({"error": str(exc)}), 400
    except llm_router.RouterUnavailable:
        # Detail stays in the container log's traceback-free world: the caller only needs
        # "temporarily off", and the reason may name internal hosts.
        return jsonify({"error": "résumé reading is temporarily unavailable"}), 503
    store.put_parses(account, used + 1)
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
    body = request.get_json(silent=True) or {}

    query = (body.get("query") or "").strip()
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
            # bytes: compare_digest raises TypeError on a non-ASCII str (see _service_caller)
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

    Reached only with a session when the wall is on (before_request), so None here means
    the feature is unconfigured — the caller answers 503, mirroring the other dark
    features."""
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
        body = request.get_json(silent=True) or {}
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
        body = request.get_json(silent=True) or {}
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
    body = request.get_json(silent=True) or {}
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


def _replay_rows(
    base: str | None, comparable: bool, company_of: dict[str, str] | None
) -> tuple[list[dict], str | None]:
    """Rebuild counts from the Board-delta ledger for a chosen set of Boards.

    ``comparable`` keeps only Boards first observed by ``base`` (ADR-0143). ``company_of``
    keeps only the picked companies' Boards and tags every row with its company key, so the
    route can split by company (ADR-0185); None means every Board. The two combine: picked
    companies, counted only over the Boards already known at the base.

    Returns the rows and the first measurement charted (the base, when ``comparable``).
    """
    if not _TRENDS:
        return [], None
    version = _TRENDS[-1]["version"]
    deltas = [row for row in _TREND_DELTAS if row["version"] == version]
    if not deltas:
        return [], None
    stamps = sorted({row["ts"] for row in _TRENDS})
    first_delta = min(row["ts"] for row in deltas)
    eligible: set[str] | None = None
    if comparable:
        if base is None:
            base = next(
                (stamp for stamp in stamps if stamp >= first_delta), first_delta
            )
        # A base before per-Board counting began starts the cohort at the first run that
        # counted by Board: nothing earlier can be told apart, and answering "nothing" left a
        # 30-day window blank for a reader who only asked to hold coverage fixed (ADR-0185).
        base_stamp = max(
            (stamp for stamp in stamps if first_delta <= stamp <= base), default=None
        ) or next((stamp for stamp in stamps if stamp >= first_delta), None)
        if base_stamp is None:
            return [], None
        first: dict[str, str] = {}
        for row in deltas:
            if row["metric"] == "stock":
                first[row["board"]] = min(first.get(row["board"], row["ts"]), row["ts"])
        eligible = {board for board, seen in first.items() if seen <= base_stamp}
    else:
        base_stamp = next((stamp for stamp in stamps if stamp >= first_delta), None)
        if base_stamp is None:
            return [], None
    if company_of is not None:
        deltas = [row for row in deltas if row["board"] in company_of]
    by_stamp: dict[str, list[dict]] = defaultdict(list)
    for row in deltas:
        by_stamp[row["ts"]].append(row)
    state: Counter[tuple[str, str, str, str, str]] = Counter()
    rows = []
    measurements = set(stamps)
    # A Board's first week in the ledger reads its whole backlog as `new`, so its `new` deltas
    # wait out the flow window and are applied once it has passed, when the backlog has aged out
    # and what lands is real inflow. The Hot tab leaves new Boards out for the same reason
    # (ADR-0185). Every replay, a pick's or a comparable cohort's, reads the same ledger.
    held: dict[str, list[tuple[tuple[str, str, str, str, str], int]]] = defaultdict(
        list
    )
    # A delta can survive a failed aggregate append. Apply it before the next
    # measurement even though that interrupted tick is not itself charted.
    for stamp in sorted(measurements | by_stamp.keys()):
        for board in [b for b in held if _NEW_HOLD[b] <= stamp]:
            for key, delta in held.pop(board):
                state[key] += delta
        for row in by_stamp[stamp]:
            if eligible is None or row["board"] in eligible:
                company = company_of[row["board"]] if company_of else ""
                key = (company, row["metric"], row["family"], row["band"], row["ats"])
                if row["metric"] == "new" and stamp < _NEW_HOLD.get(row["board"], ""):
                    held[row["board"]].append((key, row["delta"]))
                    continue
                state[key] += row["delta"]
        if stamp < base_stamp or stamp not in measurements:
            continue
        rows.extend(
            {
                "ts": stamp,
                "company": company,
                "metric": metric,
                "family": family,
                "band": band,
                "ats": ats,
                "count": count,
            }
            for (company, metric, family, band, ats), count in state.items()
        )
    return rows, base_stamp


@app.route("/trends")
def trends():
    """Role counts over time (ADR-0040, ADR-0051), or 503 until the ledger exists.

    ``?metric=stock`` (default) is live openings; ``?metric=new`` is those first seen inside
    the ledger's flow window. Default view: one series per family, each point the family's
    total across bands. ``?family=<name>`` splits that family by seniority band, and
    ``&split=roles`` swaps the bands for the family's watched roles (ADR-0051) instead.
    ``?since=`` / ``?until=`` (ISO-8601, inclusive) narrow the window to runs whose stamp falls
    in range. Parsed and re-normalised to the ledger's own stored shape (``+00:00``, whole
    seconds) before comparing — a naive string compare against the browser's
    ``Date.toISOString()`` (milliseconds, a ``Z`` suffix) would silently misorder a value that
    names the *exact same instant* as a stamp, since ``'.'`` and ``'+'`` sort differently. A
    naive (timezone-less) value is read as UTC, matching the ledger. Either bound may be
    omitted; a malformed one is a 400, not a silent no-op; an out-of-data range returns a
    normal 200 with empty series rather than a 503, since the ledger itself is not empty.

    ``?coverage=comparable&base=`` (ADR-0143) selects every Board first observed at or before
    the requested base measurement, then replays only that cohort through later measurements.
    The Board-delta ledger starts with this feature, so an earlier base returns no fabricated
    history. Omitted coverage means full coverage.

    ``?ats=`` (repeatable, ADR-0075) narrows to the named ATSes; omitted entirely means every
    ATS, which is the only spelling of "no filter" — a request naming all of them explicitly
    would exclude every migrated pre-ADR-0075 row (they carry ``ats='all'``, matching no real
    name) and silently truncate history. A pre-ship stamp has no per-ATS breakdown to select
    from, so a narrow ``ats`` scope's series legitimately start later than an unfiltered one's.

    ``?company=`` (repeatable, ADR-0185) narrows to picked companies from the company
    directory, each named by **any** of its Boards' board_keys, so a Hot-tab row or a search
    result links by the key it already carries. A company's counts exist only per Board, so a
    pick replays the Board-delta ledger and its history starts at that ledger's first tick.
    ``&split=company`` draws one series per picked company (with ``family``, within that
    family), and ``companies`` echoes the picks with their labels. Under a pick, ``totals`` is
    the picks' combined total and ``company_totals`` each pick's own, so a line split by company
    can be a share of that company. ``counted_since`` maps each pick to its first counted tick,
    and ``ledger_start`` is the Board-delta ledger's first tick, before which no pick has history.
    Under a pick, a Board counts toward ``new`` only once the flow window has passed since its
    first tick, so its backlog never reads as a week's hiring; ``new_counted_from`` maps each
    pick to the first run its ``new`` can count.
    ``discovered`` lists ``{ts, company, boards, openings}``: Boards of a pick found after its
    line began, at the first charted run that counts them. Each is a step of openings that were
    already open, not hiring, so the chart marks it.
    An unknown key is a 400, and a deployment with no directory yet answers 503.

    ``totals`` carries the served table per stamp — narrowed by ``ats`` exactly like every
    other row here — so the caller can plot a **share** of what's currently in view rather than
    a raw count: with no ATS filter that's a share of the whole index, with one it's a share of
    the selected ATSes' own total, both coverage-immune in the same way. Watched roles are left
    out of it: they re-count Jobs already counted in their family, so adding them would inflate
    the denominator. ``watch_parents`` names the families that have any, so a caller can offer
    the roles split only where it leads somewhere.

    The reserved ``non-tech`` family is never a chart series — it rides along as ``non_tech``,
    the tech-filter health number, so the page can show it as a caveat rather than a role.

    ``epochs`` (ADR-0164) lists methodology boundaries within the requested window —
    ``{ts, changed}``, ``changed`` naming which of the role taxonomy, the family map, the tech
    filter, the experience/salary extraction, the duplicate-removal rules (ADR-0188) or the family
    title rules (ADR-0215) moved at that stamp. Unlike every other field above, it is **not**
    narrowed by ``ats`` or scoped to the live series version: a refit or a new generation of title
    rules is itself one of the things that can produce a boundary, so hiding it there would hide
    the exact event most worth marking. A chart can draw a marker at
    each stamp so a level shift reads as "we changed how we count" rather than being mistaken
    for a hiring trend."""
    if not _TRENDS:
        return jsonify(error="no trend data yet"), 503
    metric = request.args.get("metric", "stock")
    if metric not in ("stock", "new"):
        return jsonify(error="metric must be 'stock' or 'new'"), 400
    coverage = request.args.get("coverage", "all")
    if coverage not in ("all", "comparable"):
        return jsonify(error="coverage must be 'all' or 'comparable'"), 400
    family = request.args.get("family")
    split = request.args.get("split", "bands")
    if split not in ("bands", "roles", "company"):
        return jsonify(error="split must be 'bands', 'roles' or 'company'"), 400
    picked = request.args.getlist("company")
    company_of: dict[str, str] | None = None
    if picked:
        if not _COMPANIES:
            return jsonify(error="no company directory on this deployment yet"), 503
        unknown = [board for board in picked if board not in _COMPANY_OF]
        if unknown:
            return jsonify(error=f"unknown company: {', '.join(unknown)}"), 400
        company_of = {
            board: key
            for key in {_COMPANY_OF[board] for board in picked}
            for board in _COMPANIES[key]["boards"]
        }
    if split == "company" and not picked:
        return jsonify(error="split=company needs at least one company"), 400

    def _norm_stamp(raw: str) -> str:
        """``raw`` re-shaped to exactly how the ledger stores ``ts`` — see the docstring's note
        on why a raw string compare against an unnormalised bound is unsafe. ``fromisoformat``
        already parses a trailing ``Z`` natively (3.11+), so nothing needs stripping first."""
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat(timespec="seconds")

    try:
        since = _norm_stamp(request.args["since"]) if "since" in request.args else None
        until = _norm_stamp(request.args["until"]) if "until" in request.args else None
        base = _norm_stamp(request.args["base"]) if "base" in request.args else None
    except ValueError:
        return jsonify(error="since/until/base must be ISO-8601"), 400
    ats = request.args.getlist("ats")

    # ``_TRENDS`` is already pinned to the live series version at load time, so filtering here
    # never has to worry about a stray row from a stale refit; only the requested window changes.
    base_stamp = None
    if coverage == "comparable" or company_of is not None:
        # A company's counts exist only per Board, so a pick replays the delta ledger too;
        # its history therefore starts at that ledger's first tick, 2026-09-13 (ADR-0185).
        trends_rows, first_charted = _replay_rows(
            base, coverage == "comparable", company_of
        )
        if coverage == "comparable":
            base_stamp = first_charted
    else:
        trends_rows = _TRENDS
        first_charted = None
    if since:
        trends_rows = [r for r in trends_rows if r["ts"] >= since]
    if until:
        trends_rows = [r for r in trends_rows if r["ts"] <= until]
    if ats:
        trends_rows = [r for r in trends_rows if r["ats"] in ats]

    # Epochs (ADR-0164) are their own timeline, independent of the series version — a refit or a
    # rules generation is itself one of the things that can produce a boundary row, so filtering
    # by the live version would hide the exact event most worth marking. Only the requested window
    # narrows it.
    epochs = _EPOCHS
    if since:
        epochs = [e for e in epochs if e["ts"] >= since]
    if until:
        epochs = [e for e in epochs if e["ts"] <= until]

    # Stamps and the share denominator come from `trends_rows` (since/until/ats-narrowed, but
    # not the family/metric drill): total(ts) is every family + non-tech IN THAT SCOPE, since
    # count_groups assigns every row exactly once, which is what makes share coverage-immune —
    # an index (or an ATS selection) that grew 1.5% overnight moves every count but no share
    # (ADR-0051, scope extended to ATS by ADR-0075).
    stock = [r for r in trends_rows if r["metric"] == "stock"]
    stamps = sorted({r["ts"] for r in stock})
    totals: dict[str, int] = {}
    for r in stock:
        if not r["family"].startswith(_WATCH_PREFIX):  # watch rows re-count family rows
            totals[r["ts"]] = totals.get(r["ts"], 0) + r["count"]

    rows = [
        r for r in trends_rows if r["metric"] == metric and r["family"] != _NON_TECH
    ]
    if split == "company":
        # One series per picked company (ADR-0185): its whole tech total, or within one family.
        rows = [
            r
            for r in rows
            if (
                r["family"] == family
                if family
                else not r["family"].startswith(_WATCH_PREFIX)
            )
        ]
        key = "company"
    elif family and split == "roles":
        # The family's watched sub-roles (ADR-0051), each its own series.
        wanted = {n for n, meta in _WATCH.items() if meta["parent"] == family}
        rows = [r for r in rows if r["family"] in wanted]
        key = "family"
    elif family:
        rows = [r for r in rows if r["family"] == family]
        key = "band"
    else:
        rows = [r for r in rows if not r["family"].startswith(_WATCH_PREFIX)]
        key = "family"

    # Stamps where the `new` metric was recorded at all. `count_groups` writes only non-empty
    # groups, so on such a stamp a series with no row genuinely saw zero fresh openings —
    # whereas a stamp with no `new` rows anywhere is one this metric did not yet exist for.
    # That second case is an inference from row presence, not a recorded fact: a run where
    # nothing anywhere was new would read as unmeasured. At this corpus size that has never
    # happened, and the honest alternative (a per-run marker row) costs more than it settles.
    # A pick's own rows cannot answer it: one company can go a whole run with nothing new,
    # which is a 0, not a gap. So under a pick the whole ledger says which runs measured `new`.
    measured = {
        r["ts"]
        for r in (_TRENDS if company_of else trends_rows)
        if r["metric"] == "new"
    }

    def value_at(
        points: dict[str, int], ts: str, counts_from: str | None = None
    ) -> int | None:
        """A series' value at one stamp — 0 where the metric ran and found none, else None.

        ``counts_from`` is when a pick's ``new`` first counts (``_new_holds``): before it every
        Board of the series is held, so the run measured nothing for it, which is a gap — a 0
        there drew a week of nothing and then a leap that read as a hiring surge."""
        if counts_from is not None and ts < counts_from:
            return None
        return points.get(ts, 0 if metric == "new" and ts in measured else None)

    picked_keys = sorted(set(company_of.values())) if company_of else []
    # A pick with rows in scope gets a line even when this metric has none of them — for `new`,
    # "nothing opened this week" is a line at 0, and a company silently missing from the legend
    # would read as a bug. A pick with no rows at all (outside a comparable cohort, the ATS
    # selection or the window) gets none, and is named in `uncounted`.
    in_scope = {r["company"] for r in trends_rows} if company_of else set()
    series: dict[str, dict[str, int]] = {
        k: {} for k in picked_keys if key == "company" and k in in_scope
    }
    for r in rows:  # sum over the other axis, so a family point is its total
        series.setdefault(r[key], {})
        at = series[r[key]]
        at[r["ts"]] = at.get(r["ts"], 0) + r["count"]
    company_labels = _company_labels(picked_keys)
    # Each pick's own first counted tick (over the Boards in scope), which is where its line
    # starts: the ledger's first tick for most, later for the 9,981 companies first counted
    # after it (measured 2026-09-24). The chart names it, so a short line never reads as the
    # company's whole history.
    counted = {
        board: pick
        for board, pick in (company_of or {}).items()
        if board in _BOARD_ARRIVALS and not (ats and ats_of(board) not in ats)
    }
    began: dict[str, str] = {}
    new_from: dict[
        str, str
    ] = {}  # when each pick's `new` first counts (see _new_holds)
    for board, pick in counted.items():
        ts = _BOARD_ARRIVALS[board][0]
        began[pick] = min(began.get(pick, ts), ts)
        release = _NEW_HOLD.get(board, ts)
        new_from[pick] = min(new_from.get(pick, release), release)

    def _series_label(name: str) -> str:
        if key == "company":
            return company_labels[name]
        if key == "band":
            return _BAND_LABELS.get(name, name)
        if name in _WATCH:
            return _WATCH[name]["label"]
        return _FAMILY_LABELS.get(name, name)

    # Under `new`, where a series' first counted run is: a company line's own pick's release,
    # and for a line summing several picks the earliest, after which each later one joins the
    # sum as a marked step (the page's stepNotes).
    def counts_from(name: str) -> str | None:
        if metric != "new" or not company_of:
            return None
        if key == "company":
            return new_from.get(name)
        return min(new_from.values(), default=None)

    out = [
        {
            "name": name,
            "label": _series_label(name),
            # None (not 0) where a run has no row for this series: a gap is "not measured",
            # and plotting it as zero would invent a crash that never happened. The "new"
            # metric refines that: count_groups writes only non-empty groups, so on a stamp
            # where new WAS measured (any new row exists), a missing series row genuinely
            # means zero fresh openings; a stamp with no new rows at all predates ADR-0051
            # and stays a gap.
            "points": [value_at(points, ts, counts_from(name)) for ts in stamps],
            "latest": value_at(points, stamps[-1], counts_from(name))
            if stamps
            else None,
        }
        for name, points in series.items()
    ]
    out.sort(key=lambda s: -(s["latest"] or 0))
    non_tech: dict[str, int] = {}
    for row in stock:
        if row["family"] == _NON_TECH:
            non_tech[row["ts"]] = non_tech.get(row["ts"], 0) + row["count"]
    # Each pick's own denominator, so a line split by company is a share of *that* company.
    company_totals: dict[str, dict[str, int]] = {k: {} for k in picked_keys}
    for row in stock:
        if company_of and not row["family"].startswith(_WATCH_PREFIX):
            at = company_totals[row["company"]]
            at[row["ts"]] = at.get(row["ts"], 0) + row["count"]
    # Boards of a pick found after its line began: each lands its tech openings at once, openings
    # that were already open, so the chart marks the step rather than let it read as hiring. A
    # Board that lands on the charted point where its company's line begins starts that line and
    # is not a step, nor is one that brought no tech openings. None under comparable coverage,
    # which leaves every such Board out of the cohort.
    # Under `new` a found Board steps the line when its hold ends, not when it arrived.
    found: dict[tuple[str, str], list[int]] = {}
    if coverage != "comparable" and stamps:
        for board, pick in counted.items():
            ts, openings = _BOARD_ARRIVALS[board]
            if metric == "new":
                ts = _NEW_HOLD.get(board, ts)
            at = bisect_left(stamps, ts)
            if (
                openings <= 0
                or at == len(stamps)
                # the pick's line begins at its own first counted run: under `new`, where its
                # first Board's hold ends, not where it arrived — the Sep 20 start of every
                # line was marked as "boards found later"
                or at
                <= bisect_left(
                    stamps, new_from[pick] if metric == "new" else began[pick]
                )
            ):
                continue
            bucket = found.setdefault((stamps[at], pick), [0, 0])
            bucket[0] += 1
            bucket[1] += openings
    # Which families have watched sub-roles, so the UI can offer the roles drill only there.
    watch_parents = sorted({meta["parent"] for meta in _WATCH.values()})
    return jsonify(
        version=_TRENDS[-1]["version"],
        coverage=coverage,
        base=base_stamp,
        metric=metric,
        stamps=stamps,
        series=out,
        totals=[totals.get(ts) for ts in stamps],
        non_tech=[non_tech.get(ts) for ts in stamps],
        split_by=key,
        # The drilled family's display name, so a cold link into a drill can name it.
        family_label=_FAMILY_LABELS.get(family, family) if family else None,
        watch_parents=watch_parents,
        epochs=epochs,
        # With its Board keys, so the chart can hand a pick to Search by Board (ADR-0185).
        companies=[
            {
                **_company_json(k, company_labels[k]),
                "board_keys": _COMPANIES[k]["boards"],
            }
            for k in picked_keys
        ],
        company_totals={
            k: [company_totals[k].get(ts) for ts in stamps] for k in picked_keys
        },
        counted_since=began,
        # Picks with nothing in this scope — a comparable cohort they joined after, an ATS
        # selection or a window they have no Boards in — so the page names them rather than
        # charting fewer companies than the chips show.
        uncounted=[k for k in picked_keys if k not in in_scope],
        ledger_start=_LEDGER_START,
        new_counted_from=new_from,
        discovered=[
            {"ts": ts, "company": pick, "boards": n, "openings": openings}
            for (ts, pick), (n, openings) in sorted(found.items())
        ],
        # Duplicate rows removed from each pick's Boards, per charted run (#649).
        evicted=_picks_evicted(counted, stamps),
    )


def _picks_evicted(counted: dict[str, str], stamps: list[str]) -> list[dict]:
    """``[{ts, company, count}]``: each pick's duplicate removals at the charted run that shows
    them — the first at or after the removal's own stamp, which is normally that stamp."""
    if not stamps:
        return []
    at: Counter = Counter()
    for board, pick in counted.items():
        for ts, count in _EVICTIONS.get(board, ()):
            k = bisect_left(stamps, ts)
            if 0 < k < len(stamps) and count:
                at[(stamps[k], pick)] += count
    return [
        {"ts": ts, "company": pick, "count": n} for (ts, pick), n in sorted(at.items())
    ]


def _company_json(key: str, label: str) -> dict:
    """A directory company as the picker and the chart show it."""
    entry = _COMPANIES[key]
    return {
        "key": key,
        "name": entry["name"],
        "label": label,
        "atses": _company_atses(entry),
        "boards": len(entry["boards"]),
        "openings": _company_openings(entry, _OPENINGS),
    }


def _company_labels(keys: list[str]) -> dict[str, str]:
    """Each company's name, told apart from any other in ``keys`` that shares it.

    The directory keeps same-named employers apart when nothing proves them one (ADR-0185), so
    "Citi" on Workday and "Citi" on Eightfold both appear, labelled by ATS. Two on the *same*
    ATS (220 name pairs measured) are labelled by their key, the one thing they cannot share.
    """
    names = Counter(_COMPANIES[key]["name"] for key in keys)
    with_ats = {
        key: f"{_COMPANIES[key]['name']} ({', '.join(_company_atses(_COMPANIES[key]))})"
        for key in keys
    }
    still_shared = Counter(with_ats.values())
    labels = {}
    for key in keys:
        name = _COMPANIES[key]["name"]
        if names[name] == 1:
            labels[key] = name
        elif still_shared[with_ats[key]] == 1:
            labels[key] = with_ats[key]
        else:
            labels[key] = f"{name} ({key})"
    return labels


@app.route("/companies/suggest")
def suggest_companies():
    """Directory companies matching ``?q=`` for the Trends company picker (ADR-0185), best
    first, or 503 until the pipeline has written a directory.

    Each carries its current tech openings and Board count, so a real company is told apart
    from a one-posting slug collision of the same name. ``?limit=`` defaults to 8, at most 20.
    A suggestion is only a candidate: nothing here resolves a typed name to a company.
    """
    if not _COMPANIES:
        return jsonify(error="no company directory on this deployment yet"), 503
    try:
        limit = max(1, min(int(request.args.get("limit", 8)), 20))
    except ValueError:
        return jsonify(error="limit must be an integer"), 400
    found = company_match.suggest(request.args.get("q", ""), _CANDIDATES, limit)
    labels = _company_labels([candidate.key for candidate in found])
    return jsonify(companies=[_company_json(c.key, labels[c.key]) for c in found])


@app.route("/auth/google", methods=["POST"])
def auth_google():
    """Trade a verified Google credential for the session cookie (ADR-0042).

    Sign-up is open: any Google-verified address gets a session. The costly features keep
    their own gates — this wall is identity, not entitlement."""
    if not _AUTH_ON:
        return jsonify({"error": "sign-in is not configured"}), 503
    body = request.get_json(silent=True) or {}
    try:
        email = identity.verify(str(body.get("credential") or ""), _GOOGLE_CLIENT_ID)
    except identity.IdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    session.permanent = True
    session["email"] = email
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


@app.route("/coverage")
def coverage():
    """What the served table actually carries, counted live (ADR-0113).

    The Data tab reads this. Its own route rather than a field on ``index`` because the tab
    is opened by a minority of visits and the counts, though cheap, are not free on the
    first one — and because a number rendered into the page at boot would freeze at
    whatever the table held then, which is the staleness this ADR exists to avoid.
    """
    return jsonify(_searcher.coverage())


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
        # window (~5 ms each, ADR-0084's primitive). `n_new` is None on a table with no
        # `first_seen` column, and the template drops the tile rather than guess.
        return render_template(
            "signin.html",
            google_client_id=_GOOGLE_CLIENT_ID,
            njobs=f"{_table.count_rows():,}",
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
            "max_scoped_boards": search.MAX_SCOPED_BOARDS,
            # Whether a Trends category can hand over as its exact Jobs, and up to how many.
            "family_handoff": _FAMILY_IDS is not None,
            "max_family_ids": search.MAX_FAMILY_IDS,
        },
        njobs=f"{_table.count_rows():,}",
        atses=capabilities.atses,
        india_opts=geo.dropdown_options(),
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
        # the recency dropdowns, from the same tuples headstart.facets counts (ADR-0084)
        seen_opts=facets.SEEN_OPTIONS,
        posted_opts=facets.POSTED_OPTIONS,
        repo=_REPO,  # the Data tab's "check any of it" links (ADR-0113)
        # The Data tab's storage list must describe THIS deployment. With the wall off there
        # is no account, so it says so rather than listing what a different one would keep.
        auth_on=_AUTH_ON,
        resume_sync_on=_SETS_ON,
        trends_on=bool(_TRENDS),
        hot_on=bool(_HOT),
        alerts_on=_ALERTS_ON,
        sets_on=_SETS_ON,
        companies_on=_SETS_ON,  # same prerequisites — the lists are per-Account records
        saved_on=_SETS_ON,  # same prerequisites — see the _SETS_ON comment
        profile_on=_SETS_ON,  # likewise (the parse button 503s on its own if the router is down)
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860)
