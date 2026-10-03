"""Historical selection must never make live Hiring now or Search use an old prefix."""

import gzip
import json
from pathlib import Path

import pytest
from test_space_app import _space_app
from test_trends_restated_history import METHODOLOGY
from test_trends_restated_history import candidate as _candidate
from test_trends_restated_history import packaged as _packaged

from headstart.trends import restated_history as artifact
from headstart.trends import trend_history

candidate = _candidate
packaged = _packaged


@pytest.fixture
def selected_app(packaged, tmp_path, monkeypatch):
    root, _, _ = packaged
    state = tmp_path / "fresh"
    trend_history.record_tick(
        state,
        "2026-10-03T00:00:00+00:00",
        {("lever:new-company", "stock", "software-engineering", "mid"): 99},
        {},
        METHODOLOGY,
    )
    (state / "company_directory.json").write_text(
        json.dumps(
            {"companies": [{"name": "New Company", "boards": ["lever:new-company"]}]}
        )
    )
    fresh = trend_history.TrendHistory.load(state, Path("config"))
    original_load = trend_history.TrendHistory.load
    original_selection = artifact.load
    monkeypatch.setattr(
        trend_history.TrendHistory,
        "load",
        lambda state, config: (
            fresh
            if str(state) == "/app/state/data/state"
            else original_load(state, config)
        ),
    )
    monkeypatch.setattr(artifact, "pull", lambda *a, **k: None)
    monkeypatch.setattr(
        artifact,
        "load",
        lambda local, config, legacy: original_selection(root, config, legacy),
    )
    with _space_app(root) as module:
        yield module, fresh


def test_history_selection_and_cache_identity(selected_app):
    module, fresh = selected_app
    client = module.app.test_client()
    preferred = client.get("/trends")
    old = client.get("/trends?history=legacy")
    assert preferred.status_code == old.status_code == 200
    assert preferred.json["history"]["kind"] == "restated"
    assert preferred.json["history"]["last_covered_tick"].startswith("2026-10-02")
    assert old.json["history"]["recomputed"] is False
    assert old.json["stamps"] == list(fresh.ticks)
    assert preferred.json["stamps"] != old.json["stamps"]
    zipped = client.get("/trends", headers={"Accept-Encoding": "gzip"})
    assert gzip.decompress(zipped.data) == preferred.data
    assert {history for history, _ in module._TRENDS_ANSWERED} >= {
        fresh,
        module._HISTORIES.restated,
    }
    assert client.get("/trends?history=bad").status_code == 400
    assert client.get("/trends?metric=new").status_code == 503


def test_fresh_hiring_and_search_stay_on_pipeline_history(selected_app):
    module, fresh = selected_app
    assert module._HISTORY is fresh
    expected_boards, expected_seen, expected_hot = module._derive_from_history(fresh)
    assert module._COMPANY_BOARDS == expected_boards
    assert module._FIRST_SEEN == expected_seen
    assert module._HOT == expected_hot
    client = module.app.test_client()
    assert (
        client.get("/companies/suggest?q=New").json["companies"][0]["name"]
        == "New Company"
    )
    assert (
        client.get("/companies/suggest?q=Acme&history=preferred").json["companies"][0][
            "name"
        ]
        == "Acme"
    )
    assert client.get("/facets?counts=total").json["newest_tick"] == fresh.ticks[-1]
    assert client.get("/job?id=absent").json["newest_tick"] == fresh.ticks[-1]
    page = client.get("/").data.decode()
    assert "Earlier history (not recomputed)" in page
    assert 'id="trends-history-note" role="status"' in page


def test_no_verified_generation_uses_explicit_legacy(tmp_path, monkeypatch):
    def unavailable(*a, **k):
        raise OSError("manifest unavailable")

    monkeypatch.setattr(artifact, "pull", unavailable)
    with _space_app(tmp_path) as module:
        assert module._HISTORIES.restated is None
        assert module._HISTORIES.reason == "OSError: manifest unavailable"
        client = module.app.test_client()
        assert client.get("/trends?history=restated").status_code == 503
        assert client.get("/search?q=").status_code == 200
