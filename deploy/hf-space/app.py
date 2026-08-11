"""HeadStart semantic search — HF Space app (ADR-0020).

Self-contained twin of ``scripts/ui/serve.py``: pulls the LanceDB ``jobs`` table from the
private HF dataset at startup (HF_TOKEN Space secret), loads the nomic encoder (baked into the
image), and serves the same filter-then-rank search page. The search conventions (model id,
task prefixes, filter clause) mirror ``headstart.search`` — keep them in lockstep.
"""

from __future__ import annotations

import csv
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import geo  # India gazetteer — synced from src/headstart/geo.py by deploy-space.yml
import lancedb
import llm_router  # synced from src/headstart/llm_router.py by deploy-space.yml (ADR-0032)
import resume_query  # synced from src/headstart/resume_query.py by deploy-space.yml

# alerts/ is a package, synced from src/headstart/alerts/ by deploy-space.yml (ADR-0035).
# Only these three members are imported here: alerts/__init__.py is empty on purpose, so the
# Space never loads the Digest or Resend modules, whose dependencies it does not install.
from alerts import access, identity
from alerts.store import Store, Subscription, subscription_id
from flask import Flask, jsonify, request, session
from huggingface_hub import snapshot_download

MODEL = "nomic-ai/nomic-embed-text-v1.5"
QUERY_PREFIX = "search_query: "  # load-bearing (ADR-0005)
TABLE = "jobs"
DATASET = os.environ.get("HF_DATASET", "imPoseidon/headstart-index")
_STATE = Path("/app/state")
_MAX_K = 100  # cap the page size so a crafted k can't dump the whole table

print(f"pulling index from {DATASET} ...", flush=True)
snapshot_download(
    DATASET,
    repo_type="dataset",
    local_dir=_STATE,
    # the trends ledger is tiny (a few dozen rows per run) and matches nothing until the
    # first pipeline run writes it — an absent pattern downloads nothing rather than failing
    allow_patterns=["data/lancedb/*", "data/state/role_trends.csv"],
    token=os.environ.get("HF_TOKEN"),
)

print("loading encoder ...", flush=True)
from sentence_transformers import SentenceTransformer  # noqa: E402 - after the cheap fail-fast download

_model = SentenceTransformer(MODEL, trust_remote_code=True, device="cpu")
_table = lancedb.connect(_STATE / "data" / "lancedb").open_table(TABLE)
# the ATSes actually present in the index — feeds the filter dropdown, grows as ingestion does
_ATSES = sorted(
    {r["ats"] for r in _table.search().select(["ats"]).limit(1_000_000).to_list()}
)
# The Space redeploys on merge, but `first_seen` only appears on the next pipeline run (ADR-0031).
# Filtering on a column the table lacks errors every query, so the feature stays dark until the
# column lands and then switches itself on — no coordinated deploy.
_HAS_FIRST_SEEN = "first_seen" in _table.schema.names
# The Résumé feature is a private beta behind a shared password (ADR-0032). No secret set →
# the endpoint answers 503 and the panel is hidden — same dark-until-ready shape as first_seen.
_RESUME_PASSWORD = os.environ.get("RESUME_PASSWORD") or ""

# Role trends (ADR-0040). Same dark-until-ready shape as the two above: the ledger only exists
# after a pipeline run has written it, so an absent file hides the panel rather than erroring.
# Read once at startup — the Space restarts after every run, so it is never more than one run
# stale, and the file is a few dozen rows per run.
_NON_TECH = "non-tech"  # reserved diagnostic series — mirrors headstart.roles.NON_TECH


