"""The Space MCP server against the real Space app — both directions of the contract (ADR-0253).

`tests/test_space_app.py` loads the real `deploy/hf-space/app.py` with only its heavy dependencies
stubbed. Its fixtures and history writers are imported here, not copied or moved, so the app this
server is tested against is the one the Space's own tests load, and a 3,500-line file other work
edits stays where it is. Each tool runs through a :class:`SpaceClient` whose `Fetch` is Flask's
test client — the port's second adapter — and what is asserted is the text an agent would read.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from urllib.parse import urlsplit

import pytest
import test_space_app as space_tests
from test_space_app import auth_app, trends_app  # noqa: F401 — fixtures, reused

from headstart.mcp_protocol.stdio import ToolFailure
from headstart.space_mcp import server
from headstart.space_mcp import space_client as sc
from headstart.space_mcp.tools import read_trends

#: A day after the fixture history's last tick (`test_space_app._T3`, 2026-08-13), so a window
#: counted back from "now" means the same ticks whatever day the suite runs.
_FIXTURE_NOW = datetime(2026, 8, 14, tzinfo=UTC)


def flask_fetch(test_client) -> sc.Fetch:
    """The `Fetch` port answered by the real app in-process, as HF's proxy would pass it on."""

    def fetch(url, headers, timeout_s):
        parts = urlsplit(url)
        answer = test_client.get(
            parts.path, query_string=parts.query, headers=dict(headers)
        )
        named = {k.lower(): v for k, v in answer.headers.items()}
        return sc.Reply(
            answer.status_code, named, sc._decoded(named, answer.get_data())
        )

    return fetch


def _client(module) -> sc.SpaceClient:
    return sc.SpaceClient(fetch=flask_fetch(module.app.test_client()))


@pytest.fixture
def companies_app(trends_app, monkeypatch, tmp_path):  # noqa: F811 — the imported fixture
    """The trends app with the three-company history installed, and the globals boot derives
    from a history rebuilt from it by the app's own `_derive_from_history`."""
    monkeypatch.setattr(read_trends, "_now", lambda: _FIXTURE_NOW)
    history = space_tests._company_history(trends_app, monkeypatch, tmp_path)
    company_boards, hot = trends_app._derive_from_history(history)
    monkeypatch.setattr(trends_app, "_COMPANY_BOARDS", company_boards)
    monkeypatch.setattr(trends_app, "_HOT", hot)
    return trends_app


@pytest.fixture
def parsed(companies_app, monkeypatch):
    """Every `SearchFilters` the app's `JobSearch.parse_filters` returned, in order."""
    seen = []
    real = companies_app._searcher.parse_filters

    def recording(args):
        filters = real(args)
        seen.append(filters)
        return filters

    monkeypatch.setattr(companies_app._searcher, "parse_filters", recording)
    return seen


@pytest.fixture
def scoped_boards(companies_app, monkeypatch):
    """Every list of `board=` values the app's Board clause was asked to scope."""
    seen = []
    real = companies_app.job_search.scoped_boards_clause

    def recording(args, *rest, **kwargs):
        seen.append(list(args.getlist("board")))
        return real(args, *rest, **kwargs)

    monkeypatch.setattr(companies_app.job_search, "scoped_boards_clause", recording)
    return seen


def test_every_reply_says_it_is_the_app_and_serves_this_servers_contract(
    companies_app,
):
    reply = flask_fetch(companies_app.app.test_client())(
        f"{sc.SPACE_URL}/facets", {}, 5
    )
    assert reply.headers["x-headstart"] == f"app; agent-api={sc.AGENT_API}"


def test_search_arguments_reach_the_app_as_the_filters_they_name(companies_app, parsed):
    text = server.call(
        _client(companies_app),
        "search_jobs",
        {"query": "backend engineer", "remote": True, "max_years": 5},
    )
    # `remote` arrives as the literal "true" `parse_filters` compares against — sent as
    # Python's "True", it would silently read as no filter at all.
    assert parsed and all(f.remote is True and f.max_years == 5 for f in parsed)
    assert '"Backend Engineer"' in text and '"Acme"' in text
    assert "Ordered by similarity to the query" in text


