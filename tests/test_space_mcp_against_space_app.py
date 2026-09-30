"""The Space MCP server against the real Space app — both directions of the contract (ADR-0253).

`tests/test_space_app.py` loads the real `deploy/hf-space/app.py` with only its heavy dependencies
stubbed. Its fixtures and history writers are imported here, not copied or moved, so the app this
server is tested against is the one the Space's own tests load, and a 3,500-line file other work
edits stays where it is. Each tool runs through a :class:`SpaceClient` whose `Fetch` is
`space_client.wsgi_fetch` — the port's in-process adapter, the one the Space's own `/mcp` serves
through (ADR-0267) — and what is asserted is the text an agent would read. The last section posts
MCP messages to that route itself, in both protocol eras.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest
import test_space_app as space_tests
from test_space_app import auth_app, trends_app  # noqa: F401 — fixtures, reused

from headstart.mcp_protocol.messages import ToolFailure
from headstart.serving import job_search
from headstart.space_mcp import server
from headstart.space_mcp import space_client as sc
from headstart.space_mcp.tools import company_profile, get_job, read_trends, search_jobs

#: A day after the fixture history's last tick (`test_space_app._T3`, 2026-08-13), so a window
#: counted back from "now" means the same ticks whatever day the suite runs.
_FIXTURE_NOW = datetime(2026, 8, 14, tzinfo=UTC)


def _client(module) -> sc.SpaceClient:
    return sc.SpaceClient(fetch=sc.wsgi_fetch(module.app))


@pytest.fixture
def companies_app(trends_app, monkeypatch, tmp_path):  # noqa: F811 — the imported fixture
    """The trends app with the three-company history installed, and the globals boot derives
    from a history rebuilt from it by the app's own `_derive_from_history`."""
    monkeypatch.setattr(read_trends, "_now", lambda: _FIXTURE_NOW)
    monkeypatch.setattr(company_profile, "_now", lambda: _FIXTURE_NOW)
    history = space_tests._company_history(trends_app, monkeypatch, tmp_path)
    company_boards, first_seen, hot = trends_app._derive_from_history(history)
    monkeypatch.setattr(trends_app, "_COMPANY_BOARDS", company_boards)
    monkeypatch.setattr(trends_app, "_FIRST_SEEN", first_seen)
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
    reply = sc.wsgi_fetch(companies_app.app)(f"{sc.SPACE_URL}/facets", {}, 5)
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


def test_a_concise_search_asks_the_app_for_the_total_alone(companies_app, monkeypatch):
    """ADR-0274: `counts=total` reaches the app, which counts no option; `detail=full` still
    gets every option's count."""
    answered = []
    real = companies_app._searcher.facets

    def recording(args, **kwargs):
        counted = real(args, **kwargs)
        answered.append((args.get("counts"), counted))
        return counted

    monkeypatch.setattr(companies_app._searcher, "facets", recording)
    client = _client(companies_app)
    concise = server.call(client, "search_jobs", {"query": "engineer"})
    full = server.call(client, "search_jobs", {"query": "engineer", "detail": "full"})
    [(asked, total_only), (asked_full, strip)] = answered
    assert asked == "total" and total_only["facets"] == {} and total_only["total"]
    assert asked_full is None and strip["facets"]["remote"]
    assert strip["total"] == total_only["total"]
    assert "remote=true: " in full and "remote=true: " not in concise
    # ADR-0355: only the full answer asks where the matching jobs are, and the app says it.
    assert "places" in strip and "places" not in total_only
    assert "Where the " in full and "Where the " not in concise


def test_a_company_name_is_the_company_boxs_substring_at_the_app(companies_app, parsed):
    text = server.call(_client(companies_app), "search_jobs", {"company": "Citi"})
    assert parsed and all(f.company == "Citi" for f in parsed)
    assert 'company name contains "Citi" (the site\'s company box)' in text