def _load_trends(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return [
            {
                "ts": r["ts"],
                "version": int(r["version"]),
                "family": r["family"],
                "band": r["band"],
                "count": int(r["count"]),
            }
            for r in csv.DictReader(fh)
        ]


def _family_labels(path: Path) -> dict[str, str]:
    """Display names, from the curated map synced beside this app (ADR-0040). The ledger
    stores slugs so a label can be reworded without breaking a series; this resolves them."""
    if not path.exists():
        return {}
    spec = json.loads(path.read_text(encoding="utf-8"))
    return {f["name"]: f.get("label", f["name"]) for f in spec["families"]}


_TRENDS = _load_trends(_STATE / "data" / "state" / "role_trends.csv")
_FAMILY_LABELS = _family_labels(Path(__file__).with_name("role_families.json"))
# A refit re-bases every series (ADR-0040), so never plot two versions on one axis: keep the
# newest only. Older rows stay in the ledger, they just aren't charted.
if _TRENDS:
    _live_version = max(r["version"] for r in _TRENDS)
    _TRENDS = [r for r in _TRENDS if r["version"] == _live_version]
# IPs that have presented the password once; in-memory, so the set empties whenever the Space
# sleeps or restarts and the password is simply asked for again. Only correct-password callers
# are ever added, so its size is bounded by people who actually hold the secret.
_RESUME_OK_IPS: set[str] = set()
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
print(
    f"ready: {_table.count_rows()} jobs across {len(_ATSES)} ATSes"
    + ("" if _HAS_FIRST_SEEN else " (no first_seen column yet — 'new since' hidden)")
    + ("" if _RESUME_PASSWORD else " (RESUME_PASSWORD unset — résumé search hidden)")
    + ("" if _ALERTS_ON else " (alerts secrets unset — email alerts hidden)")
    + ("" if _AUTH_ON else " (SECRET_KEY/GOOGLE_CLIENT_ID unset — sign-in wall off)"),
    flush=True,
)


def _store() -> Store:
    """A Subscriptions store per request — it holds no connection, only ids."""
    return Store(_SUBSCRIBERS_REPO, _SUBSCRIBERS_TOKEN)


app = Flask(__name__)
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
# from the caller's own cookie, so it can only tell you what you sent.
_PUBLIC_PATHS = {"/", "/auth/google", "/me", "/unsubscribe"}


@app.before_request
def _require_sign_in():
    if not _AUTH_ON or request.path in _PUBLIC_PATHS:
        return None
    if not session.get("email"):
        return jsonify({"error": "sign in first"}), 401
    return None


# Canonical employment-type filters mapped onto the messy per-ATS raw values
# ("fulltime", "Full-time", "fulltime_permanent", "Permanent / Full-Time", …).
_ETYPE_CLAUSES = {
    "full-time": "(lower(employment_type) LIKE '%full%'"
    " OR lower(employment_type) LIKE '%permanent%')",
    "part-time": "lower(employment_type) LIKE '%part%'",
    "contract": "(lower(employment_type) LIKE '%contract%'"
    " OR lower(employment_type) LIKE '%freelance%')",
    "internship": "lower(employment_type) LIKE '%intern%'",
}


def _int_arg(name: str) -> int | None:
    """An optional integer query param, absent when unset. Raises ``ValueError`` on garbage, which
    ``/search`` turns into a 400 — the same handling every other filter already relies on."""
    raw = request.args.get(name)
    return int(raw) if raw else None


def _like(term: str) -> str:
    """A user term made safe for a quoted LIKE pattern: quotes doubled, length-capped."""
    return term[:60].replace("'", "''").lower()


# TEMPORARY (2026-07-07) — INTENDED FOR REMOVAL. Darwinbox rows scraped before the
# candidatev2 URL fix carry the old `/ms/candidate/careers/jobs/{id}` link, which on v2
# tenants redirects to the careers home instead of the job. The stored data self-heals only
# as those postings turn over (sync leaves re-seen ids untouched — headstart.ingest.index_plan), so this
# rewrites the derivable URL at serve time as a stopgap. Remove once the darwinbox rows have
# healed (or once sync refreshes changed metadata for re-seen ids — the proper fix).
# Caveat: a legacy `new_careers=false` tenant's old-format URL would be wrongly rewritten,
# but none exist today (60/60 surveyed are v2).
_DARWINBOX_OLD = "/ms/candidate/careers/jobs/"
_DARWINBOX_NEW = "/ms/candidatev2/main/careers/jobDetails/"


def _canonical_url(ats: str | None, url: str | None) -> str | None:
    """Serve-time URL normalization; only darwinbox's stale links are rewritten (see above)."""
    if ats == "darwinbox" and url and _DARWINBOX_OLD in url:
        return url.replace(_DARWINBOX_OLD, _DARWINBOX_NEW, 1)
    return url


def _build_filter(
    *,
    remote: bool,
    max_years: int | None,
    ats: str | None,
    etype: str | None,
    india: str | None,
    location: str | None,
    company: str | None,
    has_salary: bool,
    posted_within: int | None,
    seen_within: int | None,
    first_seen_after: str | None = None,
) -> str | None:
    """The prod-table where-clause. `headstart.search.build_filter` carries the same three core
    filters (remote / employment_type / max_years); the rest — location, salary, recency — live only
    here, so the two have diverged and this one is the reference (ADR-0031)."""
    filters: list[str] = []
    if remote:
        filters.append("remote = true")
    if max_years is not None:
        filters.append(f"(min_years <= {int(max_years)} OR min_years IS NULL)")
    if ats in _ATSES:  # whitelist — never interpolated from free text
        filters.append(f"ats = '{ats}'")
    if etype in _ETYPE_CLAUSES:
        filters.append(_ETYPE_CLAUSES[etype])
    if india:
        clause = geo.where(india)  # canonical-place lookup — unknown values are ignored
        if clause:
            filters.append(clause)
    if location:
        filters.append(f"lower(location) LIKE '%{_like(location)}%'")
    if company:
        filters.append(f"lower(company) LIKE '%{_like(company)}%'")
    if has_salary:
        filters.append("salary IS NOT NULL")
    if posted_within is not None:
        # posted_at is a raw string; ISO-prefixed values (97%) compare correctly. The LIKE
        # shape guard excludes the rest — non-ISO forms like darwinbox's legacy
        # '21-Apr-2026' sort lexicographically ABOVE any ISO cutoff and would otherwise
        # leak into every window.
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=int(posted_within))
        ).strftime("%Y-%m-%d")
        filters.append(f"(posted_at >= '{cutoff}' AND posted_at LIKE '____-__-__%')")
    if seen_within is not None and _HAS_FIRST_SEEN:
        # In HOURS, not days: this window is meant to be shorter than one pipeline cycle. No shape
        # guard is needed here — unlike `posted_at`, we write `first_seen` ourselves, so it is
        # always ISO-8601 UTC. Rows predating the column are null, and `NULL >= '…'` is never true,
        # so they drop out on their own (ADR-0031).
        since = (
            datetime.now(timezone.utc) - timedelta(hours=int(seen_within))
        ).isoformat(timespec="seconds")
        filters.append(f"first_seen >= '{since}'")
    if first_seen_after and _HAS_FIRST_SEEN:
        # The alerts run's exact cutoff (ADR-0035), beside the UI's hour-granular window: a
        # Digest must carry precisely what appeared since that Subscription's Watermark, and
        # rounding up to whole hours would re-offer rows already mailed. Strictly `>`, so a
        # Watermark taken from a row's own `first_seen` cannot re-select that row.
        #
        # This is the one recency value that arrives as free text and lands in a where-clause,
        # so it is re-serialized from a parsed datetime rather than interpolated as given —
        # anything unparseable raises ValueError, which /search already answers as 400.
        moment = datetime.fromisoformat(first_seen_after).isoformat(timespec="seconds")
        filters.append(f"first_seen > '{moment}'")
    return " AND ".join(filters) if filters else None


