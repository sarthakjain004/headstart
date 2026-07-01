from headstart.filters import Filter, matches

JOB = {
    "id": "greenhouse:stripe:1",
    "ats": "greenhouse",
    "company": "Stripe",
    "title": "Backend Engineer",
    "department": "Eng",
    "location": "Remote - India",
    "remote": True,
    "url": "u",
}


def test_empty_filter_matches_all():
    assert matches(JOB, Filter()) is True


def test_remote_only():
    assert matches(JOB, Filter(remote=True)) is True
    assert matches({**JOB, "remote": False}, Filter(remote=True)) is False


def test_keyword_company_location_ats():
    assert matches(JOB, Filter(q="backend")) is True
    assert matches(JOB, Filter(q="frontend")) is False
    assert matches(JOB, Filter(company="strip")) is True
    assert matches(JOB, Filter(location="india")) is True
    assert matches(JOB, Filter(location="berlin")) is False
    assert matches(JOB, Filter(ats="greenhouse")) is True
    assert matches(JOB, Filter(ats="lever")) is False


def test_roundtrip_and_describe():
    f = Filter(q="sre", remote=True)
    assert Filter.from_dict(f.to_dict()) == f
    assert Filter().to_dict() == {}
    assert "remote only" in Filter(remote=True).describe()
    assert Filter().describe().startswith("no filters")