def test_a_category_alone_is_read_from_its_family_table_at_the_app(
    companies_app, parsed, monkeypatch
):
    """P1-5 of the round-2 critique (ADR-0322): `family=` without `board=` passes `strict=1` and
    reads the family's table; the age window and the experience floor arrive parsed."""
    read = []

    class Tables:
        def table(self, family):
            read.append(family)
            return space_tests._Table()

    monkeypatch.setattr(companies_app, "_KNOWN_FAMILIES", frozenset({"security"}))
    monkeypatch.setattr(companies_app, "_FAMILY_IDS", {"security": []})
    monkeypatch.setattr(companies_app._searcher, "families", Tables())
    text = server.call(
        _client(companies_app),
        "search_jobs",
        {"category": "security", "required_years_at_least": 5},
    )
    assert read and set(read) == {"security"}
    assert parsed and all(
        f.max_age_days == 365 and f.required_years_at_least == 5 for f in parsed
    )
    assert "category security" in text and '"Backend Engineer"' in text


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
    assert "arithmetic check passed" in text and "Openings listed: " in text
    assert "netted" not in text and "steps_at" not in text


def test_a_trend_category_the_app_does_not_know_is_refused(companies_app):
    """`/trends` has no `strict`: an unknown family answers an empty window that reconciles.
    Installed without `config/` (uvx), `category` is a free string, so the server reads the
    app's `family_known` instead; called past the schema, as that install's calls arrive."""
    client = _client(companies_app)
    known = server.call(client, "read_trends", {"category": "software-engineering"})
    assert "Category: " in known and "arithmetic check" in known
    with pytest.raises(ToolFailure, match="'nonsense-family'"):
        read_trends.answer(
            client,
            {
                "category": "nonsense-family",
                "days": 30,
                "detail": "concise",
                "coverage": "all",
                "measure": "openings",
            },
        )


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
    assert "No trend counts fall between 2026-08-29 and now" in text
    assert "arithmetic" not in text


def test_the_read_routes_answer_anyone_with_the_wall_on(auth_app):  # noqa: F811
    """The sign-in wall is on (both of its secrets set), and the server sends no credential: the
    read routes are public so that anyone can use this server (ADR-0258)."""
    text = server.call(_client(auth_app), "search_jobs", {"query": "engineer"})
    assert "1 job matches these filters" in text


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
            "country": "IN",
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
            "country",
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
        "country": "IN",
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
    assert "salary range reaching 3,000,000 INR a year or more" in text
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
    text = server.call(_client(companies_app), "hiring_now", {"lens": "expansion"})
    assert '"Acme"' in text and '"Temps Inc"' not in text
    assert "1 aggregator and staffing rows hidden" in text


# ---- get_job and similar_to (ADR-0277) ----


def test_get_job_restates_the_spaces_own_bounds():
    assert get_job.MAX_IDS == job_search.MAX_JOB_IDS
    assert get_job.ID_MAX_CHARS == job_search.JOB_ID_MAX_CHARS
    assert get_job.SPACE_DESCRIPTION_LIMIT == job_search.JOB_DESCRIPTION_LIMIT


def test_search_jobs_restates_the_spaces_places_value():
    assert search_jobs.FACET_PLACES == job_search.FACET_PLACES


def test_get_job_reads_a_posting_and_names_the_missing_at_the_app(
    companies_app, monkeypatch
):
    monkeypatch.setattr(companies_app, "_UNCONFIRMED", frozenset({"greenhouse:acme:1"}))
    text = server.call(
        _client(companies_app),
        "get_job",
        {"ids": ["greenhouse:acme:1", "greenhouse:gone:9"]},
    )
    assert text.startswith("Read 1 of 2 jobs.")
    assert '1. "Backend Engineer" at "Acme"' in text
    assert 'department "Engineering"' in text
    assert '"Build the payments API."\n"Own it end to end."' in text
    assert "latest scrape did not find it" in text
    # The directory lacks its Board, but the fixture table counts 1 on every filtered count,
    # so the index serves it.
    assert 'Not in the index now: "greenhouse:gone:9". Most often it has closed' in text
    assert "Data as of the trends tick" in text


