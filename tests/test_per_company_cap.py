"""`per_company_cap.spread`: a relevance page lists each company's first N before the others'
(ADR-0352)."""

from headstart.serving.per_company_cap import (
    MORE_FROM_COMPANY,
    PAST_COMPANY_CAP,
    company,
    spread,
)


def _row(job_id, company_name, title=None, location="London"):
    return {
        "id": job_id,
        "company": company_name,
        "title": title or f"Engineer {job_id}",
        "location": location,
    }


def test_a_companys_rows_past_the_cap_follow_every_kept_row_in_rank_order():
    """p2_07 of the round-4 critique: Reflection held 9 of 10 rows."""
    rows = [_row(f"r{i}", "Reflection") for i in range(5)] + [
        _row("n1", "Neara"),
        _row("r5", "Reflection"),
        _row("m1", "Monzo"),
    ]
    out = spread(rows, 3)
    assert [r["id"] for r in out] == ["r0", "r1", "r2", "n1", "m1", "r3", "r4", "r5"]
    assert [r.get(MORE_FROM_COMPANY) for r in out[:5]] == [3, 3, 3, None, None]
    assert all(r[PAST_COMPANY_CAP] for r in out[5:])
    assert not any(PAST_COMPANY_CAP in r for r in out[:5])


def test_nothing_is_dropped_and_a_list_under_the_cap_keeps_its_order():
    rows = [_row("a", "A"), _row("b", "B"), _row("a2", "A")]
    assert spread(rows, 3) == rows
    assert not any(MORE_FROM_COMPANY in r for r in rows)


def _capital_one(n, board, title="Machine Learning Engineer 5", place="McLean, VA, US"):
    return _row(f"{board}:{n}", "Capital One", title, place)


_WORKDAY = "workday:capitalone/Capital_One"
_FRONT = "radancy:www.capitalonecareers.com"


def test_every_row_takes_a_place_and_a_copy_stays_with_its_posting():
    """The round-5 critique's s04 (ADR-0365): Capital One's same-titled requisitions, each on its
    Workday Board and its Radancy front, filled rows 1-20 while the cap counted one group. Every
    row now counts; a posting's copy on its other Board stays beside it."""
    rows = [
        _capital_one("R1", _WORKDAY),
        _capital_one("t1", _FRONT),
        _capital_one("R2", _WORKDAY),
        _capital_one("t2", _FRONT),
        _capital_one("R3", _WORKDAY),
        _capital_one("t3", _FRONT),
        _row("p1", "Preference Model"),
        _row("e1", "EvolutionIQ"),
    ]
    out = spread(rows, 3)
    assert [r["id"].rsplit(":", 1)[-1] for r in out] == [
        "R1",
        "t1",
        "R2",
        "t2",
        "p1",
        "e1",
        "R3",
        "t3",
    ]
    assert out[0][MORE_FROM_COMPANY] == 2
    assert all(r[PAST_COMPANY_CAP] for r in out[-2:])


def test_a_copy_ranked_after_the_cap_still_joins_its_kept_posting():
    rows = [
        _capital_one("R1", _WORKDAY),
        _capital_one("R2", _WORKDAY, "Data Engineer 4"),
        _capital_one("R3", _WORKDAY, "AI Engineer 4"),
        _capital_one("R4", _WORKDAY, "Full-stack Engineer 4"),
        _capital_one("t1", _FRONT),
    ]
    out = spread(rows, 3)
    assert [r["id"].rsplit(":", 1)[-1] for r in out] == ["R1", "R2", "R3", "t1", "R4"]
    assert out[-1][PAST_COMPANY_CAP] and out[0][MORE_FROM_COMPANY] == 1


def test_a_same_titled_row_on_the_same_board_takes_its_own_place():
    """Two requisitions on one Board are two postings, whatever their titles and places."""
    rows = [_capital_one(f"R{i}", _WORKDAY) for i in range(4)]
    out = spread(rows, 3)
    assert [r.get(PAST_COMPANY_CAP) for r in out] == [None, None, None, True]


def test_a_company_is_its_name_case_and_spacing_blind_else_its_board():
    assert company({"company": " Capital  ONE ", "id": "x:y:1"}) == "capital one"
    assert company({"company": "", "id": "workday:acme/ext:1"}) == "workday:acme/ext"
    rows = [
        _row("workday:a/x:1", None),
        _row("workday:a/x:2", None),
        _row("workday:b/x:1", None),
    ]
    out = spread(rows, 1)
    assert [r["id"] for r in out] == ["workday:a/x:1", "workday:b/x:1", "workday:a/x:2"]


def test_a_row_carrying_its_board_is_that_board_not_its_ids_guess():
    """A requirements sample's row carries the directory's Board (round-4 review S2): one key
    serves the cap, the sample's employer counts and a search page's held line."""
    row = {
        "company": "",
        "id": "oracle:egud.fa.us2.oraclecloud.com:9",
        "board": "Oracle:Kotak",
    }
    assert company(row) == "oracle:kotak"
