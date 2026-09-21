"""Local dev UI — a thin adapter over the same modules the HF Space app serves (ADR-0042).

Renders the shared templates/static from ``src/headstart/ui`` and answers ``/search``
through the shared ``headstart.search.JobSearch``, against the local LanceDB copy of the
``jobs`` table. No sign-in wall, no alerts, no résumé panel, no trends — those need Space
secrets or state; the page simply renders without them, which is also what a Space with no
secrets shows.

Run:  python scripts/ui/serve.py    then open  http://localhost:8000
"""

from __future__ import annotations

import json
from pathlib import Path

import lancedb
from flask import Flask, jsonify, render_template, request

import headstart
from headstart import facets, fx, geo
from headstart.alerts.store import CompanyPrefs
from headstart.search import (
    KEYWORD_DEFAULT_SCOPE,
    PROD_TABLE,
    JobSearch,
    board_clause,
    keyword_scope_options,
    load_encoder,
)

_REPO = Path(__file__).resolve().parents[2]
# Resolved off the installed package (ADR-0153), exactly like the Space's app.py — both find
# the same headstart/ui regardless of whether headstart is this repo's src/ or the Space
# image's copy of it.
_UI = Path(headstart.__file__).parent / "ui"

# The hot list is a plain artifact with no secret behind it, so unlike trends it renders here
# whenever a local pipeline run (or a pull) has left one — which is what makes the tab
# reviewable without deploying.
_HOT_PATH = _REPO / "data" / "state" / "hot_boards.json"
_HOT = json.loads(_HOT_PATH.read_text(encoding="utf-8")) if _HOT_PATH.exists() else {}

print("loading model + index ...", flush=True)
_model = load_encoder()
_table = lancedb.connect(_REPO / "data" / "lancedb").open_table(PROD_TABLE)
_searcher = JobSearch(_model, _table)
print(f"ready: {_table.count_rows()} jobs", flush=True)

app = Flask(
    __name__,
    template_folder=str(_UI / "templates"),
    static_folder=str(_UI / "static"),
)


def _fx_as_of() -> str | None:
    """The date on the committed rate table, or None when it cannot be read (ADR-0117)."""
    return (fx.table() or {}).get("as_of")


def _fx_converts(currencies: list[str]) -> bool:
    """Whether a bracket can actually cross a currency boundary here.

    Two served currencies must both carry a rate; with fewer, `build_filter` compiles the
    single-currency clause and any copy promising conversion would be describing nothing.
    """
    rates = (fx.table() or {}).get("rates") or {}
    return len([c for c in currencies if c in rates]) > 1


@app.route("/")
def index():
    scopes = keyword_scope_options()  # the Keyword filter's one map (ADR-0104)
    return render_template(
        "base.html",
        cfg={
            "google_client_id": "",
            # The Keyword filter's scopes (ADR-0104) — window.CFG reads these client-side
            # (app.js), so without them the local dev UI's scope <select> silently loses its
            # "needs description" disabling and its default-scope comparison.
            "keyword_scopes": {value: needs for value, _, needs in scopes},
            "keyword_default_scope": KEYWORD_DEFAULT_SCOPE,
            # The Data tab's browse line reads this to name the ordering actually in force.
            "has_first_seen": _searcher.has_first_seen,
            # The salary bracket's rate table (ADR-0117), so the page can print what a row
            # in another currency comes to in the one the user asked in — the SAME table the
            # where-clause was compiled from, never a second lookup, so the label beside a row
            # cannot disagree with the query that returned it. `None` when the table is
            # unreadable, and the page then converts nothing, exactly as `build_filter` does.
            "fx": fx.table(),
        },
        # The Data tab links out to the public repo (ADR-0113). Hardcoded here rather than
        # imported: this file is the local dev renderer and shares no config with the Space.
        repo="https://github.com/sarthakjain004/headstart",
        auth_on=False,  # the local renderer has no sign-in, so nothing is stored
        njobs=f"{_table.count_rows():,}",
        atses=_searcher.atses,
        india_opts=geo.dropdown_options(),
        has_first_seen=_searcher.has_first_seen,
        # the "Highest salary" sort option — dark until the ADR-0082 columns exist on the
        # served table, the same rule app.py and JobSearch.run apply to the value the control
        # would send (this was previously missing here — the option silently vanished from
        # local dev only, ADR-0153)
        has_min_salary=_searcher.has_min_salary_annual,
        currencies=_searcher.currencies,
        # The salary bracket converts across currencies (ADR-0117); the rail prints the date
        # of the rates it used, so a stale table is visible rather than silent.
        # Both facts, because the tip needs the second one: `as_of` says the table parsed,
        # but conversion only happens where the served currencies HAVE rates. Guarding the
        # claim on the date let a deployment with no comparable currencies still promise it.
        fx_as_of=_fx_as_of(),
        fx_converts=_fx_converts(_searcher.currencies),
        # the recency dropdowns, from the same tuples headstart.facets counts (ADR-0084)
        seen_opts=facets.SEEN_OPTIONS,
        posted_opts=facets.POSTED_OPTIONS,
        # ADR-0104's scope map, as the Space passes it. Without these three the rail still
        # renders — Jinja's default Undefined yields nothing rather than raising — but the
        # Keyword scope <select> comes out with zero options, silently.
        keyword_scopes=scopes,
        keyword_default_scope=KEYWORD_DEFAULT_SCOPE,
        has_description=_searcher.has_description,
        trends_on=False,
        hot_on=bool(_HOT),
        alerts_on=False,
        sets_on=False,
        # unlike the rest: this renderer implements /companies over one in-memory record
        companies_on=True,
        saved_on=False,
        profile_on=False,
        resume_sync_on=False,  # no sign-in here, so there is no account to keep a copy on
    )


