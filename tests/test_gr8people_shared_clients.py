"""The alias writer must see exact completeness before replacing its ledger."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fake_fetcher import FakeFetcher, FakeResponse

from headstart.network import http

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "gr8people_shared_clients", ROOT / "scripts/validate/gr8people_shared_clients.py"
)
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def run_validator(monkeypatch, tmp_path, rows_by_host, totals):
    ledger = tmp_path / "data/validate/liveness"
    aliases = tmp_path / "data/validate/aliases"
    ledger.mkdir(parents=True)
    aliases.mkdir()
    (ledger / "gr8people.csv").write_text(
        "ats,tenant,url,status,jobs,checked_at\n"
        + "".join(
            f"gr8people,{host},https://{host}/jobs,live,{totals[host]},2026-10-02\n"
            for host in rows_by_host
        )
    )
    alias_path = aliases / "gr8people.csv"
    previous = "ats,duplicate,canonical,signal,resolved_to,checked_at\ngr8people,b.workgr8.com,a.workgr8.com,shared-reqs,org/1,2026-10-01\n"
    alias_path.write_text(previous)
    page = '<title>Search Careers at Acme</title>assets.gr8people.com<script id="__NEXT_DATA__">{"props":{"visit":{"orgId":"org","clientId":"1"}}}</script>'

    def route(method, url, _kwargs):
        if method == "GET":
            return FakeResponse(text=page)
        host = url.split("/")[2]
        return FakeResponse(
            text=json.dumps(
                {
                    "data": {
                        "searchJobs": {
                            "results": {
                                "nodes": rows_by_host[host],
                                "totalCount": totals[host],
                                "pageInfo": {"hasNextPage": False},
                            }
                        }
                    }
                }
            )
        )

    monkeypatch.setattr(http, "DEFAULT_FETCHER", FakeFetcher(route))
    monkeypatch.setattr(validator, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["gr8people_shared_clients.py", "--apply"])
    return alias_path, previous


def test_a_tolerated_ingestion_shortfall_cannot_overwrite_existing_aliases(
    monkeypatch, tmp_path
):
    recorded = json.loads(
        (ROOT / "tests/fixtures/gr8people_postings.json").read_text()
    )[0]
    rows = [{**recorded, "key": str(1000 + i)} for i in range(100)]
    alias_path, previous = run_validator(
        monkeypatch,
        tmp_path,
        {"a.workgr8.com": rows, "b.workgr8.com": rows},
        {"a.workgr8.com": 100, "b.workgr8.com": 101},
    )
    with pytest.raises(SystemExit, match="refusing a partial alias scan"):
        validator.main()
    assert alias_path.read_text() == previous


def test_a_diverged_pair_reports_directional_differences_before_unburying(
    monkeypatch, tmp_path, capsys
):
    rows = json.loads((ROOT / "tests/fixtures/gr8people_postings.json").read_text())
    alias_path, _ = run_validator(
        monkeypatch,
        tmp_path,
        {"a.workgr8.com": rows[:2], "b.workgr8.com": [rows[0], rows[2]]},
        {"a.workgr8.com": 2, "b.workgr8.com": 2},
    )
    validator.main()
    output = capsys.readouterr().out
    assert "diverged b.workgr8.com / a.workgr8.com: 1 / 1" in output
    assert len(alias_path.read_text().splitlines()) == 1
