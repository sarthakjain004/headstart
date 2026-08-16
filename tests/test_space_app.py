"""The Space app's wall and routes — `deploy/hf-space/app.py` (ADR-0035, ADR-0042).

The filter/search logic itself lives in `headstart.search` and is tested in
`tests/test_search.py`; what's left here is what only the app owns — the sign-in wall and
the wiring — which had no coverage anywhere, because `deploy/` sits outside `testpaths`
and importing the app pulls in a model download, a SentenceTransformer and a LanceDB
table. So the heavy imports are stubbed in `sys.modules` (the real `headstart.search`
rides along as the flat `search` module, exactly as deploy-space.yml lays it down) and the
module is loaded from its path — the same importlib trick `tests/test_check_liveness.py`
uses for `scripts/`. Auth requests ride `base_url="https://localhost"` because the session
cookie is `Secure` and the test client honours that over plain http.
"""

import importlib.util
import os
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import pytest

pytest.importorskip("flask")  # in [dev] so this runs in CI; guards a bare env

APP = Path(__file__).resolve().parents[1] / "deploy" / "hf-space" / "app.py"


def _module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


_RouterUnavailable = type("RouterUnavailable", (Exception,), {})


def _no_router(prompt):
    raise _RouterUnavailable("no router in tests")


class _Table:
    """Enough LanceDB for import time: an ats scan, a schema and a row count."""

    schema = types.SimpleNamespace(names=["ats", "title", "first_seen"])

    def search(self, *a, **k):
        return self

    def select(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def to_list(self):
        return [{"ats": "greenhouse"}, {"ats": "lever"}]

    def count_rows(self):
        return 2


@contextmanager
def _space_app(state, env=None):
    """Load the Space app from its path with the heavy imports stubbed (module docstring).

    ``env`` sets os.environ around the exec for config the module reads at import time —
    the sign-in wall's ``SECRET_KEY`` / ``GOOGLE_CLIENT_ID`` (ADR-0042)."""
    stubs = {
        "lancedb": _module(
            "lancedb",
            connect=lambda *a, **k: types.SimpleNamespace(
                open_table=lambda *a, **k: _Table()
            ),
        ),
        "sentence_transformers": _module(
            "sentence_transformers", SentenceTransformer=lambda *a, **k: object()
        ),
        "huggingface_hub": _module(
            "huggingface_hub", snapshot_download=lambda *a, **k: str(state)
        ),
        # Governs app.py's own `import geo` (the dropdown) ONLY. The flat `search` module
        # below is the real headstart.search, which bound the real headstart.geo at import —
        # so /search filter behaviour here runs real geo, not this stub. Filter tests belong
        # in tests/test_search.py, where that is explicit.
        "geo": _module(
            "geo",
            where=lambda place: None,
            DROPDOWN=["bengaluru"],
            dropdown_options=lambda: [("bengaluru", "Bengaluru")],
        ),
        "llm_router": _module(
            "llm_router",
            RouterUnavailable=_RouterUnavailable,
            # default: no router in tests — the parse route answers 503 unless a test
            # monkeypatches ask with a canned JSON reply
            ask=_no_router,
        ),
    }
    # The Space imports the alerts package and the flat search/profile_extract modules, as
    # deploy-space.yml lays them down — the real modules, not fakes, so the wiring under
    # test is real (profile_extract is pure json/re, so its scrub runs for real too).
    import headstart.alerts.access as _access
    import headstart.alerts.identity as _identity
    import headstart.alerts.store as _store
    import headstart.profile_extract as _profile_extract
    import headstart.search as _search

    stubs["alerts"] = _module("alerts", access=_access, identity=_identity)
    stubs["alerts.store"] = _store
    stubs["profile_extract"] = _profile_extract
    stubs["search"] = _search
    # Every stubbed name is restored, including the two above — leaving a fake `alerts` in
    # sys.modules would follow this fixture into every later test in the session. Env vars
    # are restored the same way, for the same reason.
    saved = {name: sys.modules.get(name) for name in stubs}
    saved_env = {key: os.environ.get(key) for key in (env or {})}
    sys.modules.update(stubs)
    os.environ.update(env or {})
    try:
        spec = importlib.util.spec_from_file_location("space_app", APP)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        for key, previous in saved_env.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    with _space_app(tmp_path_factory.mktemp("state")) as module:
        yield module


@pytest.fixture(scope="module")
def auth_app(tmp_path_factory):
    """The app with the sign-in wall on — both secrets present at import (ADR-0042)."""
    with _space_app(
        tmp_path_factory.mktemp("state"),
        env={"SECRET_KEY": "test-secret", "GOOGLE_CLIENT_ID": "client-id.example"},
    ) as module:
        yield module


@pytest.fixture(scope="module")
def service_app(tmp_path_factory):
    """Wall on AND a service token set — the Digest generator's way in (ADR-0035)."""
    with _space_app(
        tmp_path_factory.mktemp("state"),
        env={
            "SECRET_KEY": "test-secret",
            "GOOGLE_CLIENT_ID": "client-id.example",
            "ALERTS_TOKEN": "service-token",
        },
    ) as module:
        yield module


@pytest.fixture(scope="module")
def sets_app(tmp_path_factory):
    """Wall on AND per-Account storage configured — Saved sets live (ADR-0043)."""
    with _space_app(
        tmp_path_factory.mktemp("state"),
        env={
            "SECRET_KEY": "test-secret",
            "GOOGLE_CLIENT_ID": "client-id.example",
            "SUBSCRIBERS_REPO": "acme/subs",
            "SUBSCRIBERS_TOKEN": "tok",
        },
    ) as module:
        yield module


@pytest.fixture
def hub(monkeypatch):
    """A dict standing in for the Subscriptions dataset (tests/test_alerts_store.py's
    pattern), patched onto the real headstart.alerts.store the app imports."""
    import headstart.alerts.store as st

    files: dict[str, bytes] = {}
    monkeypatch.setattr(st, "_list_files", lambda repo, token: list(files))
    monkeypatch.setattr(st, "_read", lambda repo, path, token: files[path])
    monkeypatch.setattr(
        st, "_write", lambda repo, path, data, token: files.__setitem__(path, data)
    )
    monkeypatch.setattr(st, "_delete", lambda repo, path, token: files.pop(path, None))
    return files


def _signed_in(app_module, monkeypatch, email="dev@example.com"):
    monkeypatch.setattr(app_module.identity, "verify", lambda cred, cid: email)
    client = app_module.app.test_client()
    client.post("/auth/google", json={"credential": "tok"}, base_url=_HTTPS)
    return client


# ---- the sign-in wall (ADR-0042) ----

_HTTPS = "https://localhost"  # the session cookie is Secure; plain http would drop it


def test_wall_off_keeps_the_page_open(app):
    client = app.app.test_client()
    assert b"jobs indexed" in client.get("/").data
    assert client.get("/search?q=").json == []
    assert client.get("/me").json == {"auth": False, "email": None}
    # Not a 500: clearing a NullSession raises, so the route must refuse first.
    assert client.post("/signout").status_code == 503


def test_wall_on_serves_the_door_and_gates_the_api(auth_app):
    client = auth_app.app.test_client()
    page = client.get("/").data
    assert b"Sign in to search" in page
    assert b"jobs indexed" not in page
    # The door's Google button carries the real client id — a drifted placeholder would
    # ship a dead button on a page that otherwise renders fine.
    assert b"client-id.example" in page
    # …and its way out of an embedding frame. huggingface.co/spaces/… frames the app, where
    # Google's sign-in can't complete and a Lax session cookie wouldn't be sent anyway, so
    # the door offers a new tab instead. Losing this strands every visitor arriving that way.
    assert b'id="openout"' in page
    assert client.get("/search?q=x").status_code == 401
    assert client.get("/trends").status_code == 401
    assert client.post("/profile/parse", json={}).status_code == 401
    assert client.post("/subscribe", json={}).status_code == 401
    assert client.post("/signout").status_code == 401


def test_signin_flow(auth_app, monkeypatch):
    monkeypatch.setattr(
        auth_app.identity, "verify", lambda cred, cid: "dev@example.com"
    )
    client = auth_app.app.test_client()
    r = client.post("/auth/google", json={"credential": "tok"}, base_url=_HTTPS)
    assert r.status_code == 200 and r.json["email"] == "dev@example.com"
    assert client.get("/search?q=", base_url=_HTTPS).json == []
    assert b"jobs indexed" in client.get("/", base_url=_HTTPS).data
    assert client.get("/me", base_url=_HTTPS).json["email"] == "dev@example.com"
    client.post("/signout", base_url=_HTTPS)
    assert client.get("/search?q=", base_url=_HTTPS).status_code == 401


def test_bad_credential_is_401(auth_app, monkeypatch):
    def refuse(cred, cid):
        raise auth_app.identity.IdentityError("not a credential")

    monkeypatch.setattr(auth_app.identity, "verify", refuse)
    client = auth_app.app.test_client()
    r = client.post("/auth/google", json={"credential": "bad"}, base_url=_HTTPS)
    assert r.status_code == 401


def test_unsubscribe_stays_reachable_signed_out(auth_app):
    # The wall must never break a mailed link: /unsubscribe answers its own 503 here
    # (alerts unconfigured in this fixture), not the wall's 401.
    assert auth_app.app.test_client().get("/unsubscribe").status_code == 503


def test_digest_generator_reaches_search_through_the_wall(service_app):
    """The sibling of the test above, and the one that was missing.

    A mailed Digest's link must survive the wall — and so must the run that *generates*
    the Digest, which calls /search for every Subscription (ADR-0035). It is a machine
    with no Google identity to offer, so it carries the service token instead, exactly as
    /unsubscribe carries its own. Without this the alerts workflow 401s on every run.
    """
    client = service_app.app.test_client()
    assert client.get("/search?q=x").status_code == 401  # still shut to the anonymous
    ok = client.get("/search?q=", headers={"Authorization": "Bearer service-token"})
    assert ok.status_code == 200 and ok.json == []


def test_the_service_token_buys_search_and_nothing_else(service_app):
    # Scoped deliberately: the alerts run needs /search and only /search, so a leaked
    # token is not a session. Widening this is a decision, not an accident.
    client = service_app.app.test_client()
    bearer = {"Authorization": "Bearer service-token"}
    assert client.get("/trends", headers=bearer).status_code == 401
    assert client.post("/subscribe", json={}, headers=bearer).status_code == 401


def test_a_near_miss_token_is_not_a_match(service_app):
    client = service_app.app.test_client()
    for bad in (
        "Bearer service-toke",
        "Bearer service-tokenX",
        "service-token",
        "Bearer",
        # Headers decode as latin-1, and hmac.compare_digest raises TypeError on a
        # non-ASCII str — which would turn a rejected credential into an unauthenticated
        # 500 from inside before_request. Compare bytes, and this is a plain 401.
        "Bearer café",
    ):
        r = client.get("/search?q=", headers={"Authorization": bad})
        assert r.status_code == 401, f"{bad!r} got in"


def test_an_unconfigured_service_token_admits_nobody(auth_app):
    # Deny-by-default, as in alerts.access: "no token set" must mean the door is shut,
    # never that an empty or absent credential compares equal to the empty config.
    client = auth_app.app.test_client()
    for header in ("Bearer ", "Bearer x", ""):
        r = client.get("/search?q=", headers={"Authorization": header})
        assert r.status_code == 401, f"{header!r} got in with no ALERTS_TOKEN set"


# ---- Saved sets (ADR-0043) ----


def test_sets_flow_create_list_email_delete(sets_app, hub, monkeypatch):
    import json as _json

    hub["subscriptions/allowlist.json"] = b'{"allowed": ["dev@example.com"]}'
    client = _signed_in(sets_app, monkeypatch)

    # create two from "Save this search"
    r = client.post(
        "/sets",
        json={
            "name": "backend",
            "query": "backend engineer",
            "filters": {"remote": "true", "junk": "x"},
        },
        base_url=_HTTPS,
    )
    assert r.status_code == 200 and r.json["search_filters"] == {"remote": "true"}
    first = r.json
    second = client.post(
        "/sets", json={"name": "ML", "query": "ML engineer"}, base_url=_HTTPS
    ).json
    assert [s["name"] for s in client.get("/sets", base_url=_HTTPS).json] == [
        "backend",
        "ML",
    ]

    # email ON projects the Subscription from the set
    r = client.post(f"/sets/{first['id']}/email", json={"on": True}, base_url=_HTTPS)
    assert r.status_code == 200 and r.json["emails"] is True
    sub_path = f"subscriptions/{sets_app.subscription_id('dev@example.com')}.json"
    sub = _json.loads(hub[sub_path])
    assert sub["query"] == "backend engineer"
    watermark, token = sub["watermark"], sub["unsubscribe_token"]

    # moving email to the other set flips both flags and RE-projects, keeping machinery
    client.post(f"/sets/{second['id']}/email", json={"on": True}, base_url=_HTTPS)
    listed = {s["name"]: s["emails"] for s in client.get("/sets", base_url=_HTTPS).json}
    assert listed == {"backend": False, "ML": True}
    sub = _json.loads(hub[sub_path])
    assert sub["query"] == "ML engineer"
    assert (sub["watermark"], sub["unsubscribe_token"]) == (watermark, token)

    # editing the emailing set re-projects in the same request
    client.post(
        "/sets",
        json={"id": second["id"], "name": "ML", "query": "ML infra"},
        base_url=_HTTPS,
    )
    assert _json.loads(hub[sub_path])["query"] == "ML infra"

    # deleting the emailing set removes the Subscription — nothing keeps mailing
    client.delete(f"/sets/{second['id']}", base_url=_HTTPS)
    assert sub_path not in hub
    assert [s["name"] for s in client.get("/sets", base_url=_HTTPS).json] == ["backend"]


def test_email_on_is_invite_only(sets_app, hub, monkeypatch):
    hub["subscriptions/allowlist.json"] = b'{"allowed": ["someone-else@example.com"]}'
    client = _signed_in(sets_app, monkeypatch)
    made = client.post("/sets", json={"name": "x", "query": "y"}, base_url=_HTTPS).json
    r = client.post(f"/sets/{made['id']}/email", json={"on": True}, base_url=_HTTPS)
    assert r.status_code == 403
    assert client.get("/sets", base_url=_HTTPS).json[0]["emails"] is False


def test_sets_are_capped(sets_app, hub, monkeypatch):
    client = _signed_in(sets_app, monkeypatch)
    for i in range(sets_app.MAX_SETS):
        assert (
            client.post(
                "/sets", json={"name": f"s{i}", "query": "q"}, base_url=_HTTPS
            ).status_code
            == 200
        )
    r = client.post("/sets", json={"name": "one more", "query": "q"}, base_url=_HTTPS)
    assert r.status_code == 400


def test_sets_require_wall_and_storage(auth_app, monkeypatch):
    # wall on but no SUBSCRIBERS storage: the endpoints answer 503, the tab stays "soon"
    client = _signed_in(auth_app, monkeypatch)
    assert client.get("/sets", base_url=_HTTPS).status_code == 503
    assert b'data-tab="matches"' not in client.get("/", base_url=_HTTPS).data


def test_sets_are_gated_by_the_wall(sets_app):
    assert sets_app.app.test_client().get("/sets").status_code == 401


def test_toggle_off_semantics_and_fresh_machinery_on_reenable(
    sets_app, hub, monkeypatch
):
    import json as _json

    hub["subscriptions/allowlist.json"] = b'{"allowed": ["dev@example.com"]}'
    client = _signed_in(sets_app, monkeypatch)
    a = client.post("/sets", json={"name": "a", "query": "qa"}, base_url=_HTTPS).json
    b = client.post("/sets", json={"name": "b", "query": "qb"}, base_url=_HTTPS).json
    client.post(f"/sets/{a['id']}/email", json={"on": True}, base_url=_HTTPS)
    sub_path = f"subscriptions/{sets_app.subscription_id('dev@example.com')}.json"
    token_before = _json.loads(hub[sub_path])["unsubscribe_token"]

    # OFF on a set that never carried email must NOT stop the mail (stale-tab scenario)
    client.post(f"/sets/{b['id']}/email", json={"on": False}, base_url=_HTTPS)
    assert sub_path in hub

    # editing the NON-emailing set leaves the Subscription untouched
    client.post(
        "/sets", json={"id": b["id"], "name": "b", "query": "changed"}, base_url=_HTTPS
    )
    assert _json.loads(hub[sub_path])["query"] == "qa"

    # OFF on the emailing set removes it; ON again mints fresh machinery
    client.post(f"/sets/{a['id']}/email", json={"on": False}, base_url=_HTTPS)
    assert sub_path not in hub
    client.post(f"/sets/{a['id']}/email", json={"on": True}, base_url=_HTTPS)
    assert _json.loads(hub[sub_path])["unsubscribe_token"] != token_before


def test_unsubscribe_clears_the_emailing_flag(sets_app, hub, monkeypatch):
    import json as _json

    hub["subscriptions/allowlist.json"] = b'{"allowed": ["dev@example.com"]}'
    client = _signed_in(sets_app, monkeypatch)
    made = client.post("/sets", json={"name": "a", "query": "qa"}, base_url=_HTTPS).json
    client.post(f"/sets/{made['id']}/email", json={"on": True}, base_url=_HTTPS)
    sub_path = f"subscriptions/{sets_app.subscription_id('dev@example.com')}.json"
    sub = _json.loads(hub[sub_path])

    r = client.get(f"/unsubscribe?id={sub['id']}&token={sub['unsubscribe_token']}")
    assert r.status_code == 200 and sub_path not in hub
    # the set no longer claims ✉ on, so a later edit cannot silently re-subscribe
    assert client.get("/sets", base_url=_HTTPS).json[0]["emails"] is False
    client.post(
        "/sets",
        json={"id": made["id"], "name": "a", "query": "edited"},
        base_url=_HTTPS,
    )
    assert sub_path not in hub


def test_subscribe_refuses_while_sets_are_live(sets_app, hub, monkeypatch):
    client = _signed_in(sets_app, monkeypatch)
    r = client.post(
        "/subscribe", json={"credential": "x", "query": "q"}, base_url=_HTTPS
    )
    assert r.status_code == 409


def test_presets_subscription_is_adopted_as_the_emailing_set(
    sets_app, hub, monkeypatch
):
    import json as _json

    st = sets_app  # the loaded app module re-exports store names it imported
    sub = st.Subscription.create(
        "dev@example.com", "backend engineer", {"remote": "true"}
    )
    hub[f"subscriptions/{sub.id}.json"] = _json.dumps(sub.to_dict()).encode()

    client = _signed_in(sets_app, monkeypatch)
    listed = client.get("/sets", base_url=_HTTPS).json
    assert len(listed) == 1
    assert listed[0]["emails"] is True
    assert listed[0]["query"] == "backend engineer"
    assert listed[0]["search_filters"] == {"remote": "true"}
    # idempotent: a second read adopts nothing new
    assert len(client.get("/sets", base_url=_HTTPS).json) == 1


# ---- Profile (ADR-0041) ----

_EXTRACTION = {
    "query": "backend engineer, Python",
    "title": "SDE II",
    "years": 4,
    "skills": ["Python", "Go"],
    "roles": ["SDE II at Acme"],
    "education": "B.Tech",
    "location": "Pune, India",
}


def _router_answers(app_module, monkeypatch, payload=None):
    import json as _json

    reply = _json.dumps(payload or _EXTRACTION)
    monkeypatch.setattr(app_module.llm_router, "ask", lambda prompt: reply)


def test_profile_get_save_roundtrip_and_counter_discipline(sets_app, hub, monkeypatch):
    client = _signed_in(sets_app, monkeypatch)
    blank = client.get("/profile", base_url=_HTTPS).json
    assert blank["query"] == "" and blank["parses_left"] == sets_app.MAX_PARSES

    r = client.post(
        "/profile",
        json={
            "query": "backend engineer",
            "years": "4",
            "skills": "Python, Go",
            "parses_used": 99,  # must be ignored — nobody refills their own cap
        },
        base_url=_HTTPS,
    )
    assert r.status_code == 200
    assert r.json["query"] == "backend engineer" and r.json["years"] == 4
    assert r.json["parses_used"] == 0
    assert client.get("/profile", base_url=_HTTPS).json["skills"] == "Python, Go"


def test_hand_saved_query_is_scrubbed_like_the_extracted_one(
    sets_app, hub, monkeypatch
):
    # The Query contract holds whichever door the sentence came through (ADR-0041):
    # a hand-edited save must not smuggle years/salary into the ranking sentence.
    client = _signed_in(sets_app, monkeypatch)
    r = client.post(
        "/profile",
        json={
            "query": "backend engineer, 7+ years of experience, $200k salary, Python"
        },
        base_url=_HTTPS,
    )
    assert r.status_code == 200
    assert r.json["query"] == "backend engineer, Python"


def test_unreadable_counter_fails_closed_not_open(sets_app, hub, monkeypatch):
    # A transient failure reading the counter must never look like "0 used" — that
    # would reset the lifetime cap. The routes answer 503 instead.
    client = _signed_in(sets_app, monkeypatch)
    account = sets_app.subscription_id("dev@example.com")
    hub[f"profiles/{account}.parses.json"] = b"not json"
    assert client.get("/profile", base_url=_HTTPS).status_code == 503
    r = client.post("/profile/parse", json={"text": "r"}, base_url=_HTTPS)
    assert r.status_code == 503


def test_parse_fills_the_profile_and_spends_a_read(sets_app, hub, monkeypatch):
    client = _signed_in(sets_app, monkeypatch)
    _router_answers(sets_app, monkeypatch)
    r = client.post("/profile/parse", json={"text": "my résumé"}, base_url=_HTTPS)
    assert r.status_code == 200
    assert r.json["query"] == "backend engineer, Python"
    assert r.json["skills"] == "Python, Go"  # lists land as one editable line
    assert r.json["parses_left"] == sets_app.MAX_PARSES - 1


def test_parse_cap_is_lifetime_and_survives_delete(sets_app, hub, monkeypatch):
    client = _signed_in(sets_app, monkeypatch)
    _router_answers(sets_app, monkeypatch)
    for _ in range(sets_app.MAX_PARSES):
        r = client.post("/profile/parse", json={"text": "r"}, base_url=_HTTPS)
        assert r.status_code == 200
    assert (
        client.post("/profile/parse", json={"text": "r"}, base_url=_HTTPS).status_code
        == 400
    )
    # deleting blanks the career data but keeps the counter — the cap must not reset
    assert client.delete("/profile", base_url=_HTTPS).status_code == 200
    after = client.get("/profile", base_url=_HTTPS).json
    assert after["query"] == "" and after["parses_left"] == 0
    assert (
        client.post("/profile/parse", json={"text": "r"}, base_url=_HTTPS).status_code
        == 400
    )


def test_failed_extraction_still_spends_a_read(sets_app, hub, monkeypatch):
    # The router answered garbage — the call was made, so it counts (spend bound, ADR-0041)
    client = _signed_in(sets_app, monkeypatch)
    monkeypatch.setattr(sets_app.llm_router, "ask", lambda prompt: "not json at all")
    r = client.post("/profile/parse", json={"text": "r"}, base_url=_HTTPS)
    assert r.status_code == 502
    assert (
        client.get("/profile", base_url=_HTTPS).json["parses_left"]
        == sets_app.MAX_PARSES - 1
    )


def test_empty_paste_refuses_before_the_router_and_spends_nothing(
    sets_app, hub, monkeypatch
):
    client = _signed_in(sets_app, monkeypatch)  # ask still raises RouterUnavailable
    r = client.post("/profile/parse", json={"text": "   "}, base_url=_HTTPS)
    assert r.status_code == 400
    assert (
        client.get("/profile", base_url=_HTTPS).json["parses_left"]
        == sets_app.MAX_PARSES
    )


def test_router_down_is_503_and_spends_nothing(sets_app, hub, monkeypatch):
    client = _signed_in(sets_app, monkeypatch)  # the fixture's ask raises
    r = client.post("/profile/parse", json={"text": "résumé"}, base_url=_HTTPS)
    assert r.status_code == 503
    assert (
        client.get("/profile", base_url=_HTTPS).json["parses_left"]
        == sets_app.MAX_PARSES
    )


def test_profile_requires_wall_and_storage(auth_app, monkeypatch):
    client = _signed_in(auth_app, monkeypatch)
    assert client.get("/profile", base_url=_HTTPS).status_code == 503
    assert b'data-tab="profile"' not in client.get("/", base_url=_HTTPS).data


def test_profile_is_gated_by_the_wall(sets_app):
    assert sets_app.app.test_client().get("/profile").status_code == 401


def test_profile_tab_appears_when_configured(sets_app, hub, monkeypatch):
    client = _signed_in(sets_app, monkeypatch)
    assert b'data-tab="profile"' in client.get("/", base_url=_HTTPS).data


# ---- Saved jobs (ADR-0044) ----


def _star_payload(job_id="greenhouse:acme:123", **over):
    body = {
        "job_id": job_id,
        "title": "Backend Engineer",
        "company": "acme",
        "url": "https://boards.greenhouse.io/acme/jobs/123",
        "location": "Bengaluru",
        "remote": True,
        "salary": "₹30L",
    }
    body.update(over)
    return body


def test_saved_flow_star_list_unstar(sets_app, hub, monkeypatch):
    client = _signed_in(sets_app, monkeypatch)
    r = client.post("/saved", json=_star_payload(), base_url=_HTTPS)
    assert r.status_code == 200
    starred = r.json
    assert starred["job_id"] == "greenhouse:acme:123"
    assert starred["open"] is True and starred["remote"] is True

    # the list annotates each record against the live index
    monkeypatch.setattr(
        sets_app._searcher, "indexed", lambda ids: {"greenhouse:acme:123"}
    )
    assert [j["open"] for j in client.get("/saved", base_url=_HTTPS).json] == [True]

    # evicted from the index → "closed", but the display copy still lists (ADR-0042)
    monkeypatch.setattr(sets_app._searcher, "indexed", lambda ids: set())
    listed = client.get("/saved", base_url=_HTTPS).json
    assert listed[0]["open"] is False and listed[0]["title"] == "Backend Engineer"

    assert client.delete(f"/saved/{starred['id']}", base_url=_HTTPS).status_code == 200
    assert client.get("/saved", base_url=_HTTPS).json == []


def test_restar_overwrites_and_refreshes_the_copy(sets_app, hub, monkeypatch):
    client = _signed_in(sets_app, monkeypatch)
    client.post("/saved", json=_star_payload(title="Old"), base_url=_HTTPS)
    client.post("/saved", json=_star_payload(title="New"), base_url=_HTTPS)
    monkeypatch.setattr(sets_app._searcher, "indexed", lambda ids: set(ids))
    assert [j["title"] for j in client.get("/saved", base_url=_HTTPS).json] == ["New"]


def test_saved_are_capped_but_a_restar_never_hits_the_cap(sets_app, hub, monkeypatch):
    client = _signed_in(sets_app, monkeypatch)
    for i in range(sets_app.MAX_SAVED):
        r = client.post("/saved", json=_star_payload(f"a:b:{i}"), base_url=_HTTPS)
        assert r.status_code == 200
    r = client.post("/saved", json=_star_payload("one:too:many"), base_url=_HTTPS)
    assert r.status_code == 400
    # an already-starred job may still be re-starred (overwritten) at the cap
    r = client.post("/saved", json=_star_payload("a:b:0"), base_url=_HTTPS)
    assert r.status_code == 200


def test_star_requires_id_and_title(sets_app, hub, monkeypatch):
    client = _signed_in(sets_app, monkeypatch)
    assert (
        client.post("/saved", json={"title": "x"}, base_url=_HTTPS).status_code == 400
    )
    assert (
        client.post("/saved", json={"job_id": "a:b:1"}, base_url=_HTTPS).status_code
        == 400
    )


def test_unstar_unknown_record_is_404(sets_app, hub, monkeypatch):
    client = _signed_in(sets_app, monkeypatch)
    assert client.delete("/saved/" + "a" * 16, base_url=_HTTPS).status_code == 404


def test_saved_require_wall_and_storage(auth_app, monkeypatch):
    # wall on but no SUBSCRIBERS storage: the endpoints answer 503, the tab stays "soon"
    client = _signed_in(auth_app, monkeypatch)
    assert client.get("/saved", base_url=_HTTPS).status_code == 503
    assert b'data-tab="saved"' not in client.get("/", base_url=_HTTPS).data


def test_saved_are_gated_by_the_wall(sets_app):
    assert sets_app.app.test_client().get("/saved").status_code == 401


def test_saved_tab_appears_when_configured(sets_app, hub, monkeypatch):
    client = _signed_in(sets_app, monkeypatch)
    assert b'data-tab="saved"' in client.get("/", base_url=_HTTPS).data


def test_sets_keep_seen_within_but_the_projection_drops_it(sets_app, hub, monkeypatch):
    import json as _json

    hub["subscriptions/allowlist.json"] = b'{"allowed": ["dev@example.com"]}'
    client = _signed_in(sets_app, monkeypatch)
    made = client.post(
        "/sets",
        json={
            "name": "fresh",
            "query": "q",
            "filters": {"seen_within": "24", "remote": "true"},
        },
        base_url=_HTTPS,
    ).json
    assert made["search_filters"] == {"seen_within": "24", "remote": "true"}
    client.post(f"/sets/{made['id']}/email", json={"on": True}, base_url=_HTTPS)
    sub_path = f"subscriptions/{sets_app.subscription_id('dev@example.com')}.json"
    assert _json.loads(hub[sub_path])["search_filters"] == {"remote": "true"}


# ---- /trends (ADR-0040/0051): metrics, totals, and the watch drill ----------------------

_T1, _T2, _T3 = (
    "2026-08-11T01:00:00+00:00",  # pre-ADR-0051 run: stock only (migrated rows)
    "2026-08-12T01:00:00+00:00",
    "2026-08-13T01:00:00+00:00",
)


def _trends_csv(state: Path) -> None:
    rows = [
        "ts,version,metric,family,band,count",
        f"{_T1},2,stock,software-engineering,mid,100",
        f"{_T1},2,stock,ai-ml,mid,50",
        f"{_T1},2,stock,non-tech,all,25",
        f"{_T2},2,stock,software-engineering,mid,110",
        f"{_T2},2,stock,ai-ml,mid,55",
        f"{_T2},2,stock,watch:fde,mid,7",
        f"{_T2},2,stock,non-tech,all,27",
        f"{_T2},2,new,software-engineering,mid,12",
        f"{_T2},2,new,watch:fde,mid,2",
        f"{_T3},2,stock,software-engineering,mid,120",
        f"{_T3},2,stock,ai-ml,mid,60",
        f"{_T3},2,stock,watch:fde,mid,8",
        f"{_T3},2,stock,non-tech,all,30",
        f"{_T3},2,new,software-engineering,mid,9",
        f"{_T3},2,new,ai-ml,mid,4",
        f"{_T3},2,new,watch:fde,mid,1",
    ]
    out = state / "data" / "state" / "role_trends.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(rows) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def trends_app(tmp_path_factory):
    """The app with a trends ledger. `_STATE` is the hardcoded `/app/state`, so the CSV can't
    ride the snapshot stub — instead the module's own loader is pointed at the fixture file
    after import, which still exercises the real parsing (metric default included)."""
    state = tmp_path_factory.mktemp("state")
    _trends_csv(state)
    # The wall pinned OFF explicitly ("" is falsy in _AUTH_ON): module-scoped fixtures from
    # earlier in this file hold their env until teardown, so without this the trends app can
    # inherit a live wall depending on test order and answer every request 401.
    with _space_app(state, env={"SECRET_KEY": "", "GOOGLE_CLIENT_ID": ""}) as module:
        module._TRENDS = module._load_trends(
            state / "data" / "state" / "role_trends.csv"
        )
        module._WATCH = {
            "watch:fde": {
                "label": "Forward Deployed Engineer",
                "parent": "software-engineering",
            }
        }
        yield module


def test_trends_default_view_excludes_watch_and_carries_totals(trends_app):
    d = trends_app.app.test_client().get("/trends").get_json()
    names = {s["name"] for s in d["series"]}
    assert names == {"software-engineering", "ai-ml"}  # no watch:, no non-tech
    # totals are the whole served table per stamp — families + non-tech, never watch rows,
    # which re-count Jobs already counted in their family (ADR-0051's share denominator)
    assert d["totals"] == [175, 192, 210]
    assert d["watch_parents"] == ["software-engineering"]


def test_trends_new_metric_distinguishes_zero_from_not_measured(trends_app):
    d = trends_app.app.test_client().get("/trends?metric=new").get_json()
    by_name = {s["name"]: s for s in d["series"]}
    # T1 predates the metric: a gap, not a zero. T2 measured new but ai-ml had none: a true 0.
    assert by_name["ai-ml"]["points"] == [None, 0, 4]
    assert by_name["software-engineering"]["points"] == [None, 12, 9]


def test_trends_roles_split_serves_the_watchlist(trends_app):
    d = (
        trends_app.app.test_client()
        .get("/trends?family=software-engineering&split=roles")
        .get_json()
    )
    assert [s["name"] for s in d["series"]] == ["watch:fde"]
    assert d["series"][0]["label"] == "Forward Deployed Engineer"
    assert d["series"][0]["points"] == [None, 7, 8]


def test_trends_band_split_is_unchanged_by_the_watchlist(trends_app):
    d = (
        trends_app.app.test_client()
        .get("/trends?family=software-engineering")
        .get_json()
    )
    assert [s["name"] for s in d["series"]] == ["mid"]  # bands, as before ADR-0051


def test_trends_rejects_unknown_metric_and_split(trends_app):
    """Same axis, same posture — a silently-ignored `split` would answer a question nobody
    asked and look like data, which is how the truncation bug read too."""
    client = trends_app.app.test_client()
    assert client.get("/trends?metric=x").status_code == 400
    assert client.get("/trends?family=software-engineering&split=x").status_code == 400


def test_trends_since_excludes_earlier_stamps(trends_app):
    # quote(): the timestamp's literal '+' must survive as '+', not decode to a space the way
    # a raw, un-urlencoded '+' would under application/x-www-form-urlencoded rules — the same
    # encoding the browser's URLSearchParams.set() (used in app.js) already handles correctly.
    d = trends_app.app.test_client().get(f"/trends?since={quote(_T2)}").get_json()
    assert d["stamps"] == [_T2, _T3]
    assert d["totals"] == [192, 210]  # T1's 175 dropped with it


def test_trends_until_excludes_later_stamps(trends_app):
    d = trends_app.app.test_client().get(f"/trends?until={quote(_T2)}").get_json()
    assert d["stamps"] == [_T1, _T2]
    assert d["totals"] == [175, 192]


def test_trends_since_and_until_together_narrow_to_one_stamp(trends_app):
    d = (
        trends_app.app.test_client()
        .get(f"/trends?since={quote(_T2)}&until={quote(_T2)}")
        .get_json()
    )
    assert d["stamps"] == [_T2]


def test_trends_since_normalises_the_browsers_millisecond_z_format(trends_app):
    """The browser sends Date.toISOString() output — milliseconds, a trailing 'Z' — while the
    ledger stores whole-second '+00:00' stamps. A raw string compare of the two would exclude a
    `since` naming the EXACT SAME INSTANT as a stamp, because '.' (0x2E) sorts after '+' (0x2B):
    '...:00+00:00' < '...:00.000Z' even though they mean the same moment — this is the genuine
    regression guard (fails without _norm_stamp). `until`'s assertion documents the same
    required contract, but is NOT independently discriminating against this specific bug: for
    '<=', that same '.' > '+' skew never produces a wrongful exclusion at an exact instant (only
    '>=' can), so a naive `ts <= until_raw` happens to still read True here. Kept anyway — it
    pins the correct behaviour and would catch a differently-shaped regression in _norm_stamp
    itself."""
    client = trends_app.app.test_client()
    exact_instant = quote("2026-08-12T01:00:00.000Z")  # T2, browser-shaped
    since_only = client.get(f"/trends?since={exact_instant}").get_json()
    assert _T2 in since_only["stamps"], "an exact-instant since must include that stamp"
    until_only = client.get(f"/trends?until={exact_instant}").get_json()
    assert _T2 in until_only["stamps"], "an exact-instant until must include that stamp"


def test_trends_rejects_a_malformed_range_bound(trends_app):
    r = trends_app.app.test_client().get("/trends?since=not-a-date")
    assert r.status_code == 400


def test_trends_range_outside_the_data_returns_empty_not_503(trends_app):
    """The ledger itself is not empty — only the requested window is — so this must read as
    'nothing in range', not as the no-ledger-yet 503 the bare route returns before any data
    lands."""
    r = trends_app.app.test_client().get(
        f"/trends?since={quote('2030-01-01T00:00:00+00:00')}"
    )
    assert r.status_code == 200
    d = r.get_json()
    assert d["stamps"] == []
    assert d["series"] == []