def test_a_company_name_is_the_company_boxs_substring_at_the_app(companies_app, parsed):
    text = server.call(_client(companies_app), "search_jobs", {"company": "Citi"})
    assert parsed and all(f.company == "Citi" for f in parsed)
    assert 'company name contains "Citi" (the site\'s company box)' in text


def test_a_board_key_scopes_every_board_of_its_company(companies_app, scoped_boards):
    """HPE is one Tenant split into two Workday sites; either site's key means both."""
    text = server.call(
        _client(companies_app), "search_jobs", {"company": "workday:hpe/b"}
    )
    assert scoped_boards and all(
        sorted(boards) == ["workday:hpe/a", "workday:hpe/b"] for boards in scoped_boards
    )
    assert "2 Boards" in text


def test_a_trend_reads_the_pickers_citi_and_says_which(companies_app):
    """Three directory companies are named Citi; the picker offers the one with the most
    openings, and so does the server — saying so."""
    text = server.call(
        _client(companies_app), "read_trends", {"companies": ["Citi"], "days": 7}
    )
    assert "workday:citi/2" in text
    assert "largest company of that name" in text
    assert "Newest trends tick" in text


def test_a_whole_index_trend_is_reported_without_its_drawing_arrays(companies_app):
    text = server.call(
        _client(companies_app), "read_trends", {"detail": "full", "days": 7}
    )
    assert "The whole index." in text and "Window " in text
    assert "reconcile" in text
    assert "netted" not in text and "steps_at" not in text


def test_a_value_the_app_would_drop_is_refused_in_the_apps_own_words(companies_app):
    with pytest.raises(ToolFailure, match="workdya"):
        server.call(_client(companies_app), "search_jobs", {"ats": "workdya"})


def test_a_filter_on_a_column_this_table_lacks_is_a_deployment_state(companies_app):
    """The fixture's table carries no salary columns, so a salary bound under `strict=1` is the
    Space saying what it cannot do, not a caller's mistake."""
    with pytest.raises(ToolFailure, match="Not on this deployment yet"):
        server.call(
            _client(companies_app),
            "search_jobs",
            {"salary_min": 3_000_000, "salary_currency": "INR"},
        )


def test_hiring_now_with_nothing_ranked_says_why(companies_app):
    """The fixture's companies are all under Hot's 25-opening floor or too new, so the app ranks
    none; the answer says so and why, rather than listing nothing."""
    text = server.call(_client(companies_app), "hiring_now", {})
    assert "No company qualified on this Lens this week." in text
    assert "Ranked 0 companies; not ranked: 1 counted for under 3 days" in text
    assert "2 with fewer than 25 openings" in text


def test_a_window_with_no_counts_says_so_rather_than_reconciling_nothing(
    companies_app, monkeypatch
):
    """A window that starts after the fixture's last tick (2026-08-13) holds none of them."""
    monkeypatch.setattr(read_trends, "_now", lambda: datetime(2026, 9, 1, tzinfo=UTC))
    text = server.call(_client(companies_app), "read_trends", {"days": 3})
    assert "No trend counts fall in the last 3 days" in text
    assert "reconcile" not in text


def test_the_read_routes_answer_anyone_with_the_wall_on(auth_app):  # noqa: F811
    """The sign-in wall is on (both of its secrets set), and the server sends no credential: the
    read routes are public so that anyone can use this server (ADR-0253's amendment)."""
    text = server.call(_client(auth_app), "search_jobs", {"query": "engineer"})
    assert "jobs match these filters" in text


@pytest.fixture
def salaried(companies_app, monkeypatch):
    """The fixture table as a migrated one: the ADR-0082 salary columns present and two
    currencies served — `JobSearch.capabilities` is the one object a test swaps for that."""
    capabilities = dataclasses.replace(
        companies_app._searcher.capabilities,
        has_min_salary_annual=True,
        currencies=["INR", "USD"],
    )
    monkeypatch.setattr(companies_app._searcher, "capabilities", capabilities)
    return companies_app


