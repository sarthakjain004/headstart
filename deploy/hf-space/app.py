"""HeadStart semantic search — HF Space app (ADR-0020).

Pulls the LanceDB ``jobs`` table from the private HF dataset at startup (HF_TOKEN Space
secret), loads the nomic encoder (baked into the image), and serves the shared UI — the
templates and static files synced from ``src/headstart/ui`` — over the shared search path,
``search.JobSearch``, synced from ``src/headstart/search.py`` (ADR-0042). The local dev
server (``scripts/ui/serve.py``) is a thin adapter over the same two modules, so nothing
here is duplicated there any more.
"""

from __future__ import annotations

import csv
import hmac
import json
import os
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import geo  # India gazetteer — synced from src/headstart/geo.py by deploy-space.yml
import lancedb
import llm_router  # synced from src/headstart/llm_router.py by deploy-space.yml (ADR-0032)
import resume_query  # synced from src/headstart/resume_query.py by deploy-space.yml
import search  # the shared search path — synced from src/headstart/search.py (ADR-0042)

# alerts/ is a package, synced from src/headstart/alerts/ by deploy-space.yml (ADR-0035).
# Only these members are imported here: alerts/__init__.py is empty on purpose, so the
# Space never loads the Digest or Resend modules, whose dependencies it does not install.
from alerts import access, identity
from alerts.store import (
    MAX_SETS,
    SavedSet,
    Store,
    Subscription,
    subscription_id,
)
from flask import Flask, jsonify, render_template, request, session
from huggingface_hub import snapshot_download

DATASET = os.environ.get("HF_DATASET", "imPoseidon/headstart-index")
_STATE = Path("/app/state")

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

_model = SentenceTransformer(search.MODEL, trust_remote_code=True, device="cpu")
_table = lancedb.connect(_STATE / "data" / "lancedb").open_table(search.PROD_TABLE)
# The whole query path — parse, whitelist, rank, project — lives behind this one object
# (search.JobSearch); its startup scan supplies the ATS dropdown and the first_seen flag.
_searcher = search.JobSearch(_model, _table)
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
# Saved sets (ADR-0043) need both an identity (the wall) and somewhere to keep per-Account
# records (the Subscriptions dataset) — either missing keeps the Matches tab a "soon" item.
_SETS_ON = _AUTH_ON and bool(_SUBSCRIBERS_REPO and _SUBSCRIBERS_TOKEN)
print(
    f"ready: {_table.count_rows()} jobs across {len(_searcher.atses)} ATSes"
    + (
        ""
        if _searcher.has_first_seen
        else " (no first_seen column yet — 'new since' hidden)"
    )
    + ("" if _RESUME_PASSWORD else " (RESUME_PASSWORD unset — résumé search hidden)")
    + ("" if _ALERTS_ON else " (alerts secrets unset — email alerts hidden)")
    + ("" if _AUTH_ON else " (SECRET_KEY/GOOGLE_CLIENT_ID unset — sign-in wall off)"),
    flush=True,
)


def _store() -> Store:
    """A Subscriptions store per request — it holds no connection, only ids."""
    return Store(_SUBSCRIBERS_REPO, _SUBSCRIBERS_TOKEN)


# The UI's single source is src/headstart/ui (templates + static); deploy-space.yml syncs
# both next to this app. In a repo checkout (tests, local runs) the synced copies don't
# exist, so fall back to the source location.
_UI = Path(__file__).parent
if not (_UI / "templates").exists():
    _UI = Path(__file__).parents[2] / "src" / "headstart" / "ui"
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
# from the caller's own cookie, so it can only tell you what you sent.
_PUBLIC_PATHS = {"/", "/auth/google", "/me", "/unsubscribe"}


@app.before_request
def _require_sign_in():
    if not _AUTH_ON or request.path in _PUBLIC_PATHS:
        return None
    if not session.get("email"):
        return jsonify({"error": "sign in first"}), 401
    return None


@app.route("/search")
def search_jobs():
    """A thin adapter over the shared search path — parse/filter/rank live in JobSearch."""
    try:
        return jsonify(_searcher.run(request.args))
    except ValueError:
        return jsonify({"error": "invalid filter"}), 400


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


def _sets_gate() -> tuple[str, Store] | None:
    """The signed-in address and a store, or None when sets can't function.

    Reached only with a session when the wall is on (before_request), so None here means
    the feature is unconfigured — the caller answers 503, mirroring the other dark
    features."""
    email = session.get("email") if _AUTH_ON else None
    if not (_SETS_ON and email):
        return None
    return email, _store()


def _project_subscription(store: Store, email: str, saved: SavedSet) -> None:
    """Write the Subscription as the delivery projection of the emailing set (ADR-0043).

    Revising keeps the Watermark and unsubscribe token (mailed links must survive edits);
    creating starts the Watermark now, so nobody is mailed the backlog."""
    existing = store.get(subscription_id(email))
    sub = (
        existing.revised(saved.query, saved.search_filters)
        if existing
        else Subscription.create(email, saved.query, saved.search_filters)
    )
    store.put(sub)


@app.route("/sets")
def list_sets():
    gate = _sets_gate()
    if not gate:
        return jsonify({"error": "saved sets are not configured"}), 503
    email, store = gate
    return jsonify([s.to_dict() for s in store.sets_for(subscription_id(email))])


@app.route("/sets", methods=["POST"])
def save_set():
    """Create a Saved set, or update one when the body names an ``id`` (ADR-0043).

    Updating the emailing set re-projects the Subscription in the same request, so the
    delivered Digest can never drift from what the tab shows."""
    gate = _sets_gate()
    if not gate:
        return jsonify({"error": "saved sets are not configured"}), 503
    email, store = gate
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
            _project_subscription(store, email, updated)
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
    gate = _sets_gate()
    if not gate:
        return jsonify({"error": "saved sets are not configured"}), 503
    email, store = gate
    account = subscription_id(email)
    current = store.get_set(account, set_id)
    if not current:
        return jsonify({"error": "no such set"}), 404
    store.remove_set(account, set_id)
    if current.emails:
        store.remove(subscription_id(email))
    return jsonify({"ok": True})


@app.route("/sets/<set_id>/email", methods=["POST"])
def set_email(set_id: str):
    """Turn email on or off for one set — on moves it here from any other set.

    Delivery stays invite-only (ADR-0035): turning ON checks the allowlist; OFF removes
    the Subscription record, so the alerts run simply stops seeing this person."""
    gate = _sets_gate()
    if not gate:
        return jsonify({"error": "saved sets are not configured"}), 503
    email, store = gate
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
        _project_subscription(store, email, current)
    else:
        current = replace(current, emails=False)
        store.put_set(current)
        store.remove(subscription_id(email))
    return jsonify(current.to_dict())


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
        return render_template("signin.html", google_client_id=_GOOGLE_CLIENT_ID)
    return render_template(
        "base.html",
        # the one blob the static JS reads (window.CFG); everything else is template-side
        cfg={"google_client_id": _GOOGLE_CLIENT_ID},
        njobs=f"{_table.count_rows():,}",
        atses=_searcher.atses,
        india_opts=geo.dropdown_options(),
        has_first_seen=_searcher.has_first_seen,
        trends_on=bool(_TRENDS),
        resume_on=bool(_RESUME_PASSWORD),
        alerts_on=_ALERTS_ON,
        sets_on=_SETS_ON,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860)
