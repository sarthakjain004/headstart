"""HeadStart semantic search — HF Space app (ADR-0020).

Self-contained twin of ``scripts/ui/serve.py``: pulls the LanceDB ``jobs`` table from the
private HF dataset at startup (HF_TOKEN Space secret), loads the nomic encoder (baked into the
image), and serves the same filter-then-rank search page. The search conventions (model id,
task prefixes, filter clause) mirror ``headstart.search`` — keep them in lockstep.
"""

from __future__ import annotations

import hmac
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
from flask import Flask, jsonify, request
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
    allow_patterns=["data/lancedb/*"],
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
print(
    f"ready: {_table.count_rows()} jobs across {len(_ATSES)} ATSes"
    + ("" if _HAS_FIRST_SEEN else " (no first_seen column yet — 'new since' hidden)")
    + ("" if _RESUME_PASSWORD else " (RESUME_PASSWORD unset — résumé search hidden)")
    + ("" if _ALERTS_ON else " (alerts secrets unset — email alerts hidden)"),
    flush=True,
)


def _store() -> Store:
    """A Subscriptions store per request — it holds no connection, only ids."""
    return Store(_SUBSCRIBERS_REPO, _SUBSCRIBERS_TOKEN)


app = Flask(__name__)

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


@app.route("/")
def index():
    return _PAGE


_TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>HeadStart — semantic job search</title>
<style>
  /* ---- tokens. Every colour and size below is named here and used through the token,
         so the light theme is a token swap rather than a second stylesheet. ---- */
  :root{
    --ground:#0E1220; --raise:#161B2C; --raise-2:#1D2337; --rule:#252C42; --rule-2:#333B57;
    --ink:#EEF1F8; --ink-2:#A8B0C6; --ink-3:#6F7896;
    --aqua:#12D6B4; --aqua-deep:#0BA88C; --violet:#8B5CF6; --amber:#F59E0B; --lime:#A3E635;
    --coral:#FB7185;
    --glow:rgba(18,214,180,.22); --veil:rgba(8,11,20,.72);
    --r-xl:20px; --r-lg:14px; --r-md:10px; --r-pill:999px;
    --shadow:0 18px 44px rgba(0,0,0,.42);
    --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
    --serif:ui-serif,"Iowan Old Style",Georgia,serif;
    --step:.18s cubic-bezier(.2,.7,.3,1);
  }
  @media (prefers-color-scheme: light){
    :root{
      --ground:#F6F7FB; --raise:#FFFFFF; --raise-2:#F1F3F9; --rule:#E2E6F0; --rule-2:#CDD4E4;
      --ink:#131726; --ink-2:#4C5570; --ink-3:#7A83A0;
      --aqua:#00A98D; --aqua-deep:#00806B; --violet:#6D46E8; --amber:#B4740A; --lime:#5C9B12;
      --coral:#DC4B62;
      --glow:rgba(0,169,141,.18); --veil:rgba(255,255,255,.78);
      --shadow:0 14px 34px rgba(20,28,54,.10);
    }
  }
  /* the toggle wins over the OS preference, in both directions */
  :root[data-theme="light"]{
    --ground:#F6F7FB; --raise:#FFFFFF; --raise-2:#F1F3F9; --rule:#E2E6F0; --rule-2:#CDD4E4;
    --ink:#131726; --ink-2:#4C5570; --ink-3:#7A83A0;
    --aqua:#00A98D; --aqua-deep:#00806B; --violet:#6D46E8; --amber:#B4740A; --lime:#5C9B12;
    --coral:#DC4B62; --glow:rgba(0,169,141,.18); --veil:rgba(255,255,255,.78);
    --shadow:0 14px 34px rgba(20,28,54,.10);
  }
  :root[data-theme="dark"]{
    --ground:#0E1220; --raise:#161B2C; --raise-2:#1D2337; --rule:#252C42; --rule-2:#333B57;
    --ink:#EEF1F8; --ink-2:#A8B0C6; --ink-3:#6F7896;
    --aqua:#12D6B4; --aqua-deep:#0BA88C; --violet:#8B5CF6; --amber:#F59E0B; --lime:#A3E635;
    --coral:#FB7185; --glow:rgba(18,214,180,.22); --veil:rgba(8,11,20,.72);
    --shadow:0 18px 44px rgba(0,0,0,.42);
  }

  *{ box-sizing:border-box; }
  html,body{ margin:0; }
  body{
    background:var(--ground); color:var(--ink); font:15px/1.55 var(--sans);
    -webkit-font-smoothing:antialiased;
    transition:background .3s ease, color .3s ease;
  }
  /* slow ambient wash behind the header — the only purely decorative motion on the page */
  .aura{ position:fixed; inset:-30% -10% auto -10%; height:70vh; pointer-events:none; z-index:0;
    background:
      radial-gradient(46% 52% at 22% 12%, var(--glow), transparent 70%),
      radial-gradient(38% 46% at 78% 4%, rgba(139,92,246,.16), transparent 72%);
    filter:blur(6px); animation:drift 22s ease-in-out infinite alternate; }
  @keyframes drift{ from{ transform:translate3d(-2%,-1%,0) scale(1); }
                    to{ transform:translate3d(3%,2%,0) scale(1.08); } }

  .shell{ position:relative; z-index:1; max-width:1180px; margin:0 auto; padding:26px 22px 80px; }

  /* ---- masthead ---- */
  .top{ display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:30px; }
  .brand{ display:flex; align-items:center; gap:11px; }
  .mark{ width:30px; height:30px; border-radius:9px; flex:none; position:relative;
    background:linear-gradient(135deg,var(--aqua),var(--violet));
    box-shadow:0 0 0 1px rgba(255,255,255,.10) inset, 0 8px 20px var(--glow); }
  .mark::after{ content:""; position:absolute; inset:9px 9px auto auto; width:7px; height:7px;
    border-radius:50%; background:var(--ground); }
  .brand h1{ font:600 21px/1 var(--serif); letter-spacing:-.01em; margin:0; }
  .brand h1 em{ font-style:normal;
    background:linear-gradient(100deg,var(--aqua),var(--violet)); -webkit-background-clip:text;
    background-clip:text; color:transparent; }
  .top-right{ display:flex; align-items:center; gap:10px; }
  .count{ font:500 12px/1 var(--mono); font-variant-numeric:tabular-nums; color:var(--ink-2);
    background:var(--raise); border:1px solid var(--rule); padding:8px 12px; border-radius:var(--r-pill); }
  .count b{ color:var(--aqua); }
  .icon-btn{ width:36px; height:36px; border-radius:var(--r-md); border:1px solid var(--rule);
    background:var(--raise); color:var(--ink-2); cursor:pointer; font-size:15px; line-height:1;
    transition:transform var(--step), border-color var(--step), color var(--step); }
  .icon-btn:hover{ transform:translateY(-1px); border-color:var(--aqua); color:var(--aqua); }

  /* ---- search ---- */
  .hero{ margin-bottom:22px; }
  .searchbar{ display:flex; gap:10px; }
  .field{ position:relative; flex:1; }
  .field input{ width:100%; padding:17px 19px; border-radius:var(--r-xl); font:inherit; font-size:16px;
    background:var(--raise); color:var(--ink); border:1px solid var(--rule-2); outline:none;
    transition:border-color var(--step), box-shadow var(--step); }
  .field input::placeholder{ color:var(--ink-3); }
  .field input:focus{ border-color:var(--aqua); box-shadow:0 0 0 4px var(--glow); }
  .go{ padding:0 30px; border:0; border-radius:var(--r-xl); cursor:pointer; color:#04121A;
    font:700 15px/1 var(--sans); letter-spacing:.01em;
    background:linear-gradient(120deg,var(--aqua),var(--lime));
    transition:transform var(--step), box-shadow var(--step), filter var(--step); }
  .go:hover{ transform:translateY(-2px); box-shadow:0 12px 30px var(--glow); filter:saturate(1.15); }
  .go:active{ transform:translateY(0); }
  .hint{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-top:12px;
    color:var(--ink-3); font-size:12.5px; }
  .chip{ border:1px dashed var(--rule-2); background:transparent; color:var(--ink-2);
    padding:6px 12px; border-radius:var(--r-pill); font:inherit; font-size:12.5px; cursor:pointer;
    transition:border-color var(--step), color var(--step), transform var(--step); }
  .chip:hover{ border-color:var(--aqua); color:var(--aqua); transform:translateY(-1px); }

  /* ---- the two AI features, promoted out of footnotes ---- */
  .features{ display:grid; grid-template-columns:1fr 1fr; gap:12px; margin:18px 0 26px; }
  @media (max-width:860px){ .features{ grid-template-columns:1fr; } }
  .feature{ background:var(--raise); border:1px solid var(--rule); border-radius:var(--r-lg);
    overflow:hidden; transition:border-color var(--step); }
  .feature[open]{ border-color:var(--rule-2); }
  .feature summary{ list-style:none; cursor:pointer; padding:14px 16px; display:flex; gap:11px;
    align-items:center; user-select:none; }
  .feature summary::-webkit-details-marker{ display:none; }
  .feature .dot{ width:8px; height:8px; border-radius:50%; background:var(--violet); flex:none;
    box-shadow:0 0 12px var(--violet); }
  .feature .ttl{ font-weight:600; font-size:14px; }
  .feature .sub{ color:var(--ink-3); font-size:12.5px; margin-left:auto; }
  .feature .body{ padding:0 16px 16px; }
  .feature p{ color:var(--ink-2); font-size:13px; margin:0 0 12px; }
  textarea{ width:100%; padding:12px 14px; resize:vertical; font:inherit; font-size:13.5px;
    background:var(--raise-2); color:var(--ink); border:1px solid var(--rule);
    border-radius:var(--r-md); outline:none; transition:border-color var(--step); }
  textarea:focus{ border-color:var(--violet); }
  .row{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:10px; }
  .row input{ padding:10px 12px; background:var(--raise-2); color:var(--ink);
    border:1px solid var(--rule); border-radius:var(--r-md); font:inherit; width:170px; outline:none; }
  .row input:focus{ border-color:var(--violet); }
  .ghost{ padding:10px 18px; border-radius:var(--r-md); cursor:pointer; font:600 13.5px var(--sans);
    background:var(--raise-2); color:var(--ink); border:1px solid var(--rule-2);
    transition:border-color var(--step), transform var(--step); }
  .ghost:hover{ border-color:var(--violet); transform:translateY(-1px); }
  .ghost:disabled{ opacity:.55; cursor:wait; transform:none; }
  .note{ color:var(--ink-3); font-size:12.5px; }

  /* ---- layout: sticky filter rail + results ---- */
  .grid{ display:grid; grid-template-columns:260px 1fr; gap:22px; align-items:start; }
  @media (max-width:900px){ .grid{ grid-template-columns:1fr; } }
  .rail{ position:sticky; top:20px; background:var(--raise); border:1px solid var(--rule);
    border-radius:var(--r-lg); padding:16px; }
  @media (max-width:900px){ .rail{ position:static; display:none; } .rail.open{ display:block; } }
  .rail h2{ font:600 11px/1 var(--sans); letter-spacing:.12em; text-transform:uppercase;
    color:var(--ink-3); margin:0 0 10px; }
  .set + .set{ margin-top:18px; padding-top:16px; border-top:1px solid var(--rule); }
  .f{ display:flex; flex-direction:column; gap:5px; margin-bottom:11px; }
  .f:last-child{ margin-bottom:0; }
  .f label{ font-size:12px; color:var(--ink-2); font-weight:500; }
  .f select,.f input{ width:100%; padding:9px 11px; border-radius:var(--r-md); font:inherit;
    font-size:13px; background:var(--raise-2); color:var(--ink); border:1px solid var(--rule);
    outline:none; transition:border-color var(--step); }
  .f select:focus,.f input:focus{ border-color:var(--aqua); }
  .sw{ display:flex; align-items:center; gap:9px; color:var(--ink-2); font-size:13px;
    cursor:pointer; user-select:none; margin-bottom:9px; }
  .sw input{ appearance:none; width:36px; height:21px; border-radius:var(--r-pill); flex:none;
    background:var(--rule-2); position:relative; cursor:pointer; transition:background var(--step); margin:0; }
  .sw input::after{ content:""; position:absolute; top:3px; left:3px; width:15px; height:15px;
    border-radius:50%; background:#fff; transition:transform var(--step); }
  .sw input:checked{ background:linear-gradient(120deg,var(--aqua),var(--lime)); }
  .sw input:checked::after{ transform:translateX(15px); }
  .clear{ width:100%; margin-top:14px; padding:9px; border-radius:var(--r-md); cursor:pointer;
    background:transparent; border:1px solid var(--rule); color:var(--ink-3); font:inherit;
    font-size:12.5px; transition:border-color var(--step), color var(--step); }
  .clear:hover{ border-color:var(--coral); color:var(--coral); }

  .toolbar{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:14px; }
  .toolbar .n{ font:500 13px var(--mono); font-variant-numeric:tabular-nums; color:var(--ink-2); }
  .toolbar .spacer{ flex:1; }
  .toolbar select{ padding:8px 11px; border-radius:var(--r-md); background:var(--raise);
    color:var(--ink); border:1px solid var(--rule); font:inherit; font-size:12.5px; outline:none; }
  .filters-btn{ display:none; }
  @media (max-width:900px){ .filters-btn{ display:inline-block; } }
  .active{ display:flex; gap:7px; flex-wrap:wrap; margin-bottom:14px; }
  .pill{ display:inline-flex; align-items:center; gap:7px; font-size:12px; padding:5px 10px;
    border-radius:var(--r-pill); background:var(--raise-2); border:1px solid var(--rule-2);
    color:var(--ink-2); animation:pop .22s cubic-bezier(.2,.9,.3,1.3) both; }
  .pill b{ color:var(--ink); font-weight:600; }
  .pill button{ background:none; border:0; color:var(--ink-3); cursor:pointer; font-size:14px;
    line-height:1; padding:0; }
  .pill button:hover{ color:var(--coral); }
  @keyframes pop{ from{ opacity:0; transform:scale(.86); } to{ opacity:1; transform:none; } }

  /* ---- result cards ---- */
  .card{ position:relative; background:var(--raise); border:1px solid var(--rule);
    border-radius:var(--r-lg); padding:16px 18px 15px; margin-bottom:11px; overflow:hidden;
    animation:rise .42s cubic-bezier(.2,.7,.3,1) both;
    transition:border-color var(--step), transform var(--step), box-shadow var(--step); }
  .card:hover{ transform:translateY(-2px); border-color:var(--rule-2); box-shadow:var(--shadow); }
  .card::before{ content:""; position:absolute; left:0; top:0; bottom:0; width:3px;
    background:var(--tone,var(--aqua)); opacity:.85; }
  @keyframes rise{ from{ opacity:0; transform:translateY(10px); } to{ opacity:1; transform:none; } }
  .card .hd{ display:flex; align-items:flex-start; gap:14px; }
  .card a.title{ color:var(--ink); text-decoration:none; font-weight:600; font-size:16px;
    letter-spacing:-.01em; line-height:1.35; }
  .card a.title:hover{ color:var(--aqua); }
  .card .org{ color:var(--ink-2); font-size:13px; margin-top:3px; }
  .card .org span{ color:var(--ink-3); }
  /* match strength: a bar plus the number, so it reads at a glance and survives colour-blindness */
  .match{ flex:none; width:74px; text-align:right; }
  .match .v{ font:600 13px var(--mono); font-variant-numeric:tabular-nums; color:var(--tone,var(--aqua)); }
  .match .track{ height:4px; border-radius:var(--r-pill); background:var(--rule); margin-top:5px;
    overflow:hidden; }
  .match .fill{ height:100%; border-radius:var(--r-pill); background:var(--tone,var(--aqua));
    width:0; animation:fill .7s .1s cubic-bezier(.2,.7,.3,1) forwards; }
  @keyframes fill{ to{ width:var(--pct,50%); } }
  .card .tags{ display:flex; gap:6px; flex-wrap:wrap; margin-top:11px; }
  .tag{ font:500 11.5px var(--sans); padding:4px 10px; border-radius:var(--r-pill);
    background:var(--raise-2); border:1px solid var(--rule); color:var(--ink-2); }
  .tag.mono{ font-family:var(--mono); font-variant-numeric:tabular-nums; }
  .tag.new{ background:color-mix(in srgb, var(--amber) 16%, transparent); color:var(--amber);
    border-color:color-mix(in srgb, var(--amber) 34%, transparent); font-weight:600; }
  .tag.pay{ background:color-mix(in srgb, var(--lime) 15%, transparent); color:var(--lime);
    border-color:color-mix(in srgb, var(--lime) 32%, transparent); }
  .tag.rem{ background:color-mix(in srgb, var(--violet) 16%, transparent); color:var(--violet);
    border-color:color-mix(in srgb, var(--violet) 34%, transparent); }

  .empty{ text-align:center; padding:56px 24px; color:var(--ink-2); }
  .empty .big{ font:600 17px var(--serif); color:var(--ink); margin-bottom:8px; }
  .skel{ background:var(--raise); border:1px solid var(--rule); border-radius:var(--r-lg);
    padding:18px; margin-bottom:11px; }
  .shim{ height:13px; border-radius:7px; background:linear-gradient(90deg,
    var(--raise-2) 25%, var(--rule) 45%, var(--raise-2) 65%); background-size:200% 100%;
    animation:shim 1.2s linear infinite; }
  @keyframes shim{ from{ background-position:200% 0; } to{ background-position:-200% 0; } }
  .foot{ text-align:center; color:var(--ink-3); font-size:12px; margin-top:46px; }
  .foot code{ font-family:var(--mono); color:var(--ink-2); }

  :focus-visible{ outline:2px solid var(--aqua); outline-offset:2px; border-radius:4px; }
  @media (prefers-reduced-motion: reduce){
    *,*::before,*::after{ animation-duration:.001ms !important; animation-iteration-count:1 !important;
      transition-duration:.001ms !important; }
    .match .fill{ width:var(--pct,50%); }
  }
</style></head><body>
<div class="aura"></div>
<div class="shell">

  <header class="top">
    <div class="brand">
      <div class="mark"></div>
      <h1>Head<em>Start</em></h1>
    </div>
    <div class="top-right">
      <span class="count"><b>__NJOBS__</b> jobs indexed</span>
      <button class="icon-btn" id="theme" onclick="flipTheme()" aria-label="Switch light or dark theme" title="Switch theme">◐</button>
    </div>
  </header>

  <section class="hero">
    <div class="searchbar">
      <div class="field">
        <input id="q" type="text" autofocus aria-label="Describe the role you want"
               placeholder="Describe the role you want — e.g. backend engineer at a climate startup">
      </div>
      <button class="go" onclick="go()">Search</button>
    </div>
    <div class="hint">
      <span>Try:</span>
      <button class="chip" onclick="tryIt(this)">backend engineer at a climate startup</button>
      <button class="chip" onclick="tryIt(this)">ML infrastructure, PyTorch</button>
      <button class="chip" onclick="tryIt(this)">frontend engineer, design systems</button>
    </div>
  </section>

  <div class="features">
    <details class="feature"__RESUME_HIDDEN__>
      <summary><span class="dot"></span><span class="ttl">Write my search from my résumé</span><span class="sub">AI</span></summary>
      <div class="body">
        <p>Paste your résumé and we'll turn it into a search you can edit. Used for this one
           request — never stored.</p>
        <textarea id="resume" rows="6" placeholder="Paste the text of your résumé"></textarea>
        <div class="row">
          <input id="rpass" type="password" placeholder="Beta password" autocomplete="off">
          <button class="ghost" onclick="readResume()" id="rbtn">Write my search</button>
          <span class="note" id="rmsg" role="status"></span>
        </div>
      </div>
    </details>

    <details class="feature"__ALERTS_HIDDEN__>
      <summary><span class="dot"></span><span class="ttl">Email me new matches</span><span class="sub">invite only</span></summary>
      <div class="body">
        <p>We'll email you the best 30 new jobs matching the search and filters you've set here,
           every time the index refreshes — with a spreadsheet attached. Every email has an
           unsubscribe link.</p>
        <div class="row">
          <div id="gsignin"></div>
          <span class="note" id="amsg" role="status"></span>
        </div>
      </div>
    </details>
  </div>

  <div class="grid">
    <aside class="rail" id="rail">
      <div class="set">
        <h2>Role</h2>
        <div class="f"><label for="etype">Employment type</label>
          <select id="etype"><option value="">Any</option>
            <option value="full-time">Full-time</option><option value="part-time">Part-time</option>
            <option value="contract">Contract</option><option value="internship">Internship</option>
          </select></div>
        <div class="f"><label for="maxyears">Max experience wanted (years)</label>
          <input id="maxyears" type="number" min="0" placeholder="Any"></div>
        <label class="sw"><input type="checkbox" id="remote"> Remote only</label>
        <label class="sw"><input type="checkbox" id="hassalary"> Shows salary</label>
      </div>

      <div class="set">
        <h2>Where</h2>
        <div class="f"><label for="india">India</label>
          <select id="india"><option value="">Anywhere</option>
            <option value="india">All India</option>__INDIA_OPTIONS__</select></div>
        <div class="f"><label for="location">Location</label>
          <input id="location" type="text" placeholder="e.g. Berlin"></div>
        <div class="f"><label for="company">Company</label>
          <input id="company" type="text" placeholder="e.g. Binance"></div>
      </div>

      <div class="set">
        <h2>Freshness</h2>
        <div class="f"><label for="posted">Posted by the employer</label>
          <select id="posted"><option value="">Any time</option>
            <option value="1">Last 24 hours</option><option value="7">Last 7 days</option>
            <option value="30">Last 30 days</option><option value="90">Last 90 days</option>
          </select></div>
        <div class="f"__SINCE_HIDDEN__><label for="seen">First seen by HeadStart</label>
          <select id="seen"><option value="">Any time</option>
            <option value="2">Last 2 hours</option><option value="24">Last 24 hours</option>
            <option value="168">Last 7 days</option>
          </select></div>
        <div class="f"><label for="ats">Job board</label>
          <select id="ats"><option value="">Any</option>__ATS_OPTIONS__</select></div>
      </div>

      <button class="clear" onclick="clearAll()">Clear all filters</button>
    </aside>

    <main>
      <div class="toolbar">
        <button class="ghost filters-btn" onclick="toggleRail()">Filters</button>
        <span class="n" id="n"></span>
        <span class="spacer"></span>
        <label class="note" for="sort">Sort</label>
        <select id="sort" onchange="go()">
          <option value="rel">Best match</option><option value="new">Newest first</option>
        </select>
        <label class="note" for="k">Show</label>
        <select id="k" onchange="go()"><option>10</option><option selected>20</option><option>50</option></select>
      </div>
      <div class="active" id="active"></div>
      <div id="results" aria-live="polite"></div>
    </main>
  </div>

  <div class="foot">Ranked by <code>nomic</code> embeddings over a filtered LanceDB index · English-language tech roles, read straight from company boards</div>
</div>
<script>
const el = s => document.getElementById(s);
// Job text is scraped third-party content — escape before it reaches innerHTML, and only
// allow http(s) hrefs (no javascript: URLs).
const esc = s => (s==null?'':String(s)).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl = u => { const l=(u||'').toLowerCase(); return (l.startsWith('http://')||l.startsWith('https://'))? u : '#'; };
el('q').addEventListener('keydown', e => { if (e.key === 'Enter') go(); });

function flipTheme(){
  const now = document.documentElement.getAttribute('data-theme')
    || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
  document.documentElement.setAttribute('data-theme', now === 'dark' ? 'light' : 'dark');
}
function tryIt(btn){ el('q').value = btn.textContent.trim(); go(); }
function toggleRail(){ el('rail').classList.toggle('open'); }

const age = d => {
  const t = Date.parse(d || ''); if (isNaN(t)) return '';
  const days = Math.floor((Date.now() - t) / 86400000);
  if (days < 0) return '';
  if (days === 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return days + 'd ago';
  if (days < 365) return Math.floor(days/30) + 'mo ago';
  return Math.floor(days/365) + 'y ago';
};
// Indexed inside the window the user asked for, so the badge always means "newer than your
// filter" — defaulting to 24h when no window is set. `first_seen` is ours, so it always parses.
const isNew = s => {
  const t = Date.parse(s || ''); if (isNaN(t)) return false;
  const hours = Number((el('seen') && el('seen').value) || 24);
  return Date.now() - t < hours * 3600000;
};
// Match strength gets a colour as well as a number: weak matches read violet, strong ones
// read lime, so a scan down the column shows where the good results stop.
const tone = s => s >= .75 ? 'var(--lime)' : s >= .6 ? 'var(--aqua)' : s >= .45 ? 'var(--amber)' : 'var(--violet)';
const skeleton = () =>
  '<div class="skel"><div class="shim" style="width:52%"></div>' +
  '<div class="shim" style="width:30%; margin-top:10px"></div>' +
  '<div class="shim" style="width:68%; margin-top:14px"></div></div>';

// The filters as a plain object, read once so the search and the alert subscription can
// never disagree about what the user asked for. The server whitelists these again.
function currentFilters(){
  const f = {};
  if (el('remote').checked) f.remote = 'true';
  if (el('hassalary').checked) f.has_salary = 'true';
  if (el('maxyears').value) f.max_years = el('maxyears').value;
  if (el('ats').value) f.ats = el('ats').value;
  if (el('etype').value) f.etype = el('etype').value;
  if (el('india').value) f.india = el('india').value;
  if (el('location').value.trim()) f.location = el('location').value.trim();
  if (el('company').value.trim()) f.company = el('company').value.trim();
  if (el('posted').value) f.posted_within = el('posted').value;
  if (el('seen') && el('seen').value) f.seen_within = el('seen').value;
  return f;
}
const LABELS = { remote:'Remote', has_salary:'Shows salary', max_years:'Max years',
  ats:'Board', etype:'Type', india:'India', location:'Location', company:'Company',
  posted_within:'Posted ≤', seen_within:'First seen ≤' };
const CONTROL = { remote:'remote', has_salary:'hassalary', max_years:'maxyears', ats:'ats',
  etype:'etype', india:'india', location:'location', company:'company',
  posted_within:'posted', seen_within:'seen' };
function drawActive(){
  const f = currentFilters(), box = el('active');
  box.innerHTML = Object.entries(f).map(([k,v]) =>
    `<span class="pill"><b>${esc(LABELS[k]||k)}</b> ${esc(v === 'true' ? 'yes' : v)}` +
    `<button onclick="dropFilter('${esc(k)}')" aria-label="Remove ${esc(LABELS[k]||k)} filter">×</button></span>`
  ).join('');
}
function dropFilter(key){
  const c = el(CONTROL[key]); if (!c) return;
  if (c.type === 'checkbox') c.checked = false; else c.value = '';
  go();
}
function clearAll(){
  Object.values(CONTROL).forEach(id => { const c = el(id); if (!c) return;
    if (c.type === 'checkbox') c.checked = false; else c.value = ''; });
  go();
}

function firstRun(){
  el('results').innerHTML = '<div class="empty"><div class="big">Describe the role, not the keywords</div>' +
    'This searches meaning, so "someone to own our payments backend" works as well as a job title.<br>' +
    'Set the structured constraints — experience, location, salary — on the left.</div>';
}

async function go(){
  const q = el('q').value.trim();
  drawActive();
  if(!q){ firstRun(); el('n').textContent = ''; return; }
  const p = new URLSearchParams({ q, k: el('k').value });
  for (const [key, value] of Object.entries(currentFilters())) p.set(key, value);
  el('results').innerHTML = skeleton() + skeleton() + skeleton();
  el('n').textContent = 'searching…';
  const t0 = performance.now();
  let rows;
  try { rows = await (await fetch('/search?'+p)).json(); }
  catch(e){ el('results').innerHTML = '<div class="empty">That search didn\\'t go through. Try again.</div>';
            el('n').textContent = ''; return; }
  if(!Array.isArray(rows)){
    el('results').innerHTML = '<div class="empty">One of the filters isn\\'t valid — clear it and try again.</div>';
    el('n').textContent = ''; return; }
  if(!rows.length){
    el('results').innerHTML = '<div class="empty"><div class="big">Nothing matched</div>' +
      'Try loosening a filter, or describe the role more broadly.</div>';
    el('n').textContent = '0 results'; return; }
  if (el('sort').value === 'new'){
    const ts = r => { const t = Date.parse(r.posted_at || ''); return isNaN(t) ? -1 : t; };
    rows.sort((a,b) => ts(b) - ts(a));   // unparseable dates sink to the bottom
  }
  el('n').textContent = rows.length + ' result' + (rows.length===1?'':'s') +
    ' · ' + Math.round(performance.now()-t0) + ' ms';
  el('results').innerHTML = rows.map((r,i) => {
    const s = Number(r.score) || 0, c = tone(s);
    return `
    <div class="card" style="--tone:${c}; animation-delay:${Math.min(i,12)*35}ms">
      <div class="hd">
        <div style="min-width:0">
          <a class="title" href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener">${esc(r.title)}</a>
          <div class="org">${esc(r.company)}${r.location? ' <span>·</span> '+esc(r.location) : ''}</div>
        </div>
        <div class="match" title="Semantic similarity to your search">
          <div class="v">${s.toFixed(2)}</div>
          <div class="track"><div class="fill" style="--pct:${Math.round(Math.max(0,Math.min(1,s))*100)}%"></div></div>
        </div>
      </div>
      <div class="tags">
        ${isNew(r.first_seen)? '<span class="tag new">new</span>':''}
        ${r.remote? '<span class="tag rem">remote</span>':''}
        ${r.salary? '<span class="tag pay">'+esc(r.salary)+'</span>':''}
        ${r.employment_type? '<span class="tag">'+esc(r.employment_type)+'</span>':''}
        ${r.min_years!=null? '<span class="tag mono">'+(Number(r.min_years)||0)+'+ yrs</span>':''}
        ${age(r.posted_at)? '<span class="tag mono">'+age(r.posted_at)+'</span>':''}
        ${r.ats? '<span class="tag">'+esc(r.ats)+'</span>':''}
      </div>
    </div>`; }).join('');
}

// Résumé → Query: fills the search box for the user to edit, never searches on its own.
// The password rides along when filled; the server remembers this IP after one success.
async function readResume(){
  const text = el('resume').value.trim();
  const msg = el('rmsg'), btn = el('rbtn');
  if (!text){ msg.textContent = 'Paste your résumé first.'; return; }
  btn.disabled = true; msg.textContent = 'Reading…';
  try {
    const r = await fetch('/resume-to-query', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ text, password: el('rpass').value })
    });
    const data = await r.json();
    if (!r.ok){ msg.textContent = data.error || ('Failed (' + r.status + ')'); return; }
    el('q').value = data.query;
    el('q').focus();
    msg.textContent = 'Edit the search above, then hit Search.';
  } catch(e){ msg.textContent = 'That request didn\\'t go through. Try again.'; }
  finally { btn.disabled = false; }
}

