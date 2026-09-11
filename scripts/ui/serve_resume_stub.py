"""The Résumé tab's real page, served with a stub context — no index, no model, no secrets.

`scripts/ui/serve.py` is the full local dev UI: it opens LanceDB and loads the encoder, which
takes tens of seconds and needs a local copy of the index. Nothing in the résumé builder reads
either — it is ten browser scripts over `localStorage` — so the browser tests that drive it get
this instead: the SAME `templates/base.html` and the SAME `static/resume/*.js` the Space serves,
with every context value the page needs stubbed.

Inert where the value is only data — no jobs, no currencies, no sign-in. NOT inert for
`resume_sync_on`: the account switch is server-rendered only where that flag is true, so
leaving it out set `sync = null` in `resume_editor.js` and put the entire account surface
(ADR-0124) out of reach of any browser test, while this file claimed to be stubbing it. The
`/resumes` routes below are the small in-memory store that flag then needs to be true about.

Used by `tests/test_resume_editor_browser.py`, and runnable by hand:

    python scripts/ui/serve_resume_stub.py 8123    # then open http://127.0.0.1:8123/#resume
"""

from __future__ import annotations

import sys
from pathlib import Path

from flask import Flask, jsonify, render_template, request

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
        # NOT inert, and that is the point: an absent flag renders falsy, `#rb-account` is never
        # rendered, and `resume_editor.js` sets `sync = null` — so the whole account surface was
        # unreachable in a browser test while this file claimed to stub it. The `/resumes` routes
        # below are what the flag then needs to be true about.
        resume_sync_on=True,
    )


# ---- the account copy (ADR-0124), in a dict ------------------------------------------------
# One process, one visitor, no Hub. It keeps the ONE rule the real routes exist to enforce — a
# push is accepted only at exactly one past the stored revision — because that rule is what the
# browser's conflict path is written against, and a stub that accepted everything would leave
# that path untestable here. `/stub/fail/unreadable` adds the answer a browser cannot otherwise
# produce: the bodyless 409 the real route gives when the stored copy could not be READ. The
# other failure that matters, an unreachable account, is this process being stopped.
_RESUMES: dict[str, dict] = {}
_UNREADABLE = {"on": False}


@app.route("/stub/fail/<mode>")
def stub_fail(mode: str):
    """`/stub/fail/unreadable` to refuse every push; `/stub/fail/none` to stop."""
    _UNREADABLE["on"] = mode == "unreadable"
    return jsonify({"unreadable": _UNREADABLE["on"]})


@app.route("/resumes")
def list_resumes():
    rows = [
        {k: doc.get(k) for k in ("id", "name", "layoutId", "updatedAt", "rev")}
        for doc in _RESUMES.values()
    ]
    rows.sort(key=lambda r: str(r.get("updatedAt") or ""), reverse=True)
    return jsonify(rows)


@app.route("/resumes/<doc_id>", methods=["GET", "PUT", "DELETE"])
def one_resume(doc_id: str):
    if request.method == "GET":
        stored = _RESUMES.get(doc_id)
        return (
            jsonify(stored) if stored else (jsonify({"error": "no such résumé"}), 404)
        )
    if request.method == "DELETE":
        gone = _RESUMES.pop(doc_id, None)
        return (
            jsonify({"ok": True})
            if gone
            else (jsonify({"error": "no such résumé"}), 404)
        )
    document = request.get_json(silent=True)
    if not isinstance(document, dict) or document.get("id") != doc_id:
        return jsonify({"error": "that is not a résumé"}), 400
    if _UNREADABLE["on"]:
        return jsonify(
            {"error": "your account could not be read — nothing was changed"}
        ), 409
    stored = _RESUMES.get(doc_id)
    if stored and document.get("rev") != int(stored.get("rev") or 0) + 1:
        return jsonify(
            {"error": "this résumé changed somewhere else", "stored": stored}
        ), 409
    _RESUMES[doc_id] = document
    return jsonify({"ok": True, "rev": document.get("rev")})


if __name__ == "__main__":
    app.run(port=int(sys.argv[1]) if len(sys.argv) > 1 else 8123, use_reloader=False)
