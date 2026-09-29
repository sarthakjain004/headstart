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


def test_a_copy_of_a_kept_posting_takes_no_place():
    """p1_01: Capital One's Radancy and Workday twins list one posting twice; the copy is
    listed under its row on the page, so it does not use one of the company's places."""
    rows = [
        _row("c1", "Capital One", "Senior Lead Software Engineer", "Plano, TX"),
        _row("c1-twin", "Capital One", "Senior Lead Software Engineer", "Plano, TX"),
        _row("c2", "Capital One"),
        _row("c3", "Capital One"),
        _row("c4", "Capital One"),
    ]
    out = spread(rows, 3)
    assert [r["id"] for r in out] == ["c1", "c1-twin", "c2", "c3", "c4"]
    assert out[-1][PAST_COMPANY_CAP] and out[0][MORE_FROM_COMPANY] == 1


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