def test_every_search_argument_reaches_the_app_as_the_filter_it_names(salaried, parsed):
    server.call(
        _client(salaried),
        "search_jobs",
        {
            "query": "backend engineer",
            "company": "Acme",
            "remote": True,
            "has_salary": True,
            "max_years": 5,
            "employment_type": "contract",
            "india_place": "bengaluru",
            "location": "Pune",
            "salary_min": 3_000_000,
            "salary_max": 9_000_000,
            "salary_currency": "INR",
            "posted_within_days": 7,
            "first_seen_within_hours": 24,
            "keyword": "rust",
            "keyword_in": "title",
            "ats": "greenhouse",
            "sort": "salary",
        },
    )
    got = {
        field: getattr(parsed[0], field)
        for field in (
            "company",
            "remote",
            "has_salary",
            "max_years",
            "etype",
            "india",
            "location",
            "salary_min",
            "salary_max",
            "salary_currency",
            "posted_within",
            "seen_within",
            "kw",
            "kw_in",
            "ats",
        )
    }
    assert got == {
        "company": "Acme",
        "remote": True,
        "has_salary": True,
        "max_years": 5,
        "etype": "contract",
        "india": "bengaluru",
        "location": "Pune",
        "salary_min": 3_000_000,
        "salary_max": 9_000_000,
        "salary_currency": "INR",
        "posted_within": 7,
        "seen_within": 24,
        "kw": "rust",
        "kw_in": "title",
        "ats": "greenhouse",
    }


def test_a_salary_bound_on_a_migrated_table_reaches_it(salaried, parsed):
    text = server.call(
        _client(salaried),
        "search_jobs",
        {"salary_min": 3_000_000, "salary_currency": "INR", "sort": "salary"},
    )
    assert parsed[0].salary_min == 3_000_000
    assert "salary at least 3,000,000 INR a year" in text
    assert "highest salary first, in INR across every match" in text


def test_a_category_hands_over_one_companys_jobs_of_that_category(
    companies_app, monkeypatch
):
    """`category` needs a directory company: "Citi" is read as the picker's Citi, and the app
    narrows its Board to that company's jobs of the category by id (ADR-0057)."""
    monkeypatch.setattr(
        companies_app,
        "_FAMILY_IDS",
        {"software-engineering": ["workday:citi/2:7", "workday:hpe/a:1"]},
    )
    monkeypatch.setattr(
        companies_app, "_KNOWN_FAMILIES", frozenset({"software-engineering"})
    )
    clauses = []
    real = companies_app.job_search.scoped_jobs_clause

    def recording(*args, **kwargs):
        clause = real(*args, **kwargs)
        clauses.append(clause)
        return clause

    monkeypatch.setattr(companies_app.job_search, "scoped_jobs_clause", recording)
    text = server.call(
        _client(companies_app),
        "search_jobs",
        {"company": "Citi", "category": "software-engineering"},
    )
    assert clauses and all(
        "workday:citi/2:7" in clause and "hpe" not in clause for clause in clauses
    )
    assert "category needs a directory company" in text


def test_hot_rows_the_tab_hides_are_left_out_by_the_apps_own_list(
    companies_app, monkeypatch
):
    """The app names the Operators it hides (`hidden_by_default`, ADR-0238's amendment); the
    server reads that list, so the page and the agent leave out the same rows."""
    row = {
        "boards": ["workday:x"],
        "atses": ["workday"],
        "stock": 300,
        "net": 20,
        "opened": 40,
        "closed": 20,
        "closures_uncounted_boards": 0,
        "boards_in_scope": 1,
        "rate": 13,
    }
    hot = {
        "window": {"base": "2026-08-11", "from": "2026-08-12", "to": "2026-08-13"},
        "lenses": {
            "expansion": [
                {
                    **row,
                    "key": "workday:acme",
                    "company": "Acme",
                    "operator": "employer",
                },
                {
                    **row,
                    "key": "workday:temps",
                    "company": "Temps Inc",
                    "operator": "staffing",
                },
            ],
            "volume": [],
            "rate": [],
        },
        "counts": {"ranked": 2, "min_stock": 25},
        "hidden_by_default": ["staffing", "aggregator"],
    }
    monkeypatch.setattr(companies_app, "_HOT", hot)
    text = server.call(_client(companies_app), "hiring_now", {})
    assert '"Acme"' in text and '"Temps Inc"' not in text
    assert "1 aggregator and staffing rows hidden" in text
