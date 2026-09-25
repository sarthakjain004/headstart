"""The Space app's wall and routes — `deploy/hf-space/app.py` (ADR-0035, ADR-0042).

The filter/search logic itself lives in `headstart.search` and is tested in
`tests/test_search.py`; what's left here is what only the app owns — the sign-in wall and
the wiring — which had no coverage anywhere, because `deploy/` sits outside `testpaths`
and importing the app pulls in a model download, a SentenceTransformer and a LanceDB
table. So the heavy ML/network deps are stubbed in `sys.modules` and the module is loaded
from its path — the same importlib trick `tests/test_check_liveness.py` uses for
`scripts/`. Everything else app.py imports (`headstart.search`, `.facets`, `.fx`, `.geo`,
`.profile_extract`, `.alerts.*`) is the real package: the Space installs `headstart` rather
than laying its modules down flat (ADR-0153), so app.py imports it exactly the way this
test file and `scripts/ui/serve.py` already did, and there is nothing left to fake for it —
only `llm_router.ask` is defaulted off below, so a router-less test environment doesn't
attempt a real network call. Auth requests ride `base_url="https://localhost"` because the
session cookie is `Secure` and the test client honours that over plain http.
"""

import csv
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import types
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from headstart import trend_history, trend_netting
from headstart.llm_router import RouterUnavailable

pytest.importorskip("flask")  # in [dev] so this runs in CI; guards a bare env

APP = Path(__file__).resolve().parents[1] / "deploy" / "hf-space" / "app.py"


def _module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def _no_router(prompt):
    """The default ``ask`` for every app fixture — no router in the test sandbox, so this
    stands in for the real network failure without ever attempting one."""
    raise RouterUnavailable("no router in tests")


class _Table:
    """Enough LanceDB for import time: an ats scan, a schema and a row count.

    Rows carry every field `JobSearch.run` projects (ADR-0074's browse path reaches this
    fake through `/search?q=` the same as a real query does now, where the old empty-query
    shortcut used to return `[]` before ever touching the table) — this file only tests the
    wall/wiring, not ranking, so the row content itself doesn't matter beyond being complete.
    """

    schema = types.SimpleNamespace(names=["ats", "title", "first_seen"])

    def search(self, *a, **k):
        return self

    def metric(self, *a, **k):
        return self

    def select(self, cols, *a, **k):
        # lancedb rejects a tuple (`columns must be a list or a dictionary`); this fake
        # took anything, which is how a browse-path 500 stayed green in the suite.
        assert isinstance(cols, (list, dict)), f"lancedb rejects {type(cols).__name__}"
        return self

    def where(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def offset(self, *a, **k):
        return self

    def to_list(self):
        return [
            {
                "_distance": 0.1,
                "ats": "greenhouse",
                "title": "Backend Engineer",
                "company": "Acme",
                "location": "Berlin",
                "remote": True,
                "employment_type": "full-time",
                "min_years": None,
                "salary": None,
                "posted_at": "2026-08-01",
                "first_seen": "2026-08-10T00:00:00+00:00",
                "url": "https://example.test/1",
                "id": "greenhouse:acme:1",
            },
            {
                "_distance": 0.2,
                "ats": "lever",
                "title": "Frontend Engineer",
                "company": "Beta",
                "location": "Remote",
                "remote": True,
                "employment_type": "full-time",
                "min_years": None,
                "salary": None,
                "posted_at": "2026-08-02",
                "first_seen": "2026-08-11T00:00:00+00:00",
                "url": "https://example.test/2",
                "id": "lever:beta:2",
            },
        ]

    def count_rows(self, filter=None):
        # Coverage (ADR-0113) counts with a filter; everything else counts the table. One
        # of the two rows is given each field so a percentage that is neither 0 nor 100
        # comes back — a fake that answered `2` to everything would pass a renderer that
        # had silently divided by the wrong total.
        return 2 if filter is None else 1


class _Vector:
    def astype(self, _dtype):
        return self


class _Model:
    def encode(self, _texts, *, normalize_embeddings):
        assert normalize_embeddings
        return [_Vector()]


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
            "sentence_transformers", SentenceTransformer=lambda *a, **k: _Model()
        ),
        "huggingface_hub": _module(
            "huggingface_hub", snapshot_download=lambda *a, **k: str(state)
        ),
    }
    # Only the ML/network deps above are faked. Everything app.py imports from `headstart`
    # (facets, fx, geo, profile_extract, search, alerts.*) is the real package (ADR-0153), so
    # there is nothing left to substitute for it — the wiring under test is real end to end.
    saved = {name: sys.modules.get(name) for name in stubs}
    saved_env = {key: os.environ.get(key) for key in (env or {})}
    sys.modules.update(stubs)
    os.environ.update(env or {})
    try:
        spec = importlib.util.spec_from_file_location("space_app", APP)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # `module.llm_router` is the real, shared `headstart.llm_router` (every app fixture in
        # this file imports the same singleton) — default `ask` off so a router-less test
        # environment can't attempt a real network call, and restore it so this fixture's
        # teardown never leaves a different default behind for a sibling fixture still in use.
        real_ask = module.llm_router.ask
        module.llm_router.ask = _no_router
        try:
            yield module
        finally:
            module.llm_router.ask = real_ask
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
    monkeypatch.setattr(
        st, "_list_files", lambda repo, token, revision=None: list(files)
    )
    monkeypatch.setattr(st, "_read", lambda repo, path, token: files[path])
    monkeypatch.setattr(st, "_head_revision", lambda *args: "fixture-revision")
    monkeypatch.setattr(
        st, "_read_at_revision", lambda repo, path, token, revision: files[path]
    )
    monkeypatch.setattr(st, "_is_absent", lambda exc: isinstance(exc, KeyError))
    monkeypatch.setattr(
        st, "_write", lambda repo, path, data, token: files.__setitem__(path, data)
    )
    monkeypatch.setattr(st, "_delete", lambda repo, path, token: files.pop(path, None))

    def commit(repo, changes, expected, token, revision=None):
        if any(files.get(path) != before for path, before in expected.items()):
            raise st.StoreConflict("concurrent edit")
        for path, data in changes.items():
            if data is None:
                files.pop(path, None)
            else:
                files[path] = data

    monkeypatch.setattr(st, "_commit", commit)
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
    # An empty query browses (ADR-0074) rather than returning nothing — asserting a real
    # response came back is enough here; the browse behavior itself is test_search.py's job.
    assert len(client.get("/search?q=").json) == 2
    assert client.get("/me").json == {"auth": False, "email": None}
    # Not a 500: clearing a NullSession raises, so the route must refuse first.
    assert client.post("/signout").status_code == 503


def test_wall_on_serves_the_door_and_gates_the_api(auth_app):
    client = auth_app.app.test_client()
    page = client.get("/").data
    assert b"Sign in to search" in page
    # Not "jobs indexed": the door itself now says "tech jobs indexed right now" (ADR-0112).
    # The tab shell is what only the signed-in page has.
    assert b'data-tab="search"' not in page
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
    assert (
        len(client.get("/search?q=", base_url=_HTTPS).json) == 2
    )  # browses (ADR-0074)
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


def test_privacy_policy_is_public_and_linked_from_the_door(auth_app):
    # Google's OAuth consent screen needs a privacy-policy URL a stranger can open, so the
    # wall must not gate it. It points at the one canonical copy in the repository.
    client = auth_app.app.test_client()
    r = client.get("/privacy")
    assert r.status_code == 302
    assert r.headers["Location"] == f"{auth_app._REPO}/blob/main/PRIVACY.md"
    assert (Path(__file__).resolve().parents[1] / "PRIVACY.md").is_file()
    assert b'href="/privacy"' in client.get("/").data


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
    assert ok.status_code == 200 and len(ok.json) == 2  # browses (ADR-0074)


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


def test_failed_email_projection_cannot_leave_partially_toggled_sets(
    sets_app, hub, monkeypatch
):
    from headstart.alerts.store import StoreUnavailable

    hub["subscriptions/allowlist.json"] = b'{"allowed": ["dev@example.com"]}'
    client = _signed_in(sets_app, monkeypatch)
    first = client.post(
        "/sets", json={"name": "a", "query": "backend"}, base_url=_HTTPS
    ).json
    second = client.post(
        "/sets", json={"name": "b", "query": "frontend"}, base_url=_HTTPS
    ).json
    assert (
        client.post(
            f"/sets/{first['id']}/email", json={"on": True}, base_url=_HTTPS
        ).status_code
        == 200
    )
    before = dict(hub)

    def unavailable(*args, **kwargs):
        raise StoreUnavailable("projection unavailable")

    monkeypatch.setattr(sets_app, "_project_subscription", unavailable)
    response = client.post(
        f"/sets/{second['id']}/email", json={"on": True}, base_url=_HTTPS
    )
    assert response.status_code == 503
    assert hub == before


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


def test_a_non_ascii_unsubscribe_token_is_a_404_not_a_500(sets_app, hub, monkeypatch):
    # compare_digest raises TypeError on a non-ASCII str, so the query-string token has to
    # be compared as bytes — as _service_caller already does for the bearer token.
    import json as _json

    hub["subscriptions/allowlist.json"] = b'{"allowed": ["dev@example.com"]}'
    client = _signed_in(sets_app, monkeypatch)
    made = client.post("/sets", json={"name": "a", "query": "qa"}, base_url=_HTTPS).json
    client.post(f"/sets/{made['id']}/email", json={"on": True}, base_url=_HTTPS)
    sub_path = f"subscriptions/{sets_app.subscription_id('dev@example.com')}.json"
    sub = _json.loads(hub[sub_path])

    r = client.get(f"/unsubscribe?id={sub['id']}&token=%C3%A9")

    assert r.status_code == 404 and sub_path in hub


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


# ---- Company prefs (ADR-0171) ----


def _stored_companies(app_module, hub, followed=(), hidden=()):
    import json as _json

    account = app_module.subscription_id("dev@example.com")
    path = f"companies/{account}.json"
    hub[path] = _json.dumps(
        {"account": account, "followed": list(followed), "hidden": list(hidden)}
    ).encode()
    return path


def test_a_company_click_during_a_failed_read_never_blanks_the_lists(
    sets_app, hub, monkeypatch
):
    # `get_companies` fails open for search, so the write that follows it must not treat
    # "unreadable" as "nothing stored" and replace the Account's lists with one entry.
    import headstart.alerts.store as st

    path = _stored_companies(
        sets_app, hub, followed=["greenhouse:acme", "lever:beta"], hidden=["ashby:c"]
    )
    before = hub[path]
    real_read = st._read

    def flaky(repo, name, token):
        if name == path:
            raise OSError("Hub timed out")
        return real_read(repo, name, token)

    monkeypatch.setattr(st, "_read", flaky)
    client = _signed_in(sets_app, monkeypatch)

    r = client.post(
        "/companies", json={"board": "lever:delta", "action": "follow"}, base_url=_HTTPS
    )

    assert r.status_code == 503
    assert hub[path] == before


def test_a_company_click_racing_another_cannot_silently_drop_it(
    sets_app, hub, monkeypatch
):
    # Two clicks in flight read the same record; the second write must not erase the
    # first. Simulated deterministically: this request reads the record as it was before
    # another click's hide landed.
    import headstart.alerts.store as st

    path = _stored_companies(sets_app, hub, hidden=["lever:a"])
    stale = hub[path]
    _stored_companies(sets_app, hub, hidden=["lever:a", "lever:b"])
    real_read = st._read
    monkeypatch.setattr(
        st,
        "_read",
        lambda repo, name, token: (
            stale if name == path else real_read(repo, name, token)
        ),
    )
    client = _signed_in(sets_app, monkeypatch)

    r = client.post(
        "/companies", json={"board": "lever:c", "action": "hide"}, base_url=_HTTPS
    )

    assert r.status_code == 409
    assert b"lever:b" in hub[path]


def test_a_company_click_is_stored(sets_app, hub, monkeypatch):
    path = _stored_companies(sets_app, hub, followed=["greenhouse:acme"])
    client = _signed_in(sets_app, monkeypatch)

    r = client.post(
        "/companies", json={"board": "lever:b", "action": "hide"}, base_url=_HTTPS
    )

    assert r.status_code == 200
    assert r.json == {
        "followed": ["greenhouse:acme"],
        "hidden": ["lever:b"],
        "hidden_companies": 1,
    }
    assert client.get("/companies", base_url=_HTTPS).json == r.json
    assert b"lever:b" in hub[path]


def test_one_board_clicked_follows_or_hides_its_whole_company(
    sets_app, hub, monkeypatch
):
    """ADR-0230: a Hot row is a company, so its Follow is every Board of it, and so is a result
    card's hide. The hidden count is of companies: Boeing's Boards are one."""
    boeing = ("workday:boeing/EXTERNAL_CAREERS", "workday:boeing/Eng")
    monkeypatch.setattr(
        sets_app, "_COMPANY_BOARDS", {board.lower(): boeing for board in boeing}
    )
    _stored_companies(sets_app, hub)
    client = _signed_in(sets_app, monkeypatch)

    r = client.post(
        "/companies",
        json={"board": "WORKDAY:boeing/eng", "action": "follow"},
        base_url=_HTTPS,
    )
    assert r.json["followed"] == [
        "workday:boeing/external_careers",
        "workday:boeing/eng",
    ]
    r = client.post(
        "/companies",
        json={"board": "workday:boeing/external_careers", "action": "hide"},
        base_url=_HTTPS,
    )
    assert (r.json["followed"], len(r.json["hidden"])) == ([], 2)
    assert r.json["hidden_companies"] == 1


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


