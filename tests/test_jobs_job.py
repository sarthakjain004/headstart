from headstart.jobs.job import (
    Job,
    epoch_ms_to_iso,
    html_to_text,
    is_remote,
    repaired_mojibake,
    requisition_of,
)


def test_job_round_trips_to_dict():
    job = Job(
        id="greenhouse:stripe:1",
        ats="greenhouse",
        company="Stripe",
        title="Engineer",
        location="Remote",
        remote=True,
        department=None,
        url="https://example.com",
        posted_at=None,
        scraped_at="2026-01-01T00:00:00+00:00",
    )
    d = job.to_dict()
    assert d["id"] == "greenhouse:stripe:1"
    assert set(d) == {
        "id",
        "ats",
        "company",
        "title",
        "location",
        "remote",
        "department",
        "url",
        "posted_at",
        "scraped_at",
        "description",
        "experience",
        "employment_type",
        "salary",
        "requisition",
    }


def test_job_optional_fields_default_to_none():
    # a scraper that supplies none of the richer fields still builds a valid Job
    job = Job(
        id="x:y:1",
        ats="x",
        company="C",
        title="T",
        location=None,
        remote=None,
        department=None,
        url="u",
        posted_at=None,
        scraped_at="2026-01-01T00:00:00+00:00",
    )
    assert job.description is None
    assert job.experience is None
    assert job.employment_type is None
    assert job.salary is None


def test_html_to_text():
    assert html_to_text(None) is None
    assert html_to_text("") is None
    assert html_to_text("<p>Hello&nbsp;<b>world</b></p>") == "Hello world"
    # some sources entity-encode their HTML; it should still come out clean
    assert html_to_text("&lt;p&gt;Hi &amp;amp; bye&lt;/p&gt;") == "Hi & bye"


def test_is_remote():
    assert is_remote("Remote - US") is True
    assert is_remote("San Francisco, CA") is False
    assert is_remote(None) is None


def test_epoch_ms_to_iso():
    assert epoch_ms_to_iso(None) is None
    assert epoch_ms_to_iso(0) == "1970-01-01T00:00:00+00:00"


def _job(**overrides):
    fields = {
        "id": "x:y:1",
        "ats": "x",
        "company": "C",
        "title": "T",
        "location": None,
        "remote": None,
        "department": None,
        "url": "u",
        "posted_at": None,
        "scraped_at": "2026-01-01T00:00:00+00:00",
    }
    return Job(**{**fields, **overrides})


def test_job_unescapes_entities_in_title_and_company():
    # Served 2026-09-24: 56 titles (smartrecruiters, zwayam) and a pyjamahr company kept one.
    job = _job(
        title="IT Architect - Technical Process &amp; Compliance",
        company="Pitangent Analytics &amp; Software",
    )
    assert job.title == "IT Architect - Technical Process & Compliance"
    assert job.company == "Pitangent Analytics & Software"


def test_job_strips_company_whitespace():
    # 3,851 served rows carried a company with a trailing space ("Onware ").
    assert _job(company="  Onware \n").company == "Onware"
    # Only the ends: the inside of a stated name is left as the Board wrote it.
    assert _job(title=" Senior  Engineer ").title == "Senior  Engineer"


def test_job_location_drops_tags_and_lists_lines():
    # Teamtailor's own feed puts markup in `addressLocality` (knightecgroup, 2026-09-24).
    tagged = 'São Bernardo do Campo</span> - <span class="region">SP, BR'
    assert _job(location=tagged).location == "São Bernardo do Campo - SP, BR"
    # iCIMS states several places one per line; the repo joins places with "; ".
    listed = "FL-Sarasota\nUS-IL-Itasca\n US-TX-Austin, US"
    assert (
        _job(location=listed).location == "FL-Sarasota; US-IL-Itasca; US-TX-Austin, US"
    )
    assert _job(location="  Pune,   India ").location == "Pune, India"
    assert _job(location=" <br> ").location is None
    assert _job(location="&lt;Remote&gt; &#x7c; UK").location == "<Remote> | UK"


def test_a_requisition_is_stored_as_trimmed_text_or_none():
    """Two rows match only on equal strings (ADR-0210), whether the ATS stated a number or text."""
    assert requisition_of(3560628) == "3560628"
    assert requisition_of(" R-100 ") == "R-100"
    assert requisition_of("") is None
    assert requisition_of(None) is None


def test_job_repairs_utf8_read_as_latin1_in_its_display_text():
    """zoho serves locations double-encoded at source ("San JosÃ©", 2026-09-24)."""
    job = Job(
        id="zoho:x:1",
        ats="zoho",
        company="CafÃ© Coffee Day",
        title="IngÃ©nieur",
        location="San JosÃ©, Costa Rica",
        remote=None,
        department=None,
        url="https://x",
        posted_at=None,
        scraped_at="2026-09-24T00:00:00+00:00",
    )
    assert (job.company, job.title, job.location) == (
        "Café Coffee Day",
        "Ingénieur",
        "San José, Costa Rica",
    )


def test_a_real_latin1_capital_a_tilde_is_left_alone():
    """ "SÃO PAULO" is Portuguese, not mojibake: "Ã" before "O" is no UTF-8 sequence."""
    assert repaired_mojibake("SÃO PAULO") == "SÃO PAULO"
    assert repaired_mojibake("São Paulo") == "São Paulo"
    # a string that cannot be Latin-1 at all was not produced by this defect
    assert repaired_mojibake("cafÃ© â€™") == "cafÃ© â€™"
    assert repaired_mojibake(None) is None