def test_similar_to_reaches_the_app_as_like_on_both_routes(companies_app, monkeypatch):
    asked = []

    def recorded(name):
        real = getattr(companies_app._searcher, name)

        def recording(args, *rest, **kwargs):
            asked.append((name, args.get("like"), args.get("q")))
            return real(args, *rest, **kwargs)

        return recording

    for name in ("run", "facets"):
        monkeypatch.setattr(companies_app._searcher, name, recorded(name))
    text = server.call(
        _client(companies_app), "search_jobs", {"similar_to": "greenhouse:acme:1"}
    )
    assert sorted(asked) == [
        ("facets", "greenhouse:acme:1", None),
        ("run", "greenhouse:acme:1", None),
    ]
    assert 'Ordered by similarity to job "greenhouse:acme:1" (itself left out)' in text


# ---- a company looked up, and its profile (ADR-0275) ----


def test_find_company_offers_one_entry_per_name_as_the_picker_does(companies_app):
    """Three directory companies are named Citi; the picker offers only the largest, and so
    does the tool, saying a smaller one of the same name is reached by its Board key."""
    text = server.call(_client(companies_app), "find_company", {"name": "Citi"})
    assert '1 directory company for "Citi"' in text
    assert "key workday:citi/2 · exact name · 44 tech openings" in text
    assert "eightfold:citi.eightfold.ai" not in text
    assert "only the largest is listed" in text


def test_a_search_whose_company_matched_nothing_offers_the_directory_companies(
    companies_app, monkeypatch
):
    """The fixture table matches every clause, so the app's nothing-matched answer is staged.
    The fixture's names are too short for a typo match (five letters), so "Hp" is a prefix."""
    monkeypatch.setattr(companies_app._searcher, "run", lambda args, **_: [])
    monkeypatch.setattr(
        companies_app._searcher,
        "facets",
        lambda args, **_: {"total": 0, "facets": {}, "blocking": "company"},
    )
    text = server.call(_client(companies_app), "search_jobs", {"company": "Hp"})
    assert 'no company name contains "Hp"' in text
    assert (
        '"Hpe" — key workday:hpe/a, workday, 2 Boards, 13 openings, prefix match'
        in text
    )


def test_a_profile_reads_every_route_for_every_board_of_its_company(
    companies_app, scoped_boards
):
    """HPE is one Tenant split into two Workday sites: either site's key means both, in the
    facet counts (every age, and within a year: ADR-0338), the locations and the levels
    alike."""
    text = server.call(
        _client(companies_app), "company_profile", {"company": "workday:hpe/b"}
    )
    assert len(scoped_boards) == 4 and all(
        sorted(boards) == ["workday:hpe/a", "workday:hpe/b"] for boards in scoped_boards
    )
    assert text.startswith('Company: "Hpe" (workday:hpe/a, 2 Boards,')
    assert "Its Boards: workday:hpe/a, workday:hpe/b." in text
    assert "Job categories now, largest first:" in text
    # The fixture table answers its two rows, Berlin and Remote, to every scan, and 1 to every
    # filtered count.
    assert (
        'Germany 1 ("Berlin" 1). No country is read from the places of 1 ("Remote" 1).'
        in text
    )
    # Its two rows state no experience, each counted once.
    assert "Experience not stated 2" in text
    assert "Of the 1 jobs search serves on its Boards" in text
    assert (
        "search also serves 7 jobs on its Boards that the tech filter sets aside"
        in text
    )


def test_requirements_reach_the_app_as_a_role_and_its_filters(companies_app, parsed):
    """The fixture table answers its two rows to every read: the sample is both, one of them
    described, and the filters reach the app as the search filters they name (ADR-0324)."""
    text = server.call(
        _client(companies_app),
        "role_requirements",
        {"query": "backend engineer", "remote": True, "country": "DE"},
    )
    assert parsed[-1].remote is True and parsed[-1].country == "DE"
    assert text.startswith(
        'What postings closest to "backend engineer" ask for: counted over 2 distinct '
        "postings, of 1 "
    )
    assert "as a share of the 1 sampled postings with a description" in text
    assert "Remote: 2 of 2." in text
    # Two postings are anecdotes: counts, not shares (ADR-0367).
    assert "Only 2 distinct postings, under 30" in text