def test_a_hand_saved_overflowing_years_is_dropped_not_a_500(
    sets_app, hub, monkeypatch
):
    # Flask reads a JSON 1e999 as float inf, which int() refuses with OverflowError.
    client = _signed_in(sets_app, monkeypatch)
    r = client.post(
        "/profile",
        data='{"query": "backend engineer", "years": 1e999}',
        content_type="application/json",
        base_url=_HTTPS,
    )
    assert r.status_code == 200 and r.json["years"] is None


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


def test_a_second_parse_waits_out_the_first_rather_than_racing_the_cap(
    sets_app, hub, monkeypatch
):
    # Pins the in-flight guard above `parse_resume` (`_PARSING`): while one Account's read is
    # inside the router, a second read for the same Account is refused before it spends a
    # router call, and a different Account is not held up.
    import json as _json
    import threading

    entered, release = threading.Event(), threading.Event()
    calls = []

    def ask(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            entered.set()
            release.wait(5)
        return _json.dumps(_EXTRACTION)

    first, second = _signed_in(sets_app, monkeypatch), _signed_in(sets_app, monkeypatch)
    other = _signed_in(sets_app, monkeypatch, email="ada@example.com")
    monkeypatch.setattr(sets_app.llm_router, "ask", ask)
    results = {}

    def first_read():
        results["first"] = first.post(
            "/profile/parse", json={"text": "r"}, base_url=_HTTPS
        )

    t = threading.Thread(target=first_read)
    t.start()
    assert entered.wait(5)
    r = second.post("/profile/parse", json={"text": "r"}, base_url=_HTTPS)
    elsewhere = other.post("/profile/parse", json={"text": "r"}, base_url=_HTTPS)
    release.set()
    t.join(5)
    assert not t.is_alive()
    assert r.status_code == 429
    assert elsewhere.status_code == 200
    assert (
        len(calls) == 2
    )  # the first read and the other Account's — never the refused one
    assert results["first"].status_code == 200
    assert results["first"].json["parses_left"] == sets_app.MAX_PARSES - 1
    # …and once the first read finishes, the Account can read again.
    assert (
        second.post("/profile/parse", json={"text": "r"}, base_url=_HTTPS).status_code
        == 200
    )


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


@pytest.mark.parametrize("unreadable", ["hub-down", "corrupt"])
def test_delete_removes_a_profile_it_cannot_read(
    sets_app, hub, monkeypatch, unreadable
):
    # `get_profile` answers None for an unreadable record as well as an absent one; a delete
    # gated on it answered {"ok": true} and left the record stored.
    import headstart.alerts.store as st

    client = _signed_in(sets_app, monkeypatch)
    client.post("/profile", json={"query": "backend engineer"}, base_url=_HTTPS)
    path = f"profiles/{sets_app.subscription_id('dev@example.com')}.json"
    if unreadable == "corrupt":
        hub[path] = b"not json"
    else:
        real_read = st._read

        def flaky(repo, name, token):
            if name == path:
                raise OSError("Hub timed out")
            return real_read(repo, name, token)

        monkeypatch.setattr(st, "_read", flaky)

    r = client.delete("/profile", base_url=_HTTPS)

    assert r.status_code == 200 and path not in hub


def test_delete_never_reports_ok_when_it_cannot_tell(sets_app, hub, monkeypatch):
    import headstart.alerts.store as st

    client = _signed_in(sets_app, monkeypatch)
    client.post("/profile", json={"query": "backend engineer"}, base_url=_HTTPS)

    def down(*args, **kwargs):
        raise OSError("Hub timed out")

    monkeypatch.setattr(st, "_list_files", down)
    monkeypatch.setattr(st, "_read", down)

    assert client.delete("/profile", base_url=_HTTPS).status_code != 200


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


@pytest.mark.parametrize(
    "reply", [None, '{"query": "backend engineer", "years": 1e999}'], ids=str
)
def test_a_router_answer_the_reader_chokes_on_still_spends_a_read(
    sets_app, hub, monkeypatch, reply
):
    # The router answered, so the call was spent — a 500 before put_parses would make the
    # lifetime cap unbounded for any reply shaped like this.
    client = _signed_in(sets_app, monkeypatch)
    monkeypatch.setattr(sets_app.llm_router, "ask", lambda prompt: reply)
    r = client.post("/profile/parse", json={"text": "r"}, base_url=_HTTPS)
    assert r.status_code != 500
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


def test_keyword_filter_controls_render_in_the_rail(app):
    """The Keyword filter (ADR-0104): the box, its scope picker and the disclaimer slot ship in
    the Search rail. The description scopes are disabled until the served table carries the
    column, which the app fixture's fake table does not."""
    page = app.app.test_client().get("/").data
    assert b'id="kw"' in page and b'id="kwin"' in page and b'id="kwnote"' in page
    assert b'<option value="description" disabled>' in page
    assert b'<option value="both" disabled>' in page
    # the JS learns the scopes from the same map, through CFG
    assert b'"keyword_scopes"' in page and b'"keyword_default_scope": "title"' in page


def test_description_scopes_are_enabled_once_the_table_has_the_column(app, monkeypatch):
    """The disabled rule is a runtime fact of the served table, read per request — so a Space that
    has restarted onto a migrated table lights the options up with no template change."""
    monkeypatch.setattr(
        app._searcher,
        "capabilities",
        replace(app._searcher.capabilities, has_description=True),
    )
    page = app.app.test_client().get("/").data
    assert b'<option value="description">' in page
    assert b'<option value="both">' in page
    assert b"disabled" not in page.split(b'id="kwin"')[1].split(b"</select>")[0]


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


# ---- Résumé documents (ADR-0124, ADR-0131) ----
#
# The store half is tested in tests/test_alerts_store.py. What only these routes own is the
# policy: the revision check that refuses a conflicting push, the cap, the size bound, and the
# rule that a document must name itself.


def _doc(doc_id="rmfk3n2abcd", rev=1, **fields):
    document = {
        "schema": 1,
        "id": doc_id,
        "name": "Ada's résumé",
        "layoutId": "headless-headhunter",
        "updatedAt": "2026-09-10T10:00:00+00:00",
        "root": {"id": "__root__", "type": "__root__", "children": []},
        "content": {},
        "sync": True,
        "rev": rev,
    }
    document.update(fields)
    return document


def _put(client, document):
    return client.put("/resumes/" + document["id"], json=document, base_url=_HTTPS)


def test_a_resume_pushes_lists_reads_back_and_deletes(sets_app, hub, monkeypatch):
    client = _signed_in(sets_app, monkeypatch)
    assert client.get("/resumes", base_url=_HTTPS).json == []

    pushed = _put(client, _doc(rev=1))
    assert pushed.status_code == 200 and pushed.json == {"ok": True, "rev": 1}

    # The listing carries enough to show a row and open it, and none of the words. Its keys are
    # the browser's own (`updatedAt`) because the record IS the browser's export (ADR-0124).
    rows = client.get("/resumes", base_url=_HTTPS).json
    assert rows == [
        {
            "id": "rmfk3n2abcd",
            "name": "Ada's résumé",
            "layoutId": "headless-headhunter",
            "updatedAt": "2026-09-10T10:00:00+00:00",
            "rev": 1,
        }
    ]
    # …and the document itself comes back unchanged — the restore path a second browser uses.
    assert client.get("/resumes/rmfk3n2abcd", base_url=_HTTPS).json == _doc(rev=1)

    assert client.delete("/resumes/rmfk3n2abcd", base_url=_HTTPS).json == {"ok": True}
    assert client.get("/resumes", base_url=_HTTPS).json == []
    assert client.get("/resumes/rmfk3n2abcd", base_url=_HTTPS).status_code == 404
    assert client.delete("/resumes/rmfk3n2abcd", base_url=_HTTPS).status_code == 404


def test_a_push_that_is_not_one_past_the_stored_revision_is_refused(
    sets_app, hub, monkeypatch
):
    """The conflict rule, and the only thing that stops one device silently erasing another.

    The refusal hands back the STORED document, because the client cannot keep both copies
    without it — ADR-0124 decision 4 is "never resolved by discarding", and a bare 409 would
    leave the loser with nothing to adopt."""
    client = _signed_in(sets_app, monkeypatch)
    assert _put(client, _doc(rev=1)).status_code == 200
    assert _put(client, _doc(rev=2, name="second edit")).status_code == 200

    # This device still thinks the account copy is at rev 1, so it offers 2. It is not.
    stale = _put(client, _doc(rev=2, name="written on the laptop"))
    assert stale.status_code == 409
    assert stale.json["stored"]["name"] == "second edit"
    # And nothing was overwritten by the refusal.
    assert (
        client.get("/resumes/rmfk3n2abcd", base_url=_HTTPS).json["name"]
        == "second edit"
    )
    # Skipping ahead is refused for the same reason as falling behind.
    assert _put(client, _doc(rev=9)).status_code == 409


def _flaky_resume_read(monkeypatch, times=1):
    """Break the next `times` reads of a résumé document the way an outage does — the file is
    still listed, the read just does not answer. Everything else on the fake Hub keeps working,
    so the only thing under test is what the push route does with an unreadable stored copy."""
    import headstart.alerts.store as st

    real_read = st._read
    left = {"n": times}

    def flaky(repo, path, token):
        if "resumes/" in path and left["n"]:
            left["n"] -= 1
            raise OSError("the Hub did not answer")
        return real_read(repo, path, token)

    monkeypatch.setattr(st, "_read", flaky)


def test_a_push_is_refused_when_the_stored_copy_cannot_be_READ(
    sets_app, hub, monkeypatch
):
    """One unanswered Hub read must not read as "nothing stored, so nothing to lose".

    `get_resume` answers None for an absent record AND for a Hub that did not answer, and this
    route used to decide the conflict from it — so a single blip on that read sent a stale push
    down the first-push branch and overwrote the newer copy another device had just made, with a
    200 and "Saved to your account." The losing revision then exists only in the dataset's git
    history, which `squash-subscribers-history.yml` is built to erase.

    Refused with 409 and no `stored` body, deliberately, not 503: the client reads 503 as "this
    deployment keeps no account copies" and turns the whole feature off, while a bodyless 409 is
    already its "refused, and nothing here was overwritten" path."""
    client = _signed_in(sets_app, monkeypatch)
    assert _put(client, _doc(rev=1)).status_code == 200
    assert _put(client, _doc(rev=2, name="written on the phone")).status_code == 200

    _flaky_resume_read(monkeypatch)
    stale = _put(client, _doc(rev=2, name="written on the laptop"))
    assert stale.status_code == 409, (
        "a transient read let a stale push overwrite a newer copy"
    )
    assert "stored" not in stale.json
    # And the phone's revision is still the one on the account, untouched.
    kept = client.get("/resumes/rmfk3n2abcd", base_url=_HTTPS).json
    assert kept["name"] == "written on the phone" and kept["rev"] == 2
    # The control: the very same push, with the read working, is a 409 too — so the refusal
    # above is the guard holding, not the flaky read breaking something else.
    assert _put(client, _doc(rev=2, name="written on the laptop")).status_code == 409


def test_a_stored_record_that_is_not_a_resume_refuses_the_push(
    sets_app, hub, monkeypatch
):
    """Answered garbage is still not an empty slot, and this is the deliberate half of that.

    `resumes_for` skips a malformed record so one bad file cannot empty the list — a read path,
    where skipping costs a row. This is the WRITE path, where "I cannot read it" would otherwise
    become "so I will write over it", and the bytes underneath might be the only copy of a
    revision. The client is told (409), and the way out is the switch: turning sync off deletes
    the record — `delete_resume` keys on the listing, which still answers — and turning it back
    on pushes fresh. That is a wedge with an exit, which overwriting is not."""
    client = _signed_in(sets_app, monkeypatch)
    assert _put(client, _doc(rev=1)).status_code == 200
    key = next(k for k in hub if k.endswith("rmfk3n2abcd.json"))
    hub[key] = b"<!doctype html><title>504 Gateway Timeout</title>"
    refused = _put(client, _doc(rev=2))
    assert refused.status_code == 409
    assert hub[key].startswith(b"<!doctype html"), (
        "the push overwrote a record it could not read"
    )


def test_a_first_push_still_goes_through_when_the_record_is_merely_ABSENT(
    sets_app, hub, monkeypatch
):
    """The other half of the same line, and the one that would break the feature if the fix
    over-reached: a document nobody has pushed yet is absent, not unreadable, and its first push
    must still be accepted. Absence is decided by the listing, so this holds whether or not
    `huggingface_hub` is importable — the fake Hub's own "no such key" is not one of its
    exception types."""
    client = _signed_in(sets_app, monkeypatch)
    assert _put(client, _doc(rev=4)).status_code == 200
    assert client.get("/resumes/rmfk3n2abcd", base_url=_HTTPS).json["rev"] == 4


def test_a_conflict_that_cannot_read_the_stored_copy_still_refuses(
    sets_app, hub, monkeypatch
):
    """The revision decides the verdict and a second read supplies the body. If that read
    fails, the answer is still a refusal — never an acceptance — and the client's bodyless-409
    path takes it from there."""
    client = _signed_in(sets_app, monkeypatch)
    assert _put(client, _doc(rev=1)).status_code == 200
    # The first read (the revision) works, the second (the body) does not.
    import headstart.alerts.store as st

    real_read = st._read
    seen = {"n": 0}

    def second_read_fails(repo, path, token):
        if "resumes/" in path:
            seen["n"] += 1
            if seen["n"] == 2:
                raise OSError("the Hub did not answer")
        return real_read(repo, path, token)

    monkeypatch.setattr(st, "_read", second_read_fails)
    refused = _put(client, _doc(rev=9))
    assert refused.status_code == 409 and refused.json["stored"] is None


def test_a_first_push_takes_any_revision_because_there_is_nothing_to_lose(
    sets_app, hub, monkeypatch
):
    """A strict `rev == 1` would turn "the other device deleted it" into a conflict against an
    empty slot that no retry ever resolves. The counter guards stored content; there is none."""
    client = _signed_in(sets_app, monkeypatch)
    assert _put(client, _doc(rev=7)).status_code == 200
    assert client.get("/resumes/rmfk3n2abcd", base_url=_HTTPS).json["rev"] == 7


def test_a_resume_must_name_itself(sets_app, hub, monkeypatch):
    """Without this, a client could file document A at document B's path and replace it."""
    client = _signed_in(sets_app, monkeypatch)
    answer = client.put(
        "/resumes/rmfk3n2abcd", json=_doc("rmfk3n2wxyz"), base_url=_HTTPS
    )
    assert answer.status_code == 400
    assert client.get("/resumes", base_url=_HTTPS).json == []


@pytest.mark.parametrize(
    "body",
    [
        {"id": "rmfk3n2abcd"},  # no revision at all
        {"id": "rmfk3n2abcd", "rev": 0},  # 0 means "no account copy", never a push
        {"id": "rmfk3n2abcd", "rev": "1"},  # a string is not a counter
        {
            "id": "rmfk3n2abcd",
            "rev": True,
        },  # nor is a bool, which int() would have taken
        ["not", "a", "document"],
    ],
)
def test_a_push_without_a_usable_revision_is_refused(sets_app, hub, monkeypatch, body):
    client = _signed_in(sets_app, monkeypatch)
    assert (
        client.put("/resumes/rmfk3n2abcd", json=body, base_url=_HTTPS).status_code
        == 400
    )
    assert hub == {}


def test_a_malformed_document_id_is_a_400_not_a_silent_no_op(
    sets_app, hub, monkeypatch
):
    """`put_resume` refuses a bad id by doing nothing, which the browser would read as saved.
    The route checks at the door so the answer is visible (`is_resume_id`)."""
    client = _signed_in(sets_app, monkeypatch)
    assert (
        client.put("/resumes/nope", json=_doc("nope"), base_url=_HTTPS).status_code
        == 400
    )
    assert hub == {}


def test_resumes_are_capped_but_an_overwrite_never_hits_the_cap(
    sets_app, hub, monkeypatch
):
    client = _signed_in(sets_app, monkeypatch)
    ids = [f"rmfk3n2ab{i:02d}" for i in range(sets_app.MAX_RESUMES)]
    for doc_id in ids:
        assert _put(client, _doc(doc_id)).status_code == 200
    over = _put(client, _doc("rmfk3n2abzz"))
    assert over.status_code == 400 and "limit" in over.json["error"]
    # The one already stored is an update, not a create, so the cap must not refuse it.
    assert _put(client, _doc(ids[0], rev=2)).status_code == 200


def test_a_resume_over_the_size_bound_is_refused_before_it_is_parsed(
    sets_app, hub, monkeypatch
):
    client = _signed_in(sets_app, monkeypatch)
    huge = _doc(content={"n1": {"text": "x" * (sets_app.MAX_RESUME_BYTES + 1000)}})
    assert _put(client, huge).status_code == 413
    assert hub == {}


def test_resumes_are_scoped_to_the_signed_in_account(sets_app, hub, monkeypatch):
    ada = _signed_in(sets_app, monkeypatch, email="ada@example.com")
    assert _put(ada, _doc()).status_code == 200
    bob = _signed_in(sets_app, monkeypatch, email="bob@example.com")
    assert bob.get("/resumes", base_url=_HTTPS).json == []
    assert bob.get("/resumes/rmfk3n2abcd", base_url=_HTTPS).status_code == 404


def test_resumes_require_wall_and_storage(auth_app, monkeypatch):
    """Signed in, but this deployment keeps no per-Account records: 503, and the tab renders
    without the switch at all rather than offering to store what nothing can store."""
    client = _signed_in(auth_app, monkeypatch)
    assert client.get("/resumes", base_url=_HTTPS).status_code == 503
    assert (
        client.put("/resumes/rmfk3n2abcd", json=_doc(), base_url=_HTTPS).status_code
        == 503
    )
    assert b'id="rb-sync"' not in client.get("/", base_url=_HTTPS).data


def test_resumes_are_gated_by_the_wall(sets_app):
    assert sets_app.app.test_client().get("/resumes").status_code == 401
    assert (
        sets_app.app.test_client().put("/resumes/rmfk3n2abcd", json={}).status_code
        == 401
    )


def test_the_account_switch_renders_when_configured(sets_app, hub, monkeypatch):
    page = _signed_in(sets_app, monkeypatch).get("/", base_url=_HTTPS).data
    assert b'id="rb-sync"' in page
    # The consequence is stated with the switch, not three rows away (ADR-0124 decision 2).
    assert b"Keep a copy on my account" in page


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


def _write_trends(state: Path, lines: list[str]) -> None:
    _write_ledger(state, list(csv.DictReader(io.StringIO("\n".join(lines) + "\n"))))


def _write_ledger(state: Path, rows: list[dict]) -> None:
    """The aggregate trends ledger holding ``rows``; the history reads only its ticks before the
    Board-delta ledger's first (the archive)."""
    table = pa.table(
        {
            "ts": pa.array(
                [datetime.fromisoformat(row["ts"]) for row in rows],
                pa.timestamp("ms", tz="UTC"),
            ),
            "version": pa.array([int(row["version"]) for row in rows], pa.int64()),
            "metric": pa.array([row.get("metric") or "stock" for row in rows]),
            "family": pa.array([row["family"] for row in rows]),
            "band": pa.array([row["band"] for row in rows]),
            "ats": pa.array([row.get("ats") or "all" for row in rows]),
            "count": pa.array([int(row["count"]) for row in rows], pa.int64()),
        }
    )
    out = state / "data" / "state" / "role_trends.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out)


# The Space image copies config/ beside app.py and a checkout has none there, so the app's import
# reads no family labels or watched roles; a test gives the history the taxonomy it needs.
_SPACE_CONFIG = APP.parent / "config"
_DELTA_COLUMNS = ("ts", "board", "metric", "family", "band", "ats")


def _write_board_deltas(state: Path, deltas: list[dict], ledger: list[dict]) -> None:
    """The Board-delta ledger as role_trends writes it since ADR-0230 step 2: one file a tick,
    its series version under ``centroid_version``, empty when nothing moved. So a ledger tick at
    or after the first delta's that no delta names is written as an empty file."""
    if not deltas:
        return
    directory = state / "data" / "state" / "role_trend_board_deltas"
    directory.mkdir(parents=True, exist_ok=True)
    by_tick: dict[str, list[dict]] = defaultdict(list)
    for row in deltas:
        by_tick[row["ts"]].append(row)
    version_of = {row["ts"]: row["version"] for row in ledger}
    first = min(by_tick)
    for ts in sorted({*by_tick, *(t for t in version_of if t >= first)}):
        rows = by_tick.get(ts, [])
        versions = {row["version"] for row in rows} or {version_of[ts]}
        assert len(versions) == 1, f"a tick is counted at one version: {ts}"
        schema = pa.schema(
            [(name, pa.string()) for name in _DELTA_COLUMNS] + [("delta", pa.int64())],
            metadata={
                b"centroid_version": str(versions.pop()).encode(),
                b"ts": ts.encode(),
            },
        )
        table = pa.table(
            {name: [row[name] for row in rows] for name in (*_DELTA_COLUMNS, "delta")},
            schema=schema,
        )
        pq.write_table(table, directory / f"{ts.replace(':', '-')}.parquet")


def _trend_history(
    root: Path,
    *,
    ledger=(),
    deltas=(),
    companies: dict | None = None,
    config: Path = _SPACE_CONFIG,
) -> trend_history.TrendHistory:
    """A TrendHistory loaded as the Space loads one, from state files written from ``ledger``
    (aggregate rows), ``deltas`` (Board-delta rows) and the company directory. Each call writes a
    state of its own under ``root``."""
    state = Path(tempfile.mkdtemp(dir=root))
    ledger = list(ledger)
    if ledger:
        _write_ledger(state, ledger)
    _write_board_deltas(state, list(deltas), ledger)
    if companies is not None:
        (state / "data" / "state").mkdir(parents=True, exist_ok=True)
        (state / "data" / "state" / "company_directory.json").write_text(
            json.dumps({"companies": list(companies.values())}), encoding="utf-8"
        )
    return trend_history.TrendHistory.load(state / "data" / "state", config)


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
    _write_trends(state, rows)


def _write_epochs(state: Path, rows: list[dict]) -> Path:
    path = state / "data" / "state" / "trends_epochs.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        # the header is whatever the rows carry, so a fixture can write either shape
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture(scope="module")
def trends_app(tmp_path_factory):
    """The app with a trends ledger. `_STATE` is the hardcoded `/app/state`, so the ledger can't
    ride the snapshot stub — instead the history is loaded from the fixture's state after
    import, which still exercises the real parsing (metric default included)."""
    state = tmp_path_factory.mktemp("state")
    _trends_csv(state)
    # The wall pinned OFF explicitly ("" is falsy in _AUTH_ON): module-scoped fixtures from
    # earlier in this file hold their env until teardown, so without this the trends app can
    # inherit a live wall depending on test order and answer every request 401.
    with _space_app(state, env={"SECRET_KEY": "", "GOOGLE_CLIENT_ID": ""}) as module:
        module._HISTORY = trend_history.TrendHistory.load(
            state / "data" / "state", _SPACE_CONFIG
        )
        module._HISTORY._watch = {
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


def test_trends_comparable_coverage_keeps_only_boards_known_at_the_base(
    trends_app, monkeypatch, tmp_path
):
    ledger = [
        {
            "ts": stamp,
            "version": 2,
            "metric": "stock",
            "family": "software-engineering",
            "band": "mid",
            "ats": "greenhouse",
            "count": 1,
        }
        for stamp in (_T1, _T2, _T3)
    ]
    deltas = [
        {
            "ts": _T1,
            "version": 2,
            "board": "greenhouse:early",
            "metric": "stock",
            "family": "software-engineering",
            "band": "mid",
            "ats": "greenhouse",
            "delta": 10,
        },
        {
            "ts": _T2,
            "version": 2,
            "board": "greenhouse:early",
            "metric": "stock",
            "family": "software-engineering",
            "band": "mid",
            "ats": "greenhouse",
            "delta": 2,
        },
        {
            "ts": _T2,
            "version": 2,
            "board": "greenhouse:late",
            "metric": "stock",
            "family": "software-engineering",
            "band": "mid",
            "ats": "greenhouse",
            "delta": 100,
        },
        {
            "ts": _T3,
            "version": 2,
            "board": "greenhouse:early",
            "metric": "stock",
            "family": "software-engineering",
            "band": "mid",
            "ats": "greenhouse",
            "delta": -1,
        },
    ]
    monkeypatch.setattr(
        trends_app, "_HISTORY", _trend_history(tmp_path, ledger=ledger, deltas=deltas)
    )
    d = (
        trends_app.app.test_client()
        .get(f"/trends?coverage=comparable&base={quote(_T1)}")
        .get_json()
    )
    assert d["base"] == _T1
    assert d["series"][0]["points"] == [10, 12, 11]


def test_trends_comparable_base_can_be_an_unchanged_measurement(
    trends_app, monkeypatch, tmp_path
):
    ledger = [
        {
            "ts": stamp,
            "version": 2,
            "metric": "stock",
            "family": "software-engineering",
            "band": "mid",
            "ats": "greenhouse",
            "count": 1,
        }
        for stamp in (_T1, _T2, _T3)
    ]
    deltas = [
        {
            "ts": _T1,
            "version": 2,
            "board": "greenhouse:early",
            "metric": "stock",
            "family": "software-engineering",
            "band": "mid",
            "ats": "greenhouse",
            "delta": 10,
        },
        {
            "ts": _T3,
            "version": 2,
            "board": "greenhouse:early",
            "metric": "stock",
            "family": "software-engineering",
            "band": "mid",
            "ats": "greenhouse",
            "delta": 1,
        },
    ]
    monkeypatch.setattr(
        trends_app, "_HISTORY", _trend_history(tmp_path, ledger=ledger, deltas=deltas)
    )
    d = (
        trends_app.app.test_client()
        .get(f"/trends?coverage=comparable&base={quote(_T2)}")
        .get_json()
    )
    assert d["base"] == _T2
    assert d["stamps"] == [_T2, _T3]
    assert d["series"][0]["points"] == [10, 11]


@pytest.fixture
def comparable_history(trends_app, monkeypatch, tmp_path):
    def install(stamps, changes, later=()):
        group = {
            "version": 2,
            "metric": "stock",
            "family": "software-engineering",
            "band": "mid",
            "ats": "greenhouse",
        }
        monkeypatch.setattr(
            trends_app,
            "_HISTORY",
            _trend_history(
                tmp_path,
                ledger=[{**group, "ts": stamp, "count": 1} for stamp in stamps],
                deltas=[
                    {**group, "ts": stamp, "board": board, "delta": change}
                    for board, stamp, change in [
                        *(
                            ("greenhouse:early", stamp, change)
                            for stamp, change in changes
                        ),
                        *later,
                    ]
                ],
            ),
        )
        return trends_app.app.test_client()

    return install


def test_comparable_charts_every_tick_the_delta_ledger_holds(comparable_history):
    """Every tick writes its own delta file (ADR-0230), so the delta ledger names the ticks
    after the archive, and a tick the aggregate missed is charted like any other. Until step 3
    the aggregate named them, and this read [_T1, _T3] with _T2's delta folded into _T3."""
    client = comparable_history([_T1, _T3], [(_T1, 10), (_T2, 5)])
    data = client.get("/trends?coverage=comparable").get_json()
    assert data["stamps"] == [_T1, _T2, _T3]
    assert data["series"][0]["points"] == [10, 15, 15]


def test_comparable_default_starts_at_supported_history(comparable_history):
    client = comparable_history([_T1, _T2, _T3], [(_T2, 10)])
    data = client.get("/trends?coverage=comparable").get_json()
    assert data["base"] == _T2
    assert data["stamps"] == [_T2, _T3]
    assert data["series"][0]["points"] == [10, 10]
    # A base before per-Board counting began starts the cohort where counting did, rather
    # than answering nothing: the page names the base it actually used.
    early = client.get(f"/trends?coverage=comparable&base={quote(_T1)}").get_json()
    assert early["base"] == _T2
    assert early["stamps"] == [_T2, _T3]


@pytest.mark.parametrize("since", [_T1, _T3])
def test_comparable_implicit_base_is_independent_of_since(
    comparable_history, trends_app, since
):
    client = comparable_history(
        [_T1, _T2, _T3], [(_T2, 10)], [("greenhouse:later", _T3, 50)]
    )
    data = client.get(f"/trends?coverage=comparable&since={quote(since)}").get_json()
    assert data["base"] == _T2
    assert data["stamps"] == [stamp for stamp in [_T2, _T3] if stamp >= since]
    assert data["series"][0]["points"] == [10] * len(data["stamps"])


@pytest.mark.parametrize("scope", ["", "&ats=greenhouse"])
def test_comparable_keeps_zero_endpoint(comparable_history, scope):
    client = comparable_history([_T1, _T2, _T3], [(_T1, 10), (_T2, -10)])
    data = client.get("/trends?coverage=comparable" + scope).get_json()
    assert data["stamps"] == [_T1, _T2, _T3]
    assert data["totals"] == [10, 0, 0]
    assert data["series"][0]["points"] == [10, 0, 0]
    assert data["series"][0]["latest"] == 0


def test_comparable_display_window_is_separate_from_base(comparable_history):
    client = comparable_history([_T1, _T2, _T3], [(_T1, 10), (_T2, 5)])
    data = client.get(
        f"/trends?coverage=comparable&base={quote(_T1)}&since={quote(_T2)}"
        f"&until={quote(_T2)}"
    ).get_json()
    assert data["base"] == _T1
    assert data["stamps"] == [_T2]
    assert data["series"][0]["points"] == [15]


def _delta(ts, board, delta, family="software-engineering", metric="stock"):
    return {
        "ts": ts,
        "version": 2,
        "board": board,
        "metric": metric,
        "family": family,
        "band": "mid",
        "ats": board.split(":", 1)[0],
        "delta": delta,
    }


# Three companies over the delta ledger (ADR-0185). HPE is one Tenant split into two Workday
# sites, and two unrelated employers are both called "Citi".
_COMPANY_LEDGER = [
    {
        "ts": stamp,
        "version": 2,
        "metric": "stock",
        "family": "software-engineering",
        "band": "mid",
        "ats": "workday",
        "count": 1,
    }
    for stamp in (_T1, _T2, _T3)
] + [
    {
        "ts": stamp,
        "version": 2,
        "metric": "new",
        "family": "software-engineering",
        "band": "mid",
        "ats": "workday",
        "count": 1,
    }
    for stamp in (_T2, _T3)
]
_COMPANY_DELTAS = [
    _delta(_T2, "workday:hpe/a", 2, metric="new"),
    _delta(_T1, "workday:hpe/a", 10),
    _delta(_T1, "workday:hpe/b", 5),
    _delta(_T1, "workday:hpe/b", 2, family="ai-ml"),
    _delta(_T1, "workday:hpe/b", 7, family="non-tech"),
    _delta(_T1, "workday:citi/2", 40),
    _delta(_T2, "workday:hpe/a", 1),
    _delta(_T2, "eightfold:citi.eightfold.ai", 3),  # first seen at T2
    _delta(_T3, "workday:hpe/b", -5),
    _delta(_T3, "workday:citi/2", 4),
]
_COMPANY_DIRECTORY = {
    "workday:hpe/a": {"name": "Hpe", "boards": ["workday:hpe/a", "workday:hpe/b"]},
    "workday:citi/2": {"name": "Citi", "boards": ["workday:citi/2"]},
    "eightfold:citi.eightfold.ai": {
        "name": "Citi",
        "boards": ["eightfold:citi.eightfold.ai"],
    },
    # a third "Citi", on the same ATS as the first: its ATS cannot tell them apart
    "workday:citibank/x": {"name": "Citi", "boards": ["workday:citibank/x"]},
}


def _company_history(
    trends_app, monkeypatch, tmp_path, deltas=_COMPANY_DELTAS, hold=False
):
    """The three companies' history, served by the app: ``deltas`` with the fixture's ledger and
    directory, with no `new` hold unless ``hold``."""
    history = _trend_history(
        tmp_path,
        ledger=_COMPANY_LEDGER,
        deltas=deltas,
        companies=_COMPANY_DIRECTORY,
    )
    # No holds by default: the fixture's runs span two days, inside every Board's first week.
    # The hold has its own tests below.
    if not hold:
        history._new_hold = {}
    monkeypatch.setattr(trends_app, "_HISTORY", history)
    return history


@pytest.fixture
def company_trends(trends_app, monkeypatch, tmp_path):
    """Three companies over the delta ledger (ADR-0185). HPE is one Tenant split into two
    Workday sites, and two unrelated employers are both called "Citi"."""
    _company_history(trends_app, monkeypatch, tmp_path)
    return trends_app.app.test_client()


def test_board_openings_count_tech_stock_only(trends_app, tmp_path):
    """`non-tech` is no opening, `watch:` re-counts a family, `new` is not stock."""
    deltas = [
        {**_delta(_T1, "a", 100), "version": 1},  # a stale refit
        _delta(_T2, "a", 5),
        _delta(_T2, "a", 9, family="non-tech"),
        _delta(_T2, "a", 3, family="watch:fde"),
        _delta(_T2, "a", 4, metric="new"),
        _delta(_T3, "a", -2),
    ]
    assert _trend_history(tmp_path, deltas=deltas).openings()["a"] == 3


def test_any_board_of_a_company_picks_the_whole_company(company_trends):
    """A Hot-tab row links by its own Board; the company is every Board of that Tenant."""
    d = company_trends.get("/trends?company=workday:hpe/b").get_json()
    points = {s["name"]: s["points"] for s in d["series"]}
    assert points == {"software-engineering": [15, 16, 11], "ai-ml": [2, 2, 2]}
    assert [c["key"] for c in d["companies"]] == ["workday:hpe/a"]
    assert d["companies"][0]["board_keys"] == ["workday:hpe/a", "workday:hpe/b"]
    assert d["companies"][0]["openings"] == 13  # 11 + 2, no non-tech
    # the share denominator is the picked company's own total, non-tech included
    assert d["totals"] == [24, 25, 20]


def test_split_by_company_draws_a_line_per_pick_and_tells_twins_apart(company_trends):
    d = company_trends.get(
        "/trends?split=company&company=workday:citi/2"
        "&company=eightfold:citi.eightfold.ai&company=workday:hpe/a"
    ).get_json()
    assert d["split_by"] == "company"
    by_label = {s["label"]: s["points"] for s in d["series"]}
    assert by_label == {
        "Citi (workday)": [40, 40, 44],
        "Citi (eightfold)": [None, 3, 3],  # a gap before its first delta, never a zero
        "Hpe": [17, 18, 13],
    }


def test_a_pick_with_nothing_new_is_a_zero_line_not_a_missing_one(company_trends):
    """One company can go a run with nothing new; that is 0, and it must still get a line."""
    d = company_trends.get(
        "/trends?metric=new&split=company&company=workday:citi/2&company=workday:hpe/a"
    ).get_json()
    by_label = {s["label"]: s["points"] for s in d["series"]}
    assert by_label == {"Citi": [None, 0, 0], "Hpe": [None, 2, 2]}


def test_each_pick_carries_its_own_share_denominator_and_start(company_trends):
    d = company_trends.get(
        "/trends?split=company&company=workday:citi/2&company=workday:hpe/a"
    ).get_json()
    assert d["company_totals"] == {
        "workday:citi/2": [40, 40, 44],
        "workday:hpe/a": [24, 25, 20],  # non-tech in the denominator, like `totals`
    }
    assert d["totals"] == [64, 65, 64]
    assert d["counted_since"] == {"workday:citi/2": _T1, "workday:hpe/a": _T1}
    assert company_trends.get("/trends").get_json()["counted_since"] == {}
    # a company first counted later is dated from its own first Board, not the ledger's start
    late = company_trends.get("/trends?company=eightfold:citi.eightfold.ai").get_json()
    assert late["counted_since"] == {"eightfold:citi.eightfold.ai": _T2}


def test_twins_on_one_ats_are_told_apart_by_key(company_trends):
    d = company_trends.get(
        "/trends?split=company&company=workday:citi/2&company=workday:citibank/x"
        "&company=eightfold:citi.eightfold.ai"
    ).get_json()
    assert sorted(c["label"] for c in d["companies"]) == [
        "Citi (eightfold)",
        "Citi (workday:citi/2)",
        "Citi (workday:citibank/x)",
    ]


def test_company_combines_with_comparable_coverage(company_trends):
    """Picked, and counted only over Boards known at the base: the T2 Citi drops out."""
    d = company_trends.get(
        f"/trends?coverage=comparable&base={quote(_T1)}&split=company"
        "&company=workday:citi/2&company=eightfold:citi.eightfold.ai"
    ).get_json()
    assert d["base"] == _T1
    assert {s["label"]: s["points"] for s in d["series"]} == {
        "Citi (workday)": [40, 40, 44]
    }


@pytest.mark.parametrize(
    ("query", "status"),
    [
        ("/trends?company=greenhouse:nobody", 400),
        ("/trends?split=company", 400),
        ("/trends?split=everything", 400),
    ],
)
def test_bad_company_requests_are_refused(company_trends, query, status):
    assert company_trends.get(query).status_code == status


def test_suggest_ranks_and_labels_companies(company_trends):
    d = company_trends.get("/companies/suggest?q=citi").get_json()
    got = [(c["key"], c["label"], c["openings"], c["boards"]) for c in d["companies"]]
    # three Citis match exactly; only the one with the most openings is offered
    assert got == [("workday:citi/2", "Citi", 44, 1)]
    assert company_trends.get("/companies/suggest?q=hpe").get_json()["companies"][0][
        "atses"
    ] == ["workday"]
    assert company_trends.get("/companies/suggest?q=zzz").get_json() == {
        "companies": []
    }
    assert company_trends.get("/companies/suggest?limit=x").status_code == 400


def test_no_directory_answers_503(trends_app, monkeypatch):
    monkeypatch.setattr(trends_app._HISTORY, "_companies", {})
    client = trends_app.app.test_client()
    assert client.get("/companies/suggest?q=a").status_code == 503
    assert client.get("/trends?company=workday:hpe/a").status_code == 503


def test_trends_rejects_unknown_coverage(trends_app):
    assert (
        trends_app.app.test_client().get("/trends?coverage=future").status_code == 400
    )


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


# ---- /trends ats scope (ADR-0075) --------------------------------------------------------

_U1, _U2 = (
    "2026-08-14T01:00:00+00:00",  # pre-ADR-0075: migrated, ats=all sentinel only
    "2026-08-15T01:00:00+00:00",  # post-ADR-0075: real per-ats rows
)


def _ats_trends_csv(state: Path) -> None:
    rows = [
        "ts,version,metric,family,band,ats,count",
        f"{_U1},2,stock,software-engineering,mid,all,100",
        f"{_U1},2,stock,non-tech,all,all,10",
        f"{_U2},2,stock,software-engineering,mid,greenhouse,60",
        f"{_U2},2,stock,software-engineering,mid,lever,50",
        f"{_U2},2,stock,ai-ml,mid,greenhouse,20",
        f"{_U2},2,stock,non-tech,all,all,15",
    ]
    _write_trends(state, rows)


@pytest.fixture(scope="module")
def ats_trends_app(tmp_path_factory):
    state = tmp_path_factory.mktemp("ats-state")
    _ats_trends_csv(state)
    with _space_app(state, env={"SECRET_KEY": "", "GOOGLE_CLIENT_ID": ""}) as module:
        module._HISTORY = trend_history.TrendHistory.load(
            state / "data" / "state", _SPACE_CONFIG
        )
        module._HISTORY._watch = {}
        yield module


def test_trends_no_ats_param_sums_every_ats_including_the_migration_sentinel(
    ats_trends_app,
):
    d = ats_trends_app.app.test_client().get("/trends").get_json()
    by_name = {s["name"]: s for s in d["series"]}
    assert by_name["software-engineering"]["points"] == [
        100,
        110,
    ]  # U1 sentinel + U2's 60+50
    assert by_name["ai-ml"]["points"] == [None, 20]
    assert d["totals"] == [110, 145]  # U1: 100+10, U2: 60+50+20+15


def test_trends_ats_filter_excludes_the_migration_sentinel(ats_trends_app):
    """A pre-ADR-0075 row's ats='all' matches no real ATS name, so a scoped request has no
    row at all at the sentinel-only stamp — U1 drops out of `stamps` entirely rather than
    appearing as a gap, the hard history wall the ADR calls out."""
    d = ats_trends_app.app.test_client().get("/trends?ats=greenhouse").get_json()
    assert d["stamps"] == [_U2]
    by_name = {s["name"]: s for s in d["series"]}
    assert by_name["software-engineering"]["points"] == [60]
    assert by_name["ai-ml"]["points"] == [20]


def test_trends_ats_filter_narrows_totals_to_the_selected_scope(ats_trends_app):
    """Share is 'of what's in view': totals scope down with the ats filter exactly like every
    other row does, not the whole unfiltered index (ADR-0075)."""
    d = ats_trends_app.app.test_client().get("/trends?ats=greenhouse").get_json()
    assert d["totals"] == [80]  # U2 only: 60 + 20


def test_trends_multiple_ats_params_union(ats_trends_app):
    d = (
        ats_trends_app.app.test_client()
        .get("/trends?ats=greenhouse&ats=lever")
        .get_json()
    )
    assert d["stamps"] == [_U2]
    by_name = {s["name"]: s for s in d["series"]}
    assert by_name["software-engineering"]["points"] == [110]  # 60 + 50, U2 only


_METHODOLOGY = {
    "family_list_fingerprint": "aaa",
    "family_classifier_version": 3,
    "tech_filter_version": 1,
    "derivations_version": 12,
    "dedup_version": 1,
}


def _write_stamped_ticks(state: Path, stamps: list[tuple[str, dict]]) -> Path:
    """One tick a stamp, each written by ``record_tick`` with its Methodology and one Board's
    opening. Returns the ``data/state`` directory."""
    directory = state / "data" / "state"
    for ts, changed in stamps:
        trend_history.record_tick(
            directory,
            ts,
            {("greenhouse:acme", "stock", "software-engineering", "mid"): 1},
            {},
            trend_history.Methodology(**{**_METHODOLOGY, **changed}),
        )
    return directory


@pytest.fixture(scope="module")
def epochs_trends_app(tmp_path_factory):
    """The trends app over three ticks (ADR-0164, ADR-0230): a baseline at T1, the tech filter
    moving at T2, and both the family map and derivations moving together at T3."""
    state = tmp_path_factory.mktemp("epochs-state")
    _write_stamped_ticks(
        state,
        [
            (_T1, {}),
            (_T2, {"tech_filter_version": 2}),
            (
                _T3,
                {
                    "tech_filter_version": 2,
                    "family_list_fingerprint": "bbb",
                    "derivations_version": 13,
                },
            ),
        ],
    )
    with _space_app(state, env={"SECRET_KEY": "", "GOOGLE_CLIENT_ID": ""}) as module:
        module._HISTORY = trend_history.TrendHistory.load(
            state / "data" / "state", _SPACE_CONFIG
        )
        yield module


def test_trends_epochs_drops_the_baseline_and_names_what_moved(epochs_trends_app):
    d = epochs_trends_app.app.test_client().get("/trends").get_json()
    assert d["epochs"] == [
        {
            "ts": _T2,
            "changed": ["tech filter changed"],
            "fields": ["tech_filter_version"],
        },
        {
            "ts": _T3,
            "changed": [
                "role family map edited",
                "experience/salary extraction changed",
            ],
            "fields": ["family_map_fingerprint", "derivations_version"],
        },
    ]


def test_trends_epochs_are_narrowed_by_since_and_until(epochs_trends_app):
    client = epochs_trends_app.app.test_client()
    d = client.get(f"/trends?since={quote(_T3)}").get_json()
    assert d["epochs"] == [
        {
            "ts": _T3,
            "changed": [
                "role family map edited",
                "experience/salary extraction changed",
            ],
            "fields": ["family_map_fingerprint", "derivations_version"],
        }
    ]
    d = client.get(f"/trends?until={quote(_T2)}").get_json()
    assert d["epochs"] == [
        {
            "ts": _T2,
            "changed": ["tech filter changed"],
            "fields": ["tech_filter_version"],
        }
    ]


def _epochs_of(state: Path) -> list[dict]:
    """The counting changes a history marks from its ticks' Methodology alone."""
    return trend_history.TrendHistory.load(state, _SPACE_CONFIG)._epochs


def test_trends_epochs_name_a_dedup_change(tmp_path):
    """A dedup-rule change removes served duplicates in one tick, which reads as a hiring drop
    unless it is marked."""
    state = _write_stamped_ticks(tmp_path, [(_T1, {}), (_T2, {"dedup_version": 2})])
    assert _epochs_of(state) == [
        {
            "ts": _T2,
            "changed": ["duplicate removal changed"],
            "fields": ["dedup_version"],
        }
    ]


def test_trends_epochs_name_a_family_assignment_change(tmp_path):
    """ADR-0215/ADR-0220: what decides a row's family (the title rules, then the classifier head)
    moves Jobs between families in one tick, so it is marked like a family-map edit. Ticks from
    before the title decided anything carry ``none``."""
    state = _write_stamped_ticks(
        tmp_path,
        [(_T1, {"family_classifier_version": "none"}), (_T2, {})],
    )
    assert _epochs_of(state) == [
        {
            "ts": _T2,
            "changed": ["role family assignment changed"],
            "fields": ["family_classifier_version"],
        }
    ]


def test_trends_epochs_are_not_narrowed_by_ats(epochs_trends_app):
    """Unlike every other field in the response, epochs is a methodology timeline, not scoped
    to an ATS selection — a tech-filter or family-map change did not happen "for" one ATS."""
    d = epochs_trends_app.app.test_client().get("/trends?ats=greenhouse").get_json()
    assert len(d["epochs"]) == 2


@pytest.mark.parametrize(
    "path",
    [
        "data/state/role_trend_board_deltas/2026-09-13T12-00-39+00-00.parquet",
        "data/state/role_trend_index_deltas_before_board_deltas.parquet",
        # the older layout's, read until the one-off migration has run
        "data/state/role_trends.parquet",
        "data/state/trends_epochs.csv",
        "data/state/company_directory.json",
    ],
)
def test_the_index_pull_fetches_every_state_file_the_app_reads(app, monkeypatch, path):
    # The fixtures above write these files straight into the state dir, and the stubbed
    # snapshot_download ignores its patterns — so a file the pull never fetches still passed
    # here while production served `epochs: []`.
    from fnmatch import fnmatch

    seen = {}
    monkeypatch.setattr(app, "snapshot_download", lambda *a, **k: seen.update(k))
    app._pull_index()
    assert any(fnmatch(path, pattern) for pattern in seen["allow_patterns"])


# ── The trust surfaces (ADR-0112, ADR-0113) ────────────────────────────────────────────
# These assert *claims*, not markup. Each one is a sentence the product makes to a stranger
# who has no way to check it from inside the page; a refactor that drops one should fail
# here rather than ship a quieter, less accountable door.


def test_the_door_makes_its_case_before_asking_for_an_identity(auth_app):
    """ADR-0112: what it is, proof, what sign-in costs, and how to check — then the button."""
    page = auth_app.app.test_client().get("/").data.decode()
    # The proof numbers are counted, not written: the fake table holds two rows and two
    # ATSes, so a hardcoded marketing figure would not survive this.
    assert '<div class="v">2</div><div class="k">tech jobs indexed' in page
    assert '<div class="v">2</div><div class="k">ATS providers read directly' in page
    # The freshness tile: an EXACT count, because a row without `first_seen` predates the
    # column and so cannot be new. The fake answers 1 to any filtered count, so a real
    # ratio shows rather than the total repeated — which a wrong denominator would give.
    assert (
        '<div class="v">1</div><div class="k">of them added in the last 7 days' in page
    )
    # Every tile is a counted number, and exactly counted. Three drafts failed that bar and
    # were removed rather than qualified: a typed-in cadence ("~6h"), an employer count and
    # a board count — neither of the last two derivable exactly from the served table.
    assert "~6h" not in page and "refreshes" not in page
    assert "employers" not in page and "boards indexed" not in page
    # The provenance claim, the removal policy, and the no-paid-placement claim.
    assert "employer's own board" in page
    assert "Closed roles get removed, and the exception is published." in page
    assert "22 days" in page  # checkable at the door, not only behind the wall
    assert "paid placement" in page
    # What signing in costs, stated before the button rather than in a policy page behind it.
    assert "stores your email address" in page
    assert "signing out drops the session" in page
    # …and the links that make the rest checkable.
    assert "github.com/sarthakjain004/headstart" in page
    # The ask still comes last, and the embedding-frame escape hatch survives (ADR-0112
    # changed the page around it, which is exactly when this gets dropped by accident).
    assert page.index("Why the jobs hold up") < page.index("Sign in to search")
    assert 'id="openout"' in page


def test_the_door_states_no_figure_it_cannot_count(auth_app, monkeypatch):
    """A table with no `first_seen` column drops the freshness tile rather than guessing.

    Replaces a vacuous check that asserted the absence of strings nothing generates. The
    real risk is the opposite one: a tile rendering `None`, `0` or an exception where the
    number is simply unavailable."""
    searcher = auth_app.app.view_functions["index"].__globals__["_searcher"]
    monkeypatch.setattr(
        searcher, "capabilities", replace(searcher.capabilities, has_first_seen=False)
    )
    page = auth_app.app.test_client().get("/").data.decode()
    assert "added in the last" not in page
    # Scoped to the tiles: the page's own prose opens "None of this has to be taken on faith".
    tiles = re.findall(r'<div class="v">([^<]*)</div>', page)
    assert tiles and all(t.strip() and "None" not in t for t in tiles), tiles
    # …and the tiles that CAN be counted are still there.
    assert "tech jobs indexed right now" in page
    assert "ATS providers read directly" in page


def test_coverage_counts_the_served_table_rather_than_asserting(app):
    """ADR-0113: the Data tab's numbers are measured, so they cannot go stale in prose."""
    d = app.app.test_client().get("/coverage").json
    assert d["total"] == 2
    # One of two rows carries each field — a real ratio, not a placeholder. One count per
    # field against the one total; `total` is not repeated onto every field.
    assert d["fields"]["posted_at"] == 1
    assert d["fields"]["min_years"] == 1
    # `remote` is never a coverage field: it is a facet, not a gap — a share would answer
    # "how many are remote", which the rail's own counts already answer. (Its provenance is
    # mixed, and four successive drafts described it wrongly; see ADR-0113.)
    assert "remote" not in d["fields"]
    assert "atses" not in d  # nothing reads it; the template has its own list


def test_coverage_is_behind_the_wall_like_everything_else(auth_app):
    assert auth_app.app.test_client().get("/coverage").status_code == 401


def test_coverage_reports_a_missing_column_as_unknown_not_zero(app, monkeypatch):
    """A column the table lacks is None. Zero would read as 'measured, and none have it'."""
    searcher = app.app.view_functions["coverage"].__globals__["_searcher"]
    monkeypatch.setattr(
        searcher, "capabilities", replace(searcher.capabilities, has_description=False)
    )
    monkeypatch.setattr(searcher, "_coverage", None)  # drop the per-process cache
    assert app.app.test_client().get("/coverage").json["fields"]["description"] is None


def test_the_signed_in_page_says_what_the_product_is(app):
    """A user inside the app should never have to guess what they are looking at."""
    page = app.app.test_client().get("/").data.decode()
    # On screen wherever they navigate, not only in the footer of a long results page.
    assert "Tech jobs read straight from company career boards" in page
    # One repo URL, server-side: the door and the Data tab both link into it, and a rename
    # must not be able to leave half the links dead.
    assert page.count("github.com/sarthakjain004/headstart") >= 3
    # The Data tab is always present — a limits page that can be switched off is not a
    # commitment — and the footer points at it.
    assert 'data-tab="data"' in page
    assert "What's in the index, and what isn't" in page
    # And the slot that names the current result list (browse vs ranked, ADR-0074).
    assert 'id="kind"' in page


def test_the_data_tab_states_scope_gaps_and_provenance(app):
    page = app.app.test_client().get("/").data.decode()
    assert "Where the jobs come from" in page
    assert "What is deliberately left out" in page
    assert "What the index does not know" in page
    assert "How a closed job leaves" in page
    assert "What is stored about you" in page
    # The ATS list is rendered from the index's own whitelist, not typed in.
    assert "Read from 2 providers" in page
    assert ">greenhouse<" in page and ">lever<" in page
    # ADR-0113: every claim links the decision behind it. The eviction section in particular
    # must carry ADR-0053 as well as ADR-0083 — an earlier draft described the window as
    # "hours, not minutes" and omitted the scope exclusion, which has no drain at all and was
    # measured serving one board's closed jobs for 22 days.
    assert "0083-evict-only-on-a-second-consecutive-absence.md" in page
    assert "0053-scope-eviction-on-scrape-outcome.md" in page
    # Whitespace-normalised: the template wraps these sentences, and HTML collapses the
    # newlines anyway, so asserting on the raw source would only pin the line breaks.
    flat = " ".join(page.split())
    assert "105 closed jobs, the oldest 22 days old" in flat
    assert "no-client-side-fix-for-replica-instability.md" in page
    # The date of the measurement itself (the doc is headed 2026-08-24) — an earlier fix
    # wrote 2026-08-23, which is ADR-0083's go-live date, not when this was measured.
    assert "2026-08-24" in page
    assert "hours, not minutes" not in flat
    # CONTEXT.md reserves "listing"/"posting"/"opening" for the raw ATS record; the user-facing
    # noun is "job". The word may still appear in this file's own explanation of that rule.
    body = page.split('id="panel-data"', 1)[1].split("</section>", 1)[0]
    for banned in ("listings", "openings", "postings"):
        assert banned not in body, banned


def test_the_resume_reader_says_the_text_leaves_the_service(sets_app, monkeypatch):
    """The one datum that goes to a third party is disclosed where it is pasted.

    The Data tab lists it too, but a person pasting a résumé should not have to have read
    another tab first — the disclosure belongs at the moment of the decision. Needs
    ``sets_app``: the Profile panel only renders where per-Account storage is configured."""
    page = _signed_in(sets_app, monkeypatch).get("/", base_url=_HTTPS).data.decode()
    # Bounded at the panel's own end tag: unbounded, this reached the Data tab further down
    # the document, which says "language model" too — so the assertion passed with the
    # disclosure deleted from profile.html entirely.
    body = page.split('id="panel-profile"', 1)[1].split("</section>", 1)[0]
    assert "language model" in body
    assert "outside HeadStart" in body
    # …and it must not claim the rest is sent nowhere: stars, saved searches and the profile
    # are uploaded to the private subscribers dataset.
    assert "sends nothing at all" not in body


def test_the_closed_tag_is_presented_as_an_inference(sets_app, monkeypatch):
    """`closed` is read off the job's absence from the index, and ADR-0023's prune can drop a
    still-open row — so the tab says what the tag actually means rather than asserting it."""
    page = _signed_in(sets_app, monkeypatch).get("/", base_url=_HTTPS).data.decode()
    body = page.split('id="panel-saved"', 1)[1]
    assert "no longer in our index" in body
    # The mechanism, right way round: an unreadable board is why a job STAYS (ADR-0053), so
    # the second cause is ADR-0023's wholesale board sweep, not a failed read.
    assert "dropped the whole board" in body
    assert "stopped being able to read that" not in body


def test_the_page_offers_a_skip_link_past_the_filter_rail(app):
    """After the search bar, not at the top of the document: `#q` autofocuses, so a skip link
    placed before it is never reached by tabbing forward — verified in a browser."""
    page = app.app.test_client().get("/").data.decode()
    assert 'class="skip" href="#results"' in page
    assert (
        page.index('class="go"') < page.index('class="skip"') < page.index('id="rail"')
    )


def test_the_page_hands_the_browser_the_rate_table_and_its_date(app):
    """The card labels convert client-side (ADR-0117), so the page needs the rates — the SAME
    table `build_filter` compiled the query from, handed over on window.CFG rather than
    fetched again, so a figure beside a row cannot disagree with the query that returned it.
    The date rides with them: a rate without its date is the defect the table exists to avoid,
    and the Data tab prints it in prose as well."""
    import json

    from headstart import fx

    page = app.app.test_client().get("/").data.decode()
    cfg = json.loads(re.search(r"window\.CFG = (.*?);</script>", page).group(1))
    table = fx.table()
    assert cfg["fx"]["rates"] == table["rates"]
    assert cfg["fx"]["as_of"] == table["as_of"]
    assert table["as_of"] in page


def test_the_data_tab_discloses_the_conversion_and_never_a_dateless_rate(app):
    """The one approximation on that page that changes which jobs come back rather than only
    how many carry a field. With no table there is no conversion to disclose, and the page has
    to say that instead — printing an empty date would be worse than saying nothing."""
    tpl = app.app.jinja_env.get_template("data.html")
    converted = " ".join(
        tpl.render(
            atses=["greenhouse"], repo="https://example.test", fx_as_of="2024-06-01"
        ).split()
    )
    assert "dated <b>2024-06-01</b>" in converted
    assert "not purchasing power" in converted
    assert (
        "left out of a converted bracket rather than compared one-to-one" in converted
    )
    assert "0117-the-salary-bracket-compares-across-currencies.md" in converted
    # No table: the bracket degrades to a single currency, and the page says so rather than
    # advertising a conversion that is not happening.
    degraded = " ".join(
        tpl.render(atses=["greenhouse"], repo="https://example.test").split()
    )
    assert "Nothing here converts one." in degraded
    assert "dated" not in degraded


def test_a_forgotten_auth_flag_cannot_produce_a_denial(app):
    """Forgetting `auth_on` alone must not make the page claim nothing is stored.

    Jinja renders an undefined name as falsy, so the conditional is written `if auth_on or
    alerts_on` with the "Nothing" case in the `else`. Written the other way round, a renderer
    that passed `alerts_on` but forgot `auth_on` printed a denial on a deployment that stores
    plenty. Note the guarantee is exactly that and no wider: with *every* flag absent the page
    still says "Nothing", which is correct — a caller supplying no flags at all is describing
    a deployment with neither feature."""
    tpl = app.app.jinja_env.get_template("data.html")
    # Rendered with `auth_on` simply absent, exactly as a forgetful caller would.
    out = tpl.render(atses=["greenhouse"], repo="https://example.test", alerts_on=True)
    assert "Nothing." not in out
    assert "email address" in out
    # …and the alerts-only branch names what /subscribe actually keeps: `_project_subscription`
    # stores the Query and the Search filters beside the address, not the address alone.
    assert "the search and filters that alert is for" in " ".join(out.split())
    # …and it still says "Nothing" when the deployment really does keep nothing.
    bare = tpl.render(atses=["greenhouse"], repo="https://example.test")
    assert "Nothing." in bare
    assert "the key your saved work hangs off" not in bare


def test_the_salary_tip_does_not_promise_conversion_without_rates(app, monkeypatch):
    """ADR-0117 falls back to one currency when the rate table is unreadable — and the copy
    beside the control has to fall back with it.

    The first version guarded only the date, so a deployment with no rates still told the user
    that other currencies "are converted so they still match", describing something that was
    not happening. Both branches are reachable, so both are asserted."""
    tpl = app.app.jinja_env.get_template("search.html")
    ctx = {
        "currencies": ["USD", "INR"],
        "keyword_scopes": [("title", "Job title", False)],
        "keyword_default_scope": "title",
        "has_description": True,
        "india_opts": [],
        "posted_opts": [],
        "seen_opts": [],
        "atses": ["greenhouse"],
        "has_first_seen": True,
    }
    with_rates = tpl.render(fx_as_of="2024-06-01", fx_converts=True, **ctx)
    assert "converted so they" in " ".join(with_rates.split())
    assert "2024-06-01" in with_rates

    # Two ways to reach the fallback, and the copy has to hold for both: no table at all, and
    # a table whose rates do not cover the currencies this deployment serves. `fx_converts` is
    # what `build_filter` effectively keys on, so it is what the claim is guarded by — guarding
    # on the date alone let the second case promise a conversion that was not happening.
    for rendered in (
        tpl.render(**ctx),
        tpl.render(fx_as_of="2024-06-01", fx_converts=False, **ctx),
    ):
        flat = " ".join(rendered.split())
        assert "Compared inside one currency only" in flat
        assert "are converted" not in flat


def test_the_door_and_the_app_share_one_palette():
    """The door inlines its own copy of the tokens (the wall gates /static), and that copy
    has already drifted once: two critique rounds lifted the app's surfaces for contrast and
    the door kept the old values, so signing in changed the background and the door held on
    to a contrast defect the app had fixed. Pinned rather than trusted to discipline."""
    ui = Path(__file__).resolve().parents[1] / "src" / "headstart" / "ui"
    css = (ui / "static" / "style.css").read_text()
    door = (ui / "templates" / "signin.html").read_text()
    for token in (
        "--ground",
        "--raise",
        "--raise-2",
        "--rule",
        "--rule-2",
        "--ink",
        "--ink-2",
    ):
        for value in re.findall(rf"{re.escape(token)}:(#[0-9A-Fa-f]{{6}})", door):
            assert f"{token}:{value}" in css, (
                f"the door sets {token}:{value}, which style.css does not — the two token "
                "blocks must move together"
            )


def test_board_arrivals_are_a_boards_first_tick_and_its_tech_stock_then(
    trends_app, tmp_path
):
    deltas = [
        {**_delta(_T1, "a", 5), "version": 1},
        {**_delta(_T1, "a", 9, family="non-tech"), "version": 1},
        {
            **_delta(_T1, "b", 100),
            "version": 1,
        },  # an earlier version's first tick counts
        _delta(_T2, "a", 6),  # the refit re-writes every Board
        _delta(_T2, "b", 3),
    ]
    # Over every version: a refit re-writes each Board at its first tick, and reading arrivals
    # off the newest version made every Board "found" there.
    arrivals = _trend_history(tmp_path, deltas=deltas)._board_arrivals
    assert arrivals == {"a": (_T1, 5), "b": (_T1, 100)}


def test_a_board_found_after_its_company_began_is_marked(company_trends, monkeypatch):
    """Both Citi Boards as one entry: the Eightfold one arrives at T2 with 3 openings."""
    history = company_trends.application.view_functions["trends"].__globals__[
        "_HISTORY"
    ]
    one = {
        "workday:citi/2": {
            "name": "Citi",
            "boards": ["workday:citi/2", "eightfold:citi.eightfold.ai"],
        }
    }
    monkeypatch.setattr(history, "_companies", one)
    monkeypatch.setattr(
        history,
        "_company_of",
        {b: "workday:citi/2" for b in one["workday:citi/2"]["boards"]},
    )
    d = company_trends.get("/trends?company=workday:citi/2").get_json()
    assert d["discovered"] == [
        {"ts": _T2, "company": "workday:citi/2", "boards": 1, "openings": 3}
    ]
    comparable = company_trends.get(
        "/trends?company=workday:citi/2&coverage=comparable"
    ).get_json()
    assert comparable["discovered"] == []
    narrowed = company_trends.get(
        "/trends?company=workday:citi/2&ats=workday"
    ).get_json()
    assert narrowed["discovered"] == []


def test_a_single_boards_first_tick_starts_its_line_and_is_not_marked(company_trends):
    d = company_trends.get("/trends?company=eightfold:citi.eightfold.ai").get_json()
    assert d["discovered"] == []


def test_a_board_that_brought_no_tech_openings_is_not_marked(
    company_trends, monkeypatch
):
    history = company_trends.application.view_functions["trends"].__globals__[
        "_HISTORY"
    ]
    one = {
        "workday:hpe/a": {"name": "Hpe", "boards": ["workday:hpe/a", "workday:hpe/new"]}
    }
    monkeypatch.setattr(history, "_companies", one)
    monkeypatch.setattr(
        history,
        "_company_of",
        {b: "workday:hpe/a" for b in one["workday:hpe/a"]["boards"]},
    )
    arrivals = dict(history._board_arrivals)
    arrivals["workday:hpe/new"] = (_T2, 0)  # its first tick held only non-tech
    monkeypatch.setattr(history, "_board_arrivals", arrivals)
    d = company_trends.get("/trends?company=workday:hpe/a").get_json()
    assert d["discovered"] == []


def test_search_narrows_to_the_boards_a_trend_hands_over(app):
    """`board=` scopes /search and /facets to one company's Boards, accounts or not."""
    from headstart import search

    args = app.app.test_request_context(
        "/search?board=workday:citi/2&board=eightfold:x"
    ).request.args
    assert search.scoped_boards_clause(args) == (
        "(lower(id) LIKE 'eightfold:x:%' OR lower(id) LIKE 'workday:citi/2:%')"
    )
    many = "&".join(f"board=b{i}" for i in range(search.MAX_SCOPED_BOARDS + 1))
    client = app.app.test_client()
    assert client.get("/search?" + many).status_code == 400
    assert client.get("/facets?" + many).status_code == 400


def test_a_found_boards_backlog_waits_out_the_new_window(
    company_trends, trends_app, monkeypatch, tmp_path
):
    """Eightfold's Citi Board is found at T2; its first-week `new` is its backlog, not hiring."""
    deltas = _COMPANY_DELTAS + [
        _delta(_T2, "eightfold:citi.eightfold.ai", 3, metric="new")
    ]
    history = _company_history(trends_app, monkeypatch, tmp_path, deltas, hold=True)
    held = company_trends.get(
        "/trends?company=eightfold:citi.eightfold.ai&metric=new"
    ).get_json()
    assert held["series"] == []  # nothing new yet: the three were its backlog
    monkeypatch.setattr(history, "_new_hold", {})
    counted = company_trends.get(
        "/trends?company=eightfold:citi.eightfold.ai&metric=new"
    ).get_json()
    assert [s["points"] for s in counted["series"]] == [[3, 3]]
    assert held["ledger_start"] == _T1


def test_every_board_waits_out_the_new_window_from_its_first_tick(trends_app, tmp_path):
    holds = _trend_history(
        tmp_path,
        deltas=[
            _delta("2026-09-13T00:00:00+00:00", "a", 5),
            _delta("2026-09-20T06:00:00+00:00", "b", 3),
        ],
    )._new_hold
    # the first tick's baseline waits too: the ledger's first week reads every backlog as new
    assert holds == {"a": "2026-09-20T00:00:00+00:00", "b": "2026-09-27T06:00:00+00:00"}


def test_new_counts_from_each_picks_own_first_week(
    company_trends, trends_app, monkeypatch, tmp_path
):
    holds = _company_history(trends_app, monkeypatch, tmp_path, hold=True)._new_hold
    d = company_trends.get(
        "/trends?company=workday:hpe/a&company=eightfold:citi.eightfold.ai"
    ).get_json()
    assert d["new_counted_from"] == {
        "workday:hpe/a": holds["workday:hpe/a"],
        "eightfold:citi.eightfold.ai": holds["eightfold:citi.eightfold.ai"],
    }


def test_a_held_week_is_a_gap_not_a_zero(company_trends, monkeypatch):
    """Before a pick's `new` counts, its line is unmeasured: a 0 drew a surge at the release."""
    history = company_trends.application.view_functions["trends"].__globals__[
        "_HISTORY"
    ]
    monkeypatch.setattr(
        history, "_new_hold", {"workday:hpe/a": _T3, "workday:hpe/b": _T3}
    )
    d = company_trends.get(
        "/trends?metric=new&split=company&company=workday:hpe/a&company=workday:citi/2"
    ).get_json()
    points = {s["name"]: s["points"] for s in d["series"]}
    assert points == {"workday:hpe/a": [None, None, 2], "workday:citi/2": [None, 0, 0]}
    summed = company_trends.get(
        "/trends?metric=new&company=workday:hpe/a&company=workday:citi/2"
    ).get_json()
    # a summed line starts with its earliest pick; the later one joins it as a marked step
    assert summed["series"][0]["points"] == [None, 0, 2]


def test_picks_a_view_leaves_out_are_named(company_trends):
    """Comparable from T1 keeps only Boards known then: Eightfold's Citi (found T2) is out."""
    d = company_trends.get(
        f"/trends?coverage=comparable&base={quote(_T1)}&split=company"
        "&company=workday:citi/2&company=eightfold:citi.eightfold.ai"
    ).get_json()
    assert d["uncounted"] == ["eightfold:citi.eightfold.ai"]
    assert [s["name"] for s in d["series"]] == ["workday:citi/2"]
    whole = company_trends.get(
        "/trends?company=workday:citi/2&company=eightfold:citi.eightfold.ai"
    ).get_json()
    assert whole["uncounted"] == []
    assert company_trends.get("/trends").get_json()["uncounted"] == []


def test_duplicate_removals_are_named_per_pick(company_trends, monkeypatch, tmp_path):
    """#649's ledger, summed across rules and Boards, at the charted run that shows it."""
    history = company_trends.application.view_functions["trends"].__globals__[
        "_HISTORY"
    ]
    ledger = tmp_path / "dedup_evictions.csv"
    ledger.write_text(
        "ts,board,count,rule\n"
        f"{_T2},workday:hpe/a,4,backing-requisition\n"
        f"{_T2},workday:hpe/b,3,workday-tenant\n"
        f"{_T1},workday:hpe/a,9,case-variant\n"  # at the first run: already in the start
        f"{_T3},workday:citi/2,2,alias:mirror\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(history, "_evictions", trend_history._load_evictions(ledger))
    d = company_trends.get(
        "/trends?split=company&company=workday:hpe/a&company=workday:citi/2"
    ).get_json()
    assert d["evicted"] == [
        {"ts": _T2, "company": "workday:hpe/a", "count": 7},
        {"ts": _T3, "company": "workday:citi/2", "count": 2},
    ]
    assert trend_history._load_evictions(tmp_path / "missing.csv") == {}


def test_a_refit_is_a_step_in_one_history_not_its_end(
    trends_app, monkeypatch, tmp_path
):
    """Version 2 runs T1–T2; a refit starts version 2001 at T3 with every Board re-written. Read
    in the older layout, the re-write is the Board's change against T2, as the migration stores
    it."""
    ledger = [
        {
            "ts": ts,
            "version": v,
            "metric": "stock",
            "family": "software-engineering",
            "band": "mid",
            "ats": "workday",
            "count": n,
        }
        for ts, v, n in [(_T1, 2, 10), (_T2, 2, 12), (_T3, 2001, 15), (_T3, 2, 99)]
    ]
    deltas = [
        {**_delta(_T1, "workday:hpe/a", 10), "version": 2},
        {**_delta(_T2, "workday:hpe/a", 2), "version": 2},
        {
            **_delta(_T3, "workday:hpe/a", 15),
            "version": 2001,
        },  # the refit's full re-write
    ]
    companies = {"workday:hpe/a": {"name": "Hpe", "boards": ["workday:hpe/a"]}}
    history = _trend_history(
        tmp_path, ledger=ledger, deltas=deltas, companies=companies
    )
    history._new_hold = {}
    monkeypatch.setattr(trends_app, "_HISTORY", history)
    d = (
        trends_app.app.test_client()
        .get("/trends?company=workday:hpe/a&split=company")
        .get_json()
    )
    assert d["stamps"] == [_T1, _T2, _T3]
    assert d["series"][0]["points"] == [10, 12, 15]
    assert d["counted_since"] == {"workday:hpe/a": _T1}


_REPO_FAMILIES = Path(__file__).resolve().parents[1] / "config" / "role_families.json"


def test_watched_roles_follow_a_family_by_either_name(
    trends_app, monkeypatch, tmp_path
):
    """The watchlist moved to v3 parents before their data landed; the AI drill must survive.
    It stays under AI / Machine Learning, not under Data Science as well."""
    rows = [
        {
            "ts": _T1,
            "version": 2,
            "metric": "stock",
            "family": family,
            "band": "all",
            "ats": "x",
            "count": n,
        }
        for family, n in [("ai-ml", 30), ("data-science", 10), ("watch:llm-genai", 8)]
    ]
    history = _trend_history(tmp_path, ledger=rows)
    history._family_successor = trend_history.family_successors(_REPO_FAMILIES)
    history._watch = {
        "watch:llm-genai": {"label": "LLM / GenAI", "parent": "ai-ml-data-science"}
    }
    monkeypatch.setattr(trends_app, "_HISTORY", history)
    client = trends_app.app.test_client()
    top = client.get("/trends").get_json()
    assert top["watch_parents"] == ["ai-ml"]
    drill = client.get("/trends?family=ai-ml&split=roles").get_json()
    assert [s["name"] for s in drill["series"]] == ["watch:llm-genai"]
    none = client.get("/trends?family=data-science&split=roles").get_json()
    assert none["series"] == []


def test_a_retired_family_reads_as_its_successor_once_that_has_data(
    trends_app, monkeypatch, tmp_path
):
    """A window spanning the switch draws one line, not one that stops and one that starts."""
    rows = [
        {
            "ts": ts,
            "version": v,
            "metric": "stock",
            "family": family,
            "band": "all",
            "ats": "x",
            "count": n,
        }
        for ts, v, family, n in [
            (_T1, 2, "ai-ml", 30),
            (_T2, 2, "ai-ml", 31),
            (_T3, 3001, "ai-ml-data-science", 40),
        ]
    ]
    history = _trend_history(tmp_path, ledger=rows)
    history._family_successor = trend_history.family_successors(_REPO_FAMILIES)
    monkeypatch.setattr(trends_app, "_HISTORY", history)
    client = trends_app.app.test_client()
    top = client.get("/trends").get_json()
    assert [(s["name"], s["points"]) for s in top["series"]] == [
        ("ai-ml-data-science", [30, 31, 40])
    ]
    old_link = client.get("/trends?family=ai-ml").get_json()
    assert old_link["family"] == "ai-ml-data-science"


def test_a_v3_family_before_its_data_is_all_its_predecessors(
    trends_app, monkeypatch, tmp_path
):
    """AI, ML & Data Science reads as AI / Machine Learning plus Data Science, not the larger."""
    rows = [
        {
            "ts": _T1,
            "version": 2,
            "metric": "stock",
            "family": family,
            "band": band,
            "ats": "x",
            "count": n,
        }
        for family, band, n in [
            ("ai-ml", "mid", 30),
            ("data-science", "mid", 10),
            ("devops", "mid", 5),
        ]
    ]
    history = _trend_history(tmp_path, ledger=rows)
    history._family_successor = trend_history.family_successors(_REPO_FAMILIES)
    history._family_labels = trend_history._family_labels(_REPO_FAMILIES)
    monkeypatch.setattr(trends_app, "_HISTORY", history)
    client = trends_app.app.test_client()
    d = client.get("/trends?family=ai-ml-data-science").get_json()
    assert d["family"] == "ai-ml-data-science" and d["family_known"] is True
    assert [s["points"] for s in d["series"]] == [[40]]
    unknown = client.get("/trends?family=nonsense-family").get_json()
    assert unknown["family_known"] is False
    # A real family with nothing in scope is empty, not unknown.
    empty = client.get("/trends?family=security").get_json()
    assert empty["family_known"] is True and empty["series"] == []


def test_hot_is_ranked_at_boot_from_the_history_the_trends_tab_reads(
    trends_app, monkeypatch, tmp_path
):
    """ADR-0230: no pipeline file; the Space ranks the Company directory from `_HISTORY`."""
    history = _company_history(trends_app, monkeypatch, tmp_path)
    seen = {}

    def rank(given, directory):
        seen.update(history=given, directory=directory)
        return {"window": {"base": _T1}, "lenses": {}, "counts": {"ranked": 0}}

    monkeypatch.setattr(trends_app.hot_ranking, "rank", rank)
    # A directory from before the Operator ranks nothing: every staffing firm would otherwise
    # read as an employer until the next run wrote one.
    assert trends_app._rank_hot(history) == {}
    for entry in history.companies.values():
        entry["operator"] = "employer"
    ranked = trends_app._rank_hot(history)
    assert ranked["window"]["base"] == _T1
    assert seen == {"history": history, "directory": history.companies}
    monkeypatch.setattr(trends_app, "_HOT", ranked)
    assert trends_app.app.test_client().get("/hot").get_json() == ranked


def test_a_hot_ranking_that_fails_darkens_hot_only(trends_app, monkeypatch, tmp_path):
    history = _company_history(trends_app, monkeypatch, tmp_path)
    for entry in history.companies.values():
        entry["operator"] = "employer"

    def broken(*_):
        raise KeyError("counted_since")

    monkeypatch.setattr(trends_app.hot_ranking, "rank", broken)
    assert trends_app._rank_hot(history) == {}
    monkeypatch.setattr(trends_app, "_HOT", {})
    assert trends_app.app.test_client().get("/hot").status_code == 503


def test_every_category_hands_search_the_jobs_its_trend_counts(
    trends_app, monkeypatch, tmp_path
):
    """For each category a trend can show, Search's id set is the size of the trend's count —
    old names, new names and merged names alike (AI, ML & Data Science opened as 0 jobs)."""
    from headstart import search

    successors = trend_history.family_successors(_REPO_FAMILIES)
    labels = trend_history._family_labels(_REPO_FAMILIES)
    # The data mid-transition: every retired name still assigned, and a few new ones too; then
    # a scope holding only some of a family's predecessors (Data Science, not AI / ML).
    everything = [
        *successors,
        "engineering-management",
        "software-engineering",
        "devops",
    ]
    for held in (everything, [n for n in everything if n != "ai-ml"]):
        counts = {name: k + 1 for k, name in enumerate(held)}
        history = _trend_history(
            tmp_path,
            ledger=[
                {
                    "ts": _T1,
                    "version": 2,
                    "metric": "stock",
                    "family": name,
                    "band": "mid",
                    "ats": "x",
                    "count": n,
                }
                for name, n in counts.items()
            ],
        )
        history._family_successor = successors
        history._family_labels = labels
        monkeypatch.setattr(trends_app, "_HISTORY", history)
        family_ids = trends_app._with_predecessors(
            {name: [f"x:{name}:{i}" for i in range(n)] for name, n in counts.items()},
            successors,
        )
        client = trends_app.app.test_client()
        for family in sorted(set(successors.values()) | set(held)):
            d = client.get(f"/trends?family={family}").get_json()
            trend = sum(s["points"][-1] or 0 for s in d["series"])
            args = trends_app.app.test_request_context(
                f"/search?board=x&family={d['family']}"
            ).request.args
            clause = search.scoped_jobs_clause(args, family_ids)
            assert len(re.findall(r"'x:[^']*'", clause)) == trend, family


def test_a_view_summing_picks_carries_each_picks_own_line(company_trends):
    """Five companies' Total read +362 of hiring where their Company breakdown summed to +306:
    summed whole, one company's step came out with every company's change that run. Each pick's
    own line lets the page take a step out of its company's part only."""
    d = company_trends.get(
        "/trends?company=workday:hpe/a&company=eightfold:citi.eightfold.ai"
    ).get_json()
    assert set(d["pick_series"]) == {"workday:hpe/a", "eightfold:citi.eightfold.ai"}
    for j in range(len(d["stamps"])):
        summed = [s["points"][j] for s in d["series"] if s["points"][j] is not None]
        picks = [p[j] for p in d["pick_series"].values() if p[j] is not None]
        assert sum(picks) == sum(summed)
    one = company_trends.get("/trends?company=workday:hpe/a").get_json()
    assert one["pick_series"] == {}, "one pick is its own sum"


def test_a_category_summing_picks_carries_each_picks_own_part(company_trends):
    """NVIDIA and Micron's categories summed +73 against their Total of +40: a category took no
    company's duplicate removals. Each pick's part of every category lets the page scale a
    company's part by its own removals, and the parts are the category."""
    d = company_trends.get(
        "/trends?company=workday:hpe/a&company=eightfold:citi.eightfold.ai"
    ).get_json()
    lines = {s["name"]: s["points"] for s in d["series"]}
    assert set(d["pick_parts"]) == set(lines)
    for name, parts in d["pick_parts"].items():
        for j, v in enumerate(lines[name]):
            got = [p[j] for p in parts.values() if p[j] is not None]
            assert sum(got) == (v or 0), (name, j)
        for company, part in parts.items():
            # 0 wherever its company is counted: a first opening there is hiring, not a join.
            for v, whole in zip(part, d["pick_series"][company]):
                assert (v is None) == (whole is None), (name, company)
    one = company_trends.get("/trends?company=workday:hpe/a").get_json()
    assert one["pick_parts"] == {}


def _with_turnover(trends_app, monkeypatch, tmp_path, rows: list[dict]) -> None:
    """The fixture's ledger plus turnover rows (ADR-0227), loaded as the Space loads them."""
    history = _company_history(
        trends_app, monkeypatch, tmp_path, _COMPANY_DELTAS + rows
    )
    history._turnover_since = _T2


_HPE_TURNOVER = [
    _delta(_T1, "workday:hpe/a", 99, metric="opened"),  # before the window's first run
    _delta(_T3, "workday:hpe/b", 2, metric="opened"),
    _delta(_T3, "workday:hpe/b", 7, metric="closed"),
    _delta(_T3, "workday:hpe/b", 1, family="ai-ml", metric="recounted_in"),
    _delta(_T3, "workday:hpe/b", 1, family="ai-ml", metric="recounted_out"),
    _delta(_T3, "workday:hpe/b", 1, family="all", metric="unscoped"),
    _delta(_T3, "workday:citi/2", 5, metric="opened"),
    _delta(_T2, "eightfold:citi.eightfold.ai", 3, metric="recounted_in"),  # found
]


def test_turnover_rows_leave_every_level_as_it_was(
    company_trends, trends_app, monkeypatch, tmp_path
):
    """A tick's turnover rides its delta file (ADR-0227). Replayed as levels, 99 opened jobs
    would have become 99 more openings on HPE's line."""
    before = company_trends.get("/trends?company=workday:hpe/a").get_json()
    _with_turnover(trends_app, monkeypatch, tmp_path, _HPE_TURNOVER)
    after = company_trends.get("/trends?company=workday:hpe/a").get_json()
    assert [s["points"] for s in after["series"]] == [
        s["points"] for s in before["series"]
    ]
    assert after["totals"] == before["totals"]
    hpe = {"workday:hpe/a": "hpe", "workday:hpe/b": "hpe"}
    replayed, _ = trends_app._HISTORY._replay_rows(None, False, hpe, [], None, None)
    assert {r["metric"] for r in replayed} == {"stock", "new"}
    assert (
        trend_history._family_weights(
            [{"metric": "opened", "family": "ai-ml", "count": 99}]
        )
        == {}
    )


def test_each_line_carries_the_turnover_its_change_is_made_of(
    company_trends, trends_app, monkeypatch, tmp_path
):
    """Opened and closed beside the net line, on every line of a pick (ADR-0227). The first run
    is None, since what landed there happened before the window."""
    _with_turnover(trends_app, monkeypatch, tmp_path, _HPE_TURNOVER)
    d = company_trends.get("/trends?company=workday:hpe/a").get_json()
    lines = {s["name"]: s["turnover"] for s in d["series"]}
    assert lines["software-engineering"] == {
        "opened": [None, 0, 2],
        "closed": [None, 0, 7],
        "recounted": [None, 0, 0],
    }
    assert lines["ai-ml"]["recounted"] == [None, 0, 0]
    assert d["turnover_since"] == _T2
    assert d["closures_unseen"] == {"workday:hpe/a": 1}
    split = company_trends.get(
        "/trends?split=company&company=workday:hpe/a&company=workday:citi/2"
    ).get_json()
    by_label = {s["label"]: s["turnover"]["opened"] for s in split["series"]}
    assert by_label == {"Hpe": [None, 0, 2], "Citi": [None, 0, 5]}


def test_the_index_has_turnover_and_it_is_the_sum_of_every_companys(
    company_trends, trends_app, monkeypatch, tmp_path
):
    """With no company picked, every line carries turnover too, summed from the same Board rows,
    so the index is exactly the sum over every company, run by run (ADR-0227). A found Board is
    recounted in the index as in its company."""
    _with_turnover(trends_app, monkeypatch, tmp_path, _HPE_TURNOVER)
    index = company_trends.get("/trends").get_json()
    turnover = {s["name"]: s["turnover"] for s in index["series"]}
    assert turnover["software-engineering"] == {
        "opened": [None, 0, 7],
        "closed": [None, 0, 7],
        "recounted": [None, 3, 0],
    }
    assert index["closures_unseen"] == {"": 1}
    every = "&".join(f"company={key}" for key in _COMPANY_DIRECTORY)
    companies = company_trends.get(f"/trends?split=company&{every}").get_json()
    for kind in ("opened", "closed", "recounted"):
        by_run = [
            sum(s["turnover"][kind][j] or 0 for s in companies["series"])
            for j in range(len(companies["stamps"]))
        ]
        in_index = [
            sum(t[kind][j] or 0 for t in turnover.values())
            for j in range(len(index["stamps"]))
        ]
        assert by_run == in_index, kind
    lever = company_trends.get("/trends?ats=lever").get_json()
    assert all(
        v in (None, 0) for s in lever["series"] for v in s["turnover"]["opened"]
    ), "an ATS filter narrows the index's turnover"


def test_the_index_shows_what_every_companys_view_shows_after_runs_are_left_out(
    company_trends, trends_app, monkeypatch, tmp_path
):
    """The figures each view displays reconcile (ADR-0227): the index leaves out, Board by
    Board, the runs a company's own line leaves out, so its opened and closed are the sum of
    what every company's view shows. A duplicate-removal change at the last run can move HPE
    (two Workday sites) and not Citi's one Workday site: HPE's turnover there is left out of the
    index as of HPE's line, Citi's stays in both."""
    _with_turnover(trends_app, monkeypatch, tmp_path, _HPE_TURNOVER)
    epoch = {
        "ts": _T3,
        "changed": ["duplicate removal changed"],
        "fields": ["dedup_version"],
    }
    monkeypatch.setattr(trends_app._HISTORY, "_epochs", [epoch])
    index = company_trends.get("/trends").get_json()
    assert index["turnover_left_out"] == [_T3]

    def shown(lines, left=()):
        return {
            kind: sum(
                v
                for t in lines
                for j, v in enumerate(t[kind])
                if j > 0 and v is not None and j not in left
            )
            for kind in ("opened", "closed")
        }

    in_index = shown([s["turnover"] for s in index["series"]])
    every = "&".join(f"company={key}" for key in _COMPANY_DIRECTORY)
    split = company_trends.get(f"/trends?split=company&{every}").get_json()
    by_company = {"opened": 0, "closed": 0}
    for s in split["series"]:
        # The page's rule for a pick's own line: a duplicate-removal change leaves out its run
        # (and the run after) only where the pick holds Boards it can move.
        touched = trend_netting.dedup_touched(_COMPANY_DIRECTORY[s["name"]]["boards"])
        for kind, n in shown([s["turnover"]], {2} if touched else ()).items():
            by_company[kind] += n
    assert in_index == by_company == {"opened": 5, "closed": 0}


@pytest.mark.parametrize(
    ("epoch_ts", "fields", "query", "left_out"),
    [
        # A tech-filter change on the middle run: its run and the run after, everywhere.
        (_T2, ["tech_filter_version"], "", [_T2, _T3]),
        # On the window's first run it is already in every line's start: nothing is left out,
        # as app.js leaves nothing out there (its settling run cut Amazon's real −7).
        (_T1, ["tech_filter_version"], "", []),
        # The index under comparable coverage is still the index: the same runs come out.
        (_T3, ["dedup_version"], "?coverage=comparable", [_T3]),
    ],
)
def test_the_index_leaves_out_the_runs_a_companys_line_leaves_out(
    company_trends, trends_app, monkeypatch, tmp_path, epoch_ts, fields, query, left_out
):
    """The Space's rule for the index mirrors the page's for a pick's line (ADR-0227), whatever
    the change, wherever it lands, and under comparable coverage too."""
    _with_turnover(trends_app, monkeypatch, tmp_path, _HPE_TURNOVER)
    epoch = {"ts": epoch_ts, "changed": ["a change"], "fields": fields}
    monkeypatch.setattr(trends_app._HISTORY, "_epochs", [epoch])
    index = company_trends.get(f"/trends{query}").get_json()
    assert index["turnover_left_out"] == left_out
    opened = [
        sum(s["turnover"]["opened"][j] or 0 for s in index["series"])
        for j in range(len(index["stamps"]))
    ]
    everywhere = "dedup_version" not in fields
    for j, ts in enumerate(index["stamps"]):
        if ts in left_out and everywhere:
            assert all(s["turnover"]["opened"][j] is None for s in index["series"]), ts
    if query:  # comparable: HPE's run-3 turnover is out, Citi's one-site Board's stays
        assert opened[2] == 5


def test_no_turnover_off_openings(company_trends, trends_app, monkeypatch, tmp_path):
    """Under `new` a line is a rolling level of fresh jobs, not a stock with a net change."""
    _with_turnover(trends_app, monkeypatch, tmp_path, _HPE_TURNOVER)
    d = company_trends.get("/trends?metric=new&company=workday:hpe/a").get_json()
    assert all("turnover" not in s for s in d["series"])
    assert d["closures_unseen"] == {}


def test_comparable_starts_its_window_where_all_coverage_does(company_trends):
    """Under Comparable the cohort's base was the last run before the asked start, so Google's
    Sep 15–20 window began one run earlier than under All coverage (1,502 against 1,494)."""
    everything = company_trends.get("/trends?company=workday:hpe/a").get_json()
    first, second = everything["stamps"][:2]
    between = (
        first[:11] + "12:34:56+00:00"
        if first[:10] == second[:10]
        else second[:10] + "T00:00:00+00:00"
    )
    assert first < between < second
    between = between.replace("+", "%2B")  # "+" in a query string reads as a space
    since = company_trends.get(
        f"/trends?company=workday:hpe/a&since={between}"
    ).get_json()
    held = company_trends.get(
        f"/trends?company=workday:hpe/a&coverage=comparable&base={between}"
    ).get_json()
    assert held["stamps"][0] == since["stamps"][0] == second


def test_a_company_key_is_found_whatever_its_case(company_trends):
    """A hand-typed `GOOGLE:careers.google.com` answered "not in the company directory"."""
    d = company_trends.get("/trends?company=WORKDAY:HPE/A").get_json()
    assert "error" not in d and d["series"]
    # Answered under the directory's own key, which the page adopts for its picks.
    lower = company_trends.get("/trends?company=workday:hpe/a").get_json()
    assert [c["key"] for c in d["companies"]] == [c["key"] for c in lower["companies"]]


def test_a_window_is_one_scope_whatever_instant_inside_a_run_gap_asks(trends_app):
    """The 7/30/90-day presets ask for now − N to the second; any instant between the same two
    runs holds the same runs, so it answers the same (#690, whose memo keyed on the runs a window
    holds; the index is now read from columns and needs none)."""
    stamps = list(trends_app._HISTORY.ticks)
    assert len(stamps) >= 3
    at = datetime.fromisoformat(stamps[1])
    early, late = (
        (at - timedelta(seconds=s)).isoformat(timespec="seconds") for s in (2, 1)
    )
    assert stamps[0] < early < late < stamps[1]
    client = trends_app.app.test_client()
    first = client.get("/trends", query_string={"since": early}).get_json()
    assert client.get("/trends", query_string={"since": late}).get_json() == first
