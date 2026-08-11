"""The Space app's request layer — `deploy/hf-space/app.py` (ADR-0035, ADR-0042).

Two things live here: `_build_filter`, the *reference* filter implementation (its own
docstring says so: it has diverged from `headstart.search.build_filter`), and the sign-in
wall — both had no coverage anywhere, because `deploy/` sits outside `testpaths` and
importing the app pulls in a model download, a SentenceTransformer and a LanceDB table.

So the heavy imports are stubbed in `sys.modules` and the module is loaded from its path —
the same importlib trick `tests/test_check_liveness.py` uses for `scripts/`. Only pure
string-building and Flask's test client are exercised; nothing here touches the network or
the index. Auth requests ride `base_url="https://localhost"` because the session cookie is
`Secure` and the test client honours that over plain http.
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
        "geo": _module("geo", where=lambda place: None, DROPDOWN=["bengaluru"]),
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
    # The Space imports the alerts package flat, as deploy-space.yml lays it down.
    import headstart.alerts.access as _access
    import headstart.alerts.identity as _identity
    import headstart.alerts.store as _store

    stubs["alerts"] = _module("alerts", access=_access, identity=_identity)
    stubs["alerts.store"] = _store
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


def _clause(app, **kw):
    base = dict(
        remote=False,
        max_years=None,
        ats=None,
        etype=None,
        india=None,
        location=None,
        company=None,
        has_salary=False,
        posted_within=None,
        seen_within=None,
    )
    base.update(kw)
    return app._build_filter(**base)


def test_no_filters_is_no_clause(app):
    assert _clause(app) is None


def test_first_seen_after_is_strictly_greater_than(app):
    # Strict `>`: a Watermark taken from a row's own first_seen must not re-select that row.
    clause = _clause(app, first_seen_after="2026-08-02T12:00:00+00:00")
    assert clause == "first_seen > '2026-08-02T12:00:00+00:00'"


def test_first_seen_after_is_reserialized_not_interpolated(app):
    # The value arrives as free text and lands in a where-clause, so it is parsed and
    # re-emitted; a quote cannot survive that round trip.
    with pytest.raises(ValueError):
        _clause(app, first_seen_after="2026-08-02' OR '1'='1")
    with pytest.raises(ValueError):
        _clause(app, first_seen_after="yesterday")


def test_first_seen_after_normalizes_sub_second_precision(app):
    clause = _clause(app, first_seen_after="2026-08-02T12:00:00.123456+00:00")
    assert clause == "first_seen > '2026-08-02T12:00:00+00:00'"


def test_first_seen_after_combines_with_other_filters(app):
    clause = _clause(
        app, remote=True, max_years=3, first_seen_after="2026-08-02T12:00:00+00:00"
    )
    assert clause.startswith("remote = true AND (min_years <= 3")
    assert clause.endswith("AND first_seen > '2026-08-02T12:00:00+00:00'")


def test_seen_within_still_works_beside_it(app):
    assert "first_seen >= '" in _clause(app, seen_within=6)


def test_unknown_ats_is_ignored_rather_than_interpolated(app):
    assert _clause(app, ats="'; DROP TABLE jobs; --") is None


# ---- the sign-in wall (ADR-0042) ----

_HTTPS = "https://localhost"  # the session cookie is Secure; plain http would drop it


def test_wall_off_keeps_the_page_open(app):
    client = app.app.test_client()
    assert b"jobs indexed" in client.get("/").data
    assert client.get("/search?q=").json == []
    assert client.get("/me").json == {"auth": False, "email": None}


def test_wall_on_serves_the_door_and_gates_the_api(auth_app):
    client = auth_app.app.test_client()
    page = client.get("/").data
    assert b"Sign in to search" in page
    assert b"jobs indexed" not in page
    assert client.get("/search?q=x").status_code == 401
    assert client.get("/trends").status_code == 401


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
