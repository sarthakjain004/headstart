"""User journeys over the shipped UI, with owned HTTP transports replaced by fixtures.

No model, production data, account or employer page is accessed. Layout, navigation,
focus, native dialogs, requests and the résumé editor are real Chromium behavior.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from flask import Flask, jsonify, render_template, request
from werkzeug.serving import make_server

from headstart.search_filters.compiler import (
    KEYWORD_DEFAULT_SCOPE,
    keyword_scope_options,
)

pw = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def experience():
    ui = ROOT / "src/headstart/ui"
    app = Flask(
        __name__,
        static_folder=str(ui / "static"),
        template_folder=str(ui / "templates"),
    )
    sets = [
        {
            "id": "backend",
            "name": "Backend roles",
            "query": "backend",
            "search_filters": {},
            "emails": False,
        }
    ]
    jobs = [
        {
            "id": "greenhouse:fixture:1",
            "title": "Backend Engineer",
            "company": "Fixture Company",
            "url": "https://example.invalid/job/1",
            "location": "Remote, United States",
            "remote": True,
            "score": 0.8,
            "salary": "$120,000–150,000",
            "first_seen": "2026-10-01T00:00:00+00:00",
        }
    ]
    saved = []
    writes = []
    queries = []
    profile = {"query": "backend engineer", "years": "3", "location": "Berlin"}
    fail_search = [False]
    scopes = keyword_scope_options()

    @app.get("/")
    def index():
        return render_template(
            "base.html",
            cfg={
                "keyword_default_scope": KEYWORD_DEFAULT_SCOPE,
                "keyword_scopes": {v: needs for v, _, needs in scopes},
                "has_first_seen": True,
            },
            njobs="1",
            atses=["greenhouse"],
            repo="https://example.invalid",
            keyword_scopes=scopes,
            keyword_default_scope=KEYWORD_DEFAULT_SCOPE,
            has_description=True,
            has_first_seen=True,
            has_min_salary=True,
            country_opts=[("US", "United States"), ("DE", "Germany")],
            india_opts=[],
            currencies=["USD"],
            posted_opts=[],
            seen_opts=[],
            sets_on=True,
            saved_on=True,
            profile_on=True,
            hot_on=True,
            trends_on=True,
            companies_on=True,
            alerts_on=False,
        )

    @app.get("/me")
    def me():
        return jsonify(auth=True, email="fixture@example.invalid")

    @app.get("/signin")
    def signin():
        return render_template(
            "signin.html",
            google_client_id="fixture",
            njobs="1",
            n_atses=1,
            n_new=None,
            new_days=7,
            repo="https://example.invalid",
        )

    @app.get("/search")
    def search():
        queries.append(request.args.to_dict())
        return (
            (jsonify(error="Fixture search unavailable"), 503)
            if fail_search[0]
            else jsonify(
                [
                    {**job, "score": job["score"] if request.args.get("q") else None}
                    for job in jobs
                ]
            )
        )

    @app.get("/facets")
    def facets():
        return jsonify(total=1, facets={})

    @app.route("/sets", methods=["GET", "POST"])
    def search_sets():
        if request.method == "POST":
            body = request.json
            writes.append(("set", body))
            existing = next((s for s in sets if s["id"] == body.get("id")), None)
            if existing:
                existing.update(name=body["name"])
            else:
                sets.append(
                    {
                        "id": "new",
                        "name": body["name"],
                        "query": body["query"],
                        "search_filters": body["filters"],
                        "emails": False,
                    }
                )
        return jsonify(sets)

    @app.delete("/sets/<key>")
    def delete_set(key):
        writes.append(("delete", key))
        sets[:] = [s for s in sets if s["id"] != key]
        return jsonify(ok=True)

    @app.post("/sets/<key>/email")
    def email_set(key):
        next(s for s in sets if s["id"] == key)["emails"] = request.json["on"]
        return jsonify(ok=True)

    @app.route("/saved", methods=["GET", "POST"])
    def saved_jobs():
        if request.method == "POST":
            body = dict(request.json)
            body.update(open=True, starred_at="2026-10-02T00:00:00+00:00")
            saved.append(body)
            return jsonify(body)
        return jsonify(saved)

    @app.delete("/saved/<path:key>")
    def unsave(key):
        saved[:] = [j for j in saved if j["job_id"] != key]
        return jsonify(ok=True)

    @app.route("/profile", methods=["GET", "POST", "DELETE"])
    def search_profile():
        if request.method == "POST":
            profile.update(request.json)
        if request.method == "DELETE":
            writes.append(("delete-profile", None))
            profile.clear()
        return jsonify(profile)

    @app.route("/companies", methods=["GET", "POST"])
    def companies():
        return jsonify(followed=[], hidden=[])

    @app.get("/companies/lookup")
    def lookup():
        return jsonify([])

    @app.get("/companies/suggest")
    def suggestions():
        return jsonify([])

    @app.get("/hot")
    def hot():
        row = {
            "key": "greenhouse:fixture",
            "company": "Fixture Company",
            "boards": ["greenhouse:fixture"],
            "stock": 10,
            "net": 3,
            "opened": 5,
            "closed": 2,
            "rate": 50,
            "expansion": 3,
            "percent": 30,
            "previous": 7,
        }
        return jsonify(
            lenses={
                key: [row]
                for key in ["expansion", "opened_less_closed", "volume", "rate"]
            },
            window={
                "from": "2026-09-25T00:00:00+00:00",
                "to": "2026-10-02T00:00:00+00:00",
                "turnover_from": "2026-09-25T00:00:00+00:00",
            },
            excluded={},
        )

    @app.get("/trends")
    def trends():
        golden = json.loads(
            (
                ROOT
                / "tests/fixtures/trend_readings/index_bases_read_off_the_first_count.json"
            ).read_text()
        )
        return jsonify(**golden["answer_input"], reading=golden["reading"])

    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with pw.sync_playwright() as api:
        browser = api.chromium.launch()
        yield (
            browser,
            f"http://127.0.0.1:{server.server_port}",
            writes,
            queries,
            fail_search,
        )
        browser.close()
    server.shutdown()


def open_screen(page, screen, compact):
    link = page.locator(f'.tabs [data-tab="{screen}"]:visible')
    if not link.count() and compact:
        page.locator("#nav-more").click()
        link = page.locator(f'.tabs [data-tab="{screen}"]:visible')
    link.click()
    pw.expect(page.locator(f"#panel-{screen}")).to_be_visible()


@pytest.mark.parametrize("width", [390, 1440])
def test_discover_save_manage_prepare_and_recover(experience, width):
    browser, url, writes, queries, fail_search = experience
    page = browser.new_page(
        viewport={"width": width, "height": 900}, reduced_motion="reduce"
    )
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    pw.expect(page.locator(".tour-offer")).to_have_count(0)
    page.get_by_role("button", name="Backend engineering", exact=True).click()
    pw.expect(page.locator("#results .title")).to_contain_text("Backend Engineer")
    pw.expect(page.locator("#results")).to_be_focused()
    page.get_by_role("button", name="Save this job", exact=True).click()
    pw.expect(
        page.get_by_role("button", name="Remove this job from saved", exact=True)
    ).to_have_text("Saved")
    open_screen(page, "saved", width < 1280)
    pw.expect(page.locator("#saved-results .title")).to_contain_text("Backend Engineer")
    open_screen(page, "search", width < 1280)
    page.locator("#savebtn").click()
    page.locator("#savename").fill("My backend search")
    page.locator("#savecancel").click()
    assert not writes
    pw.expect(page.locator("#savebtn")).to_be_focused()
    page.locator("#savebtn").click()
    page.locator("#savego").click()
    pw.expect(page.locator("#save-success")).to_be_visible()
    page.locator("#save-success a").click()
    pw.expect(page.locator("#matches-results .title")).to_be_visible()
    page.locator('#sets-strip [data-id="new"] button').click()
    page.locator('#matches-actions [data-act="rename"]').click()
    pw.expect(page.locator("#decision-dialog")).to_be_visible()
    page.locator("#decision-value").fill("Backend shortlist")
    page.locator("#decision-confirm").click()
    pw.expect(page.locator("#sets-strip")).to_contain_text("Backend shortlist")
    before = len(writes)
    page.locator('#matches-actions [data-act="del"]').click()
    page.keyboard.press("Escape")
    pw.expect(page.locator("#decision-dialog")).to_be_hidden()
    assert len(writes) == before
    page.locator('#matches-actions [data-act="del"]').click()
    page.locator("#decision-confirm").click()
    pw.expect(page.locator("#sets-strip")).not_to_contain_text("Backend shortlist")
    open_screen(page, "search", width < 1280)
    page.locator('input[name="qmode"][value="title"]').check()
    open_screen(page, "profile", width < 1280)
    pw.expect(page.locator("#pquery")).to_have_value("backend engineer")
    pw.expect(page.locator("#presume")).to_be_hidden()
    if width < 1280:
        page.locator("#psave").focus()
        assert (
            page.locator("#psave").bounding_box()["y"]
            + page.locator("#psave").bounding_box()["height"]
            < page.locator(".mobile-nav").bounding_box()["y"]
        )
    page.locator("#papply").click()
    pw.expect(page.locator("#panel-search")).to_be_visible()
    pw.expect(page.locator('input[name="qmode"][value="meaning"]')).to_be_checked()
    pw.expect(page.locator("#results")).to_be_focused()
    assert any(q.get("location") == "Berlin" for q in queries)
    if width < 1100:
        pw.expect(page.locator("#country")).to_be_visible()
        pw.expect(page.locator("#maxyears")).to_be_visible()
        pw.expect(page.locator("#remote")).to_be_visible()
        page.locator("#maxyears").focus()
        page.set_viewport_size({"width": 1440, "height": 900})
        pw.expect(page.locator("#rail #maxyears")).to_have_value("3")
        pw.expect(page.locator("#maxyears")).to_be_focused()
        page.set_viewport_size({"width": width, "height": 900})
        pw.expect(page.locator("#quick-filters #maxyears")).to_have_value("3")
        pw.expect(page.locator("#maxyears")).to_be_focused()
    fail_search[0] = True
    page.locator("#search-go").click()
    pw.expect(page.locator("[data-retry-search]")).to_be_visible()
    fail_search[0] = False
    page.locator("[data-retry-search]").click()
    pw.expect(page.locator("#results .title")).to_be_visible()
    open_screen(page, "hot", width < 1280)
    pw.expect(page.locator(".hot-see")).to_be_visible()
    page.locator(".hot-see").click()
    pw.expect(page.locator("#panel-search")).to_be_visible()
    pw.expect(page.locator("#results")).to_be_focused()
    open_screen(page, "trends", width < 1280)
    pw.expect(page.locator("#trends-chart")).to_be_visible()
    page.locator("#trends-table-toggle").click()
    pw.expect(page.locator("#trends-table-wrap")).to_be_visible()
    open_screen(page, "resume", width < 1280)
    page.wait_for_function("window.ResumeEditor && ResumeEditor.current()")
    page.locator("#rb-seg-preview").click()
    pw.expect(page.locator("#rb-paper")).to_be_visible()
    page.locator("#rb-download").click()
    pw.expect(page.locator("#rb-pop-download")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors, errors
    page.close()


@pytest.mark.parametrize("width", [375, 768, 1440])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_all_screens_keep_their_geometry_without_the_skin(experience, width, theme):
    browser, url, _, _, _ = experience
    page = browser.new_page(
        viewport={"width": width, "height": 900},
        color_scheme=theme,
        reduced_motion="reduce",
    )
    page.goto(url)
    geometry = """() => [...document.querySelectorAll('.shell, .shell *')]
      .filter(el => el.getClientRects().length && el.namespaceURI === 'http://www.w3.org/1999/xhtml')
      .map(el => {const r=el.getBoundingClientRect(); return [el.id, r.x, r.y, r.width, r.height];})"""
    toggle_skin = """disabled => {
      function visit(sheet) {
        if (sheet.href?.split('?')[0].endsWith('-skin.css') || sheet.href?.split('?')[0].endsWith('home-theme.css') || sheet.href?.split('?')[0].endsWith('theme-tokens.css')) {sheet.disabled=disabled; return;}
        for (const rule of sheet.cssRules) if (rule.styleSheet) visit(rule.styleSheet);
      }
      [...document.styleSheets].forEach(visit);
    }"""
    for screen in [
        "home",
        "search",
        "saved",
        "matches",
        "hot",
        "trends",
        "profile",
        "resume",
    ]:
        open_screen(page, screen, width < 1280)
        if screen == "trends":
            pw.expect(page.locator("#trends-chart")).to_be_visible()
        if screen == "resume":
            page.wait_for_function("window.ResumeEditor && ResumeEditor.current()")
        page.evaluate("document.fonts.ready")
        # Fade affects no geometry; allow asynchronous fixture data and layout observers
        # to settle before comparing the same state with only appearance removed.
        page.wait_for_timeout(100)
        before = page.evaluate(geometry)
        page.evaluate(toggle_skin, True)
        after = page.evaluate(geometry)
        assert before == after, f"{screen} at {width}px/{theme}: skin moved the layout"
        page.evaluate(toggle_skin, False)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
            screen,
            width,
            theme,
        )
    page.close()


def test_theme_choice_and_mobile_reading_focus_survive_reload(experience):
    browser, url, _, _, _ = experience
    page = browser.new_page(
        viewport={"width": 390, "height": 900},
        color_scheme="light",
        reduced_motion="reduce",
    )
    page.goto(url)
    page.get_by_role("button", name="Use dark theme", exact=True).click()
    page.reload()
    pw.expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    pw.expect(
        page.get_by_role("button", name="Use light theme", exact=True)
    ).to_be_visible()
    rows = [
        {
            "id": f"greenhouse:company{i}:1",
            "title": f"Engineer {i}",
            "company": f"Company {i}",
            "url": f"https://example.invalid/{i}",
            "score": 0.8,
        }
        for i in range(20)
    ]
    page.route("**/search?*", lambda route: route.fulfill(json=rows))
    page.route(
        "**/facets?*", lambda route: route.fulfill(json={"total": 20, "facets": {}})
    )
    page.get_by_role("button", name="Backend engineering", exact=True).click()
    pw.expect(page.locator("#results .title")).to_have_count(20)
    pw.expect(page.locator("#results")).to_be_focused()
    assert (
        page.locator("#results .title").first.bounding_box()["y"]
        < page.locator(".mobile-nav").bounding_box()["y"]
    )
    page.close()


@pytest.mark.parametrize("width", [375, 1440])
def test_signin_uses_the_chosen_skin_and_exposes_the_action_first(experience, width):
    """Mock only Google's rendering interface; no credential or authentication is exercised."""
    browser, url, _, _, _ = experience
    page = browser.new_page(viewport={"width": width, "height": 667})
    page.add_init_script("localStorage.setItem('hs.theme', 'dark')")
    page.route(
        "https://accounts.google.com/gsi/client",
        lambda route: route.fulfill(
            content_type="application/javascript",
            body="""
      window.google={accounts:{id:{initialize(){},renderButton(host){
        const button=document.createElement('button');button.textContent='Google sign-in fixture';
        button.style.minHeight='44px';host.append(button);
      }}}};
    """,
        ),
    )
    page.goto(url + "/signin")
    pw.expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    button = page.get_by_role("button", name="Google sign-in fixture", exact=True)
    pw.expect(button).to_be_visible()
    assert button.bounding_box()["y"] + button.bounding_box()["height"] < 667
    assert (
        page.locator(".gate").bounding_box()["y"]
        < page.locator(".mid").bounding_box()["y"]
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    before = page.locator(".wrap").evaluate(
        "el => [...el.querySelectorAll('*')].map(n=>{const r=n.getBoundingClientRect();return [r.x,r.y,r.width,r.height]})"
    )
    page.evaluate(
        "[...document.styleSheets].filter(s=>/signin-skin|theme-tokens/.test(s.href)).forEach(s=>s.disabled=true)"
    )
    after = page.locator(".wrap").evaluate(
        "el => [...el.querySelectorAll('*')].map(n=>{const r=n.getBoundingClientRect();return [r.x,r.y,r.width,r.height]})"
    )
    assert before == after, "Sign-in skin changed placement"
    page.close()
