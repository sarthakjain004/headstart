#!/usr/bin/env python3
"""Exercise shipped templates/JS in Chromium against a loopback-only fixture server.

Run after `python -m playwright install chromium`. No model, external account, or production
data is involved. API replies are fixtures; DOM, keyboard, layout and request ordering are real.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from headstart.search_filter_compiler import (
    KEYWORD_DEFAULT_SCOPE,
    keyword_scope_options,
)

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    ui = ROOT / "src/headstart/ui"
    app = Flask(
        __name__,
        static_folder=str(ui / "static"),
        template_folder=str(ui / "templates"),
    )
    old_started, release_old = threading.Event(), threading.Event()
    scopes = keyword_scope_options()

    @app.get("/")
    def index():
        return render_template(
            "base.html",
            cfg={
                "keyword_default_scope": KEYWORD_DEFAULT_SCOPE,
                "keyword_scopes": {value: needs for value, _, needs in scopes},
                "has_first_seen": True,
            },
            keyword_scopes=scopes,
            keyword_default_scope=KEYWORD_DEFAULT_SCOPE,
            has_description=True,
            has_first_seen=True,
            has_min_salary=True,
            njobs="1",
            atses=["lever"],
            india_opts=[],
            posted_opts=[],
            seen_opts=[],
            currencies=["USD"],
            sets_on=True,
            alerts_on=False,
            saved_on=False,
            profile_on=False,
            trends_on=False,
            auth_on=False,
            repo="https://example.invalid",
        )

    @app.get("/search")
    def search():
        query = request.args.get("q") or "browse"
        if query == "old":
            old_started.set()
            release_old.wait(timeout=10)
        return jsonify(
            [
                {
                    "id": "lever:fixture:" + query,
                    "title": query.upper(),
                    "company": "Fixture",
                    "url": "https://example.invalid/job",
                    "location": "Remote",
                    "remote": True,
                    "score": None,
                    "first_seen": "2026-09-01T00:00:00+00:00",
                }
            ]
        )

    @app.get("/facets")
    def facets():
        return jsonify({"total": 1, "facets": {}})

    @app.get("/sets")
    def sets():
        return jsonify(
            [
                {
                    "id": "fixture",
                    "name": "Fixture set",
                    "query": "saved",
                    "search_filters": {},
                }
            ]
        )

    @app.get("/me")
    def me():
        return jsonify({"email": ""})

    @app.get("/coverage")
    def coverage():
        return jsonify({"total": 1, "fields": {"description": 1}})

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as browser_api:
            browser = browser_api.chromium.launch()
            for width in (1280, 390):
                old_started.clear()
                release_old.clear()
                page = browser.new_page(viewport={"width": width, "height": 900})
                errors = []
                page.on(
                    "pageerror", lambda error, errors=errors: errors.append(str(error))
                )
                page.route(
                    "**/*",
                    lambda route: (
                        route.continue_()
                        if route.request.url.startswith(base + "/")
                        else route.abort()
                    ),
                )
                page.goto(base)
                expect(page.locator("#results .title")).to_contain_text("BROWSE")
                query = page.get_by_label("Describe the role you want", exact=True)
                query.fill("old")
                query.press("Enter")
                assert old_started.wait(timeout=5), "old request was not started"
                query.fill("new")
                query.press("Enter")
                expect(page.locator("#results .title")).to_contain_text("NEW")
                with page.expect_response("**/search?q=old*"):
                    release_old.set()
                page.wait_for_load_state("networkidle")
                expect(page.locator("#results .title")).to_contain_text("NEW")
                page.get_by_role("button", name="Save this search", exact=True).click()
                expect(
                    page.get_by_text(
                        "Keyword filters and numeric salary brackets aren't saved yet.",
                        exact=True,
                    )
                ).to_be_visible()
                page.get_by_role("link", name="Matches", exact=True).click()
                expect(page.locator("#matches-results .title")).to_contain_text("SAVED")
                page.get_by_role("link", name="Data", exact=True).click()
                expect(
                    page.get_by_role(
                        "heading", name="Saved searches and delivery limits"
                    )
                ).to_be_visible()
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                ), "horizontal page overflow"
                assert not errors, errors
                print(
                    f"browser smoke: {width}px, keyboard/search race/navigation/disclosures passed",
                    flush=True,
                )
                page.close()
            browser.close()
    finally:
        release_old.set()
        server.shutdown()
        worker.join(timeout=2)


if __name__ == "__main__":
    main()