@app.route("/coverage")
def coverage():
    """The Data tab's live counts (ADR-0113) — the Space route's local twin."""
    return jsonify(_searcher.coverage())


# Follow/hide (ADR-0171). The Space keeps these per Account in the HF-backed store; there are
# no accounts here, so this renderer keeps ONE in-memory set for the single local user. That is
# enough to exercise the real filter and the real UI, and it is deliberately not persisted —
# a dev server that remembered your hidden companies between restarts would hide a bug.
_LOCAL_COMPANIES = CompanyPrefs.blank("local")


def _company_where(args) -> str | None:
    """Mirror of the Space's per-request follow/hide clause."""
    clauses = []
    if args.get("mine") in ("1", "true"):
        clauses.append(
            board_clause(_LOCAL_COMPANIES.followed, exclude=False) or "false"
        )
    if _LOCAL_COMPANIES.hidden:
        clauses.append(board_clause(_LOCAL_COMPANIES.hidden, exclude=True))
    return " AND ".join(c for c in clauses if c) or None


@app.route("/companies")
def list_companies():
    return jsonify(
        {
            "followed": list(_LOCAL_COMPANIES.followed),
            "hidden": list(_LOCAL_COMPANIES.hidden),
        }
    )


@app.route("/companies", methods=["POST"])
def set_company():
    global _LOCAL_COMPANIES
    body = request.get_json(silent=True) or {}
    board = str(body.get("board") or "").strip()
    action = str(body.get("action") or "").strip()
    if not board or action not in ("follow", "hide", "clear"):
        return jsonify(
            {"error": "board and action (follow|hide|clear) are required"}
        ), 400
    _LOCAL_COMPANIES = _LOCAL_COMPANIES.with_board(board, action)
    return list_companies()


@app.route("/hot")
def hot_companies():
    """Mirror of the Space's route, so the tab is reviewable locally (ADR-0042)."""
    if not _HOT:
        return jsonify({"error": "no hot list built locally"}), 503
    return jsonify(_HOT)


@app.route("/search")
def search_jobs():
    try:
        return jsonify(
            _searcher.run(request.args, extra_where=_company_where(request.args))
        )
    except ValueError:
        return jsonify({"error": "invalid filter"}), 400


@app.route("/facets")
def search_facets():
    """Per-option result counts (issue #275) — the same shared path the Space serves."""
    try:
        return jsonify(_searcher.facets(request.args))
    except ValueError:
        return jsonify({"error": "invalid filter"}), 400


@app.route("/me")
def me():
    """The header identity probe — always signed out locally."""
    return jsonify({"auth": False, "email": None})


if __name__ == "__main__":
    app.run(port=8000, debug=False)
