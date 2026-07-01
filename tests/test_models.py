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
