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

from headstart import geo
from headstart.search import PROD_TABLE, JobSearch, load_encoder

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


@app.route("/")
def index():
    return render_template(
        "base.html",
        cfg={"google_client_id": ""},
        njobs=f"{_table.count_rows():,}",
        atses=_searcher.atses,
        india_opts=geo.dropdown_options(),
        has_first_seen=_searcher.has_first_seen,
        trends_on=False,
        resume_on=False,
        alerts_on=False,
        sets_on=False,
        saved_on=False,
    )


@app.route("/search")
def search_jobs():
    try:
        return jsonify(_searcher.run(request.args))
    except ValueError:
        return jsonify({"error": "invalid filter"}), 400


@app.route("/me")
def me():
    """The header identity probe — always signed out locally."""
    return jsonify({"auth": False, "email": None})


if __name__ == "__main__":
    app.run(port=8000, debug=False)