def test_a_requirements_category_without_role_assignments_is_the_deployments_state(
    companies_app,
):
    """The fixture pulls no role assignments, so the app cannot sample a category, and says so
    as a deployment's state rather than an empty answer."""
    with pytest.raises(ToolFailure, match="role assignments"):
        server.call(
            _client(companies_app), "role_requirements", {"category": "security"}
        )


# ---- the Space's own /mcp, in both protocol eras (ADR-0267) ----

#: One call of each registered tool, and a phrase its answer carries.
_EACH_TOOL = [
    ("search_jobs", {"query": "backend engineer"}, '"Backend Engineer"'),
    ("get_job", {"ids": ["greenhouse:acme:1"]}, '"Build the payments API."'),
    ("read_trends", {"days": 7}, "Newest trends tick"),
    ("hiring_now", {}, "No company qualified on this Lens this week."),
    ("find_company", {"name": "Citi"}, "key workday:citi/2"),
    ("company_profile", {"company": "workday:hpe/b"}, 'Germany 1 ("Berlin" 1)'),
    ("role_requirements", {"query": "backend engineer"}, "counted over 2 distinct"),
]

_MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
}


def test_every_tool_is_registered_in_this_list():
    assert [name for name, _, _ in _EACH_TOOL] == list(server.BY_NAME)


def _post(client, message, headers=None):
    return client.post("/mcp", json=message, headers=headers or {})


def test_a_legacy_client_initializes_lists_and_calls_every_tool(companies_app):
    """claude.ai's connector setup and older clients: the 2025-11-25 handshake, then one POST
    per message, with the negotiated version in a header after it."""
    client = companies_app.app.test_client()
    hello = _post(
        client,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}},
        },
    )
    assert hello.status_code == 200
    assert hello.json["result"]["serverInfo"]["name"] == server.NAME
    assert "Mcp-Session-Id" not in hello.headers
    after = {"MCP-Protocol-Version": "2025-11-25"}
    initialized = _post(
        client, {"jsonrpc": "2.0", "method": "notifications/initialized"}, after
    )
    assert initialized.status_code == 202 and initialized.get_data() == b""
    listed = _post(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, after)
    assert listed.json["result"]["tools"] == server.TOOLS
    for n, (name, arguments, phrase) in enumerate(_EACH_TOOL, start=3):
        called = _post(
            client,
            {
                "jsonrpc": "2.0",
                "id": n,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            after,
        )
        result = called.json["result"]
        assert called.status_code == 200 and "isError" not in result, (name, result)
        assert phrase in result["content"][0]["text"], name


def test_a_modern_client_discovers_lists_and_calls_every_tool_statelessly(
    companies_app,
):
    """claude.ai's chats and Claude Code: 2026-07-28, every request carrying its version in
    `_meta` and mirrored into headers, no handshake."""
    client = companies_app.app.test_client()

    def modern(method, request_id, headers=None, **params):
        return _post(
            client,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": {**params, "_meta": _MODERN_META},
            },
            {
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": method,
                **(headers or {}),
            },
        )

    discovered = modern("server/discover", 1)
    assert discovered.status_code == 200
    assert "2026-07-28" in discovered.json["result"]["supportedVersions"]
    assert discovered.json["result"]["instructions"] == server.INSTRUCTIONS
    listed = modern("tools/list", 2).json["result"]
    assert listed["tools"] == server.TOOLS and listed["resultType"] == "complete"
    for n, (name, arguments, phrase) in enumerate(_EACH_TOOL, start=3):
        called = modern(
            "tools/call", n, {"Mcp-Name": name}, name=name, arguments=arguments
        )
        result = called.json["result"]
        assert called.status_code == 200 and "isError" not in result, (name, result)
        assert result["resultType"] == "complete"
        assert phrase in result["content"][0]["text"], name
