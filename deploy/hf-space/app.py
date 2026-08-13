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
import profile_extract  # synced from src/headstart/profile_extract.py by deploy-space.yml
import search  # the shared search path — synced from src/headstart/search.py (ADR-0042)

# alerts/ is a package, synced from src/headstart/alerts/ by deploy-space.yml (ADR-0035).
# Only these members are imported here: alerts/__init__.py is empty on purpose, so the
# Space never loads the Digest or Resend modules, whose dependencies it does not install.
from alerts import access, identity
from alerts.store import (
    MAX_PARSES,
    MAX_SAVED,
    MAX_SETS,
    Profile,
    SavedJob,
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
    f"ready: {_table.count_rows()} jobs across {len(_searcher.atses)} ATSes"
    + (
        ""
        if _searcher.has_first_seen
        else " (no first_seen column yet — 'new since' hidden)"
    )
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


@app.route("/search")
def search_jobs():
    """A thin adapter over the shared search path — parse/filter/rank live in JobSearch."""
    try:
        return jsonify(_searcher.run(request.args))
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
    account = subscription_id(email)
    if store.get_profile(account):
        store.remove_profile(account)
    return jsonify({"ok": True})


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
    _project_subscription(store, email, query, search_filters)
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
    store: Store, email: str, query: str, search_filters: dict
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
    store.put(sub)


@app.route("/sets")
def list_sets():
    gate = _account_gate()
    if not gate:
        return jsonify({"error": "saved sets are not configured"}), 503
    email, store = gate
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
            _project_subscription(store, email, updated.query, updated.search_filters)
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
        _project_subscription(store, email, current.query, current.search_filters)
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
        alerts_on=_ALERTS_ON,
        sets_on=_SETS_ON,
        saved_on=_SETS_ON,  # same prerequisites — see the _SETS_ON comment
        profile_on=_SETS_ON,  # likewise (the parse button 503s on its own if the router is down)
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860)
