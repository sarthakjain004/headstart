"""The Résumé tab's real page, served with a stub context — no index, no model, no secrets.

`scripts/ui/serve.py` is the full local dev UI: it opens LanceDB and loads the encoder, which
takes tens of seconds and needs a local copy of the index. Nothing in the résumé builder reads
either — it is ten browser scripts over `localStorage` — so the browser tests that drive it get
this instead: the SAME `templates/base.html` and the SAME `static/resume/*.js` the Space serves,
with every context value the page needs stubbed to something inert.

Used by `tests/test_resume_editor_browser.py`, and runnable by hand:

    python scripts/ui/serve_resume_stub.py 8123    # then open http://127.0.0.1:8123/#resume
"""

from __future__ import annotations

import sys
from pathlib import Path

from flask import Flask, jsonify, render_template

_UI = Path(__file__).resolve().parents[2] / "src" / "headstart" / "ui"

app = Flask(
    __name__, template_folder=str(_UI / "templates"), static_folder=str(_UI / "static")
)


@app.route("/")
def index():
    """base.html with the résumé panel live and every other tab's data empty."""
    return render_template(
        "base.html",
        cfg={"google_client_id": "", "has_first_seen": True, "fx": None},
        repo="https://github.com/sarthakjain004/headstart",
        auth_on=False,
        njobs="0",
        atses=[],
        india_opts=[],
        has_first_seen=True,
        currencies=[],
        fx_as_of=None,
        fx_converts=False,
        seen_opts=[],
        posted_opts=[],
        keyword_scopes=[],
        keyword_default_scope="",
        has_description=True,
        trends_on=False,
        alerts_on=False,
        sets_on=False,
        saved_on=False,
        profile_on=False,
    )


@app.route("/me")
def me():
    """The header's identity probe — always signed out, so the page never waits on auth."""
    return jsonify({"auth": False, "email": None})


if __name__ == "__main__":
    app.run(port=int(sys.argv[1]) if len(sys.argv) > 1 else 8123, use_reloader=False)