def _search(query: str, where: str | None, k: int) -> list[dict]:
    qv = _model.encode([QUERY_PREFIX + query], normalize_embeddings=True)[0].astype(
        "float32"
    )
    search = _table.search(qv).metric("cosine")
    if where:
        search = search.where(where, prefilter=True)
    rows = search.limit(k).to_list()
    return [
        {
            "score": round(1 - r["_distance"], 3),
            "title": r["title"],
            "company": r["company"],
            "location": r.get("location"),
            "remote": r["remote"],
            "employment_type": r.get("employment_type"),
            "min_years": r.get("min_years"),
            "salary": r.get("salary"),
            "ats": r.get("ats"),
            "posted_at": r.get("posted_at"),
            "first_seen": r.get("first_seen"),
            "url": _canonical_url(
                r.get("ats"), r.get("url")
            ),  # temporary; see _canonical_url
        }
        for r in rows
    ]


@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    try:
        k = int(request.args.get("k") or 20)
        where = _build_filter(
            remote=request.args.get("remote") == "true",
            max_years=_int_arg("max_years"),
            ats=(request.args.get("ats") or "").strip() or None,
            etype=(request.args.get("etype") or "").strip() or None,
            india=(request.args.get("india") or "").strip().lower() or None,
            location=(request.args.get("location") or "").strip() or None,
            company=(request.args.get("company") or "").strip() or None,
            has_salary=request.args.get("has_salary") == "true",
            posted_within=_int_arg("posted_within"),
            seen_within=_int_arg("seen_within"),
            first_seen_after=(request.args.get("first_seen_after") or "").strip()
            or None,
        )
    except ValueError:
        return jsonify({"error": "invalid filter"}), 400
    return jsonify(_search(q, where, max(1, min(k, _MAX_K))))


