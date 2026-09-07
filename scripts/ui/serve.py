"""Local dev UI — a thin adapter over the same modules the HF Space app serves (ADR-0042).

Renders the shared templates/static from ``src/headstart/ui`` and answers ``/search``
through the shared ``headstart.search.JobSearch``, against the local LanceDB copy of the
``jobs`` table. No sign-in wall, no alerts, no résumé panel, no trends — those need Space
secrets or state; the page simply renders without them, which is also what a Space with no
secrets shows.

Run:  python scripts/ui/serve.py    then open  http://localhost:8000
"""

from __future__ import annotations

from pathlib import Path

import lancedb
from flask import Flask, jsonify, render_template, request

from headstart import facets, fx, geo
from headstart.search import (
    KEYWORD_DEFAULT_SCOPE,
    PROD_TABLE,
    JobSearch,
    keyword_scope_options,
    load_encoder,
)

_REPO = Path(__file__).resolve().parents[2]
_UI = _REPO / "src" / "headstart" / "ui"

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
    return render_template(
        "base.html",
        cfg={
            "google_client_id": "",
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
        keyword_scopes=keyword_scope_options(),
        keyword_default_scope=KEYWORD_DEFAULT_SCOPE,
        has_description=_searcher.has_description,
        trends_on=False,
        alerts_on=False,
        sets_on=False,
        saved_on=False,
        profile_on=False,
    )


@app.route("/coverage")
def coverage():
    """The Data tab's live counts (ADR-0113) — the Space route's local twin."""
    return jsonify(_searcher.coverage())


@app.route("/search")
def search_jobs():
    try:
        return jsonify(_searcher.run(request.args))
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