// Google sign-in returns a signed credential; the address is read from it server-side, so
// the browser never gets to say who it is subscribing.
async function onGoogleCredential(resp){
  const msg = el('amsg');
  const q = el('q').value.trim();
  if (!q){ msg.textContent = 'Type the role you want first.'; return; }
  msg.textContent = 'Subscribing…';
  try {
    const r = await fetch('/subscribe', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ credential: resp.credential, query: q, filters: currentFilters() })
    });
    const data = await r.json();
    msg.textContent = r.ok ? ('Subscribed — digests go to ' + data.email)
                           : (data.error || ('Failed (' + r.status + ')'));
  } catch(e){ msg.textContent = 'That request didn\\'t go through. Try again.'; }
}
function initAlerts(){
  if (!window.google || !'__GOOGLE_CLIENT_ID__') return;
  google.accounts.id.initialize({ client_id: '__GOOGLE_CLIENT_ID__', callback: onGoogleCredential });
  google.accounts.id.renderButton(el('gsignin'), { theme: 'filled_black', size: 'medium' });
}
firstRun();
</script>
<script src="https://accounts.google.com/gsi/client" async onload="initAlerts()"></script>
</body></html>"""

_PAGE = (
    _TEMPLATE.replace("__NJOBS__", f"{_table.count_rows():,}")
    # Hidden until the pipeline adds the column, so the control can ship ahead of the data
    # (the `el('seen')` guard in go() keeps the query-string builder happy either way).
    .replace("__SINCE_HIDDEN__", "" if _HAS_FIRST_SEEN else ' style="display:none"')
    # Hidden until the RESUME_PASSWORD secret exists — dark feature, same shape as above.
    .replace("__RESUME_HIDDEN__", "" if _RESUME_PASSWORD else ' style="display:none"')
    # Same dark-until-ready shape: no alerts secrets, no panel and no sign-in button.
    .replace("__ALERTS_HIDDEN__", "" if _ALERTS_ON else ' style="display:none"')
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
