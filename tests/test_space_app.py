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

import pytest

pytest.importorskip("flask")  # in [dev] so this runs in CI; guards a bare env

APP = Path(__file__).resolve().parents[1] / "deploy" / "hf-space" / "app.py"


def _module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


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
            "llm_router", RouterUnavailable=type("RU", (Exception,), {})
        ),
        "resume_query": _module(
            "resume_query",
            EmptyQuery=type("EQ", (Exception,), {}),
            ResumeError=type("RE", (Exception,), {}),
            ResumeTooLong=type("RTL", (Exception,), {}),
            query_for=lambda *a, **k: "",
        ),
    }
    # The Space imports the alerts package and the search module flat, as deploy-space.yml
    # lays them down — the real modules, not fakes, so the wiring under test is real.
    import headstart.alerts.access as _access
    import headstart.alerts.identity as _identity
    import headstart.alerts.store as _store
    import headstart.search as _search

    stubs["alerts"] = _module("alerts", access=_access, identity=_identity)
    stubs["alerts.store"] = _store
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
    assert client.get("/search?q=x").status_code == 401
    assert client.get("/trends").status_code == 401
    assert client.post("/resume-to-query", json={}).status_code == 401
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