def _client_ip() -> str:
    """The caller's IP as the platform's own proxy reports it — the *rightmost*
    ``X-Forwarded-For`` entry. Leftmost entries are client-supplied and trivially spoofed;
    the rightmost was appended by the hop in front of us. Still a gate, not authentication:
    IPs are shared (NAT) and forgeable in principle (ADR-0032)."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.rsplit(",", 1)[-1].strip()
    return request.remote_addr or "?"


@app.route("/resume-to-query", methods=["POST"])
def resume_to_query():
    """Paste a Résumé, get the one Query it implies — password-gated, once per IP (ADR-0032).

    The pasted text is used for this single LLM call and never stored or logged."""
    if not _RESUME_PASSWORD:
        return jsonify({"error": "résumé search is not configured"}), 503
    body = request.get_json(silent=True) or {}
    ip = _client_ip()
    if ip not in _RESUME_OK_IPS:
        if not hmac.compare_digest(str(body.get("password") or ""), _RESUME_PASSWORD):
            return jsonify({"error": "wrong or missing password"}), 401
        _RESUME_OK_IPS.add(ip)
    try:
        query = resume_query.query_for(str(body.get("text") or ""), ask=llm_router.ask)
    except resume_query.ResumeTooLong as exc:
        return jsonify({"error": str(exc)}), 413
    except resume_query.EmptyQuery as exc:
        return jsonify({"error": str(exc)}), 502
    except resume_query.ResumeError as exc:  # EmptyResume and any future refusal
        return jsonify({"error": str(exc)}), 400
    except llm_router.RouterUnavailable:
        # Detail stays in the container log's traceback-free world: the caller only needs
        # "temporarily off", and the reason may name internal hosts.
        return jsonify({"error": "résumé search is temporarily unavailable"}), 503
    return jsonify({"query": query})


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
    # Re-subscribing revises the record in place. Minting a new one would rotate the
    # unsubscribe token, which would 404 the link in every Digest already delivered.
    existing = store.get(subscription_id(email))
    sub = (
        existing.revised(query, search_filters)
        if existing
        else Subscription.create(email, query, search_filters)
    )
    store.put(sub)
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
    sub = store.get(sub_id)
    if (
        sub
        and sub.unsubscribe_token
        and hmac.compare_digest(sub.unsubscribe_token, token)
    ):
        store.remove(sub.id)
        return (
            "<p style='font-family:system-ui'>Unsubscribed. No more job digests "
            "will be sent to this address.</p>"
        )
    return "that unsubscribe link is not valid", 404


@app.route("/trends")
def trends():
    """Role-family counts over time (ADR-0040), or 503 until the ledger exists.

    Default: one series per family, each point the family's total across bands at that run.
    ``?family=<name>``: that family's series split by seniority band instead. The reserved
    ``non-tech`` family is never a chart series — it rides along as ``non_tech``, the
    tech-filter health number, so the page can show it as a caveat rather than a role."""
    if not _TRENDS:
        return jsonify(error="no trend data yet"), 503
    band = request.args.get("family")
    rows = [r for r in _TRENDS if r["family"] != _NON_TECH]
    if band:
        rows = [r for r in rows if r["family"] == band]
        key = "band"
    else:
        key = "family"

    series: dict[str, dict[str, int]] = {}
    for r in rows:  # sum over the other axis, so a family point is its total
        series.setdefault(r[key], {})
        at = series[r[key]]
        at[r["ts"]] = at.get(r["ts"], 0) + r["count"]
    stamps = sorted({r["ts"] for r in rows})
    out = [
        {
            "name": name,
            "label": _FAMILY_LABELS.get(name, name),
            # None (not 0) where a run has no row for this series: a gap is "not measured",
            # and plotting it as zero would invent a crash that never happened
            "points": [points.get(ts) for ts in stamps],
            "latest": points.get(stamps[-1]) if stamps else None,
        }
        for name, points in series.items()
    ]
    out.sort(key=lambda s: -(s["latest"] or 0))
    non_tech = {r["ts"]: r["count"] for r in _TRENDS if r["family"] == _NON_TECH}
    return jsonify(
        version=_TRENDS[-1]["version"],
        stamps=stamps,
        series=out,
        non_tech=[non_tech.get(ts) for ts in stamps],
        split_by=key,
    )


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


@app.route("/")
def index():
    if _AUTH_ON and not session.get("email"):
        return _SIGNIN_PAGE
    return _PAGE


# The page templates' single source is src/headstart/ui/templates/; deploy-space.yml syncs
# the directory next to this app the way it syncs geo.py and alerts/. In a repo checkout
# (tests, local runs) the synced copy doesn't exist, so fall back to the source location.
_TEMPLATE_DIR = Path(__file__).parent / "templates"
if not _TEMPLATE_DIR.exists():
    _TEMPLATE_DIR = Path(__file__).parents[2] / "src" / "headstart" / "ui" / "templates"
_TEMPLATE = (_TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")
# The sign-in door. Static per boot — the only thing it varies on is the client id.
_SIGNIN_PAGE = (
    (_TEMPLATE_DIR / "signin.html")
    .read_text(encoding="utf-8")
    .replace("__GOOGLE_CLIENT_ID__", _GOOGLE_CLIENT_ID)
)

_PAGE = (
    _TEMPLATE.replace("__NJOBS__", f"{_table.count_rows():,}")
    # Hidden until the pipeline adds the column, so the control can ship ahead of the data
    # (the `el('seen')` guard in go() keeps the query-string builder happy either way).
    .replace("__SINCE_HIDDEN__", "" if _HAS_FIRST_SEEN else ' style="display:none"')
    # Hidden until the RESUME_PASSWORD secret exists — dark feature, same shape as above.
    .replace("__RESUME_HIDDEN__", "" if _RESUME_PASSWORD else ' style="display:none"')
    # Same dark-until-ready shape: no alerts secrets, no panel and no sign-in button.
    .replace("__ALERTS_HIDDEN__", "" if _ALERTS_ON else ' style="display:none"')
    # Likewise for trends: hidden until a pipeline run has written the ledger (ADR-0040).
    .replace("__TRENDS_HIDDEN__", "" if _TRENDS else ' style="display:none"')
    .replace("__GOOGLE_CLIENT_ID__", _GOOGLE_CLIENT_ID)
    .replace(
        "__ATS_OPTIONS__",
        "".join(f'<option value="{a}">{a}</option>' for a in _ATSES),
    )
    .replace(
        "__INDIA_OPTIONS__",
        "".join(
            f'<option value="{c}">{c.title().replace("Ncr", "NCR")}</option>'
            for c in geo.DROPDOWN
        ),
    )
)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860)
