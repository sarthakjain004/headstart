from headstart.models import Job, epoch_ms_to_iso, html_to_text, is_remote


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
