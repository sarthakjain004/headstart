from headstart.models import Job, epoch_ms_to_iso, is_remote


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
        "id", "ats", "company", "title", "location",
        "remote", "department", "url", "posted_at", "scraped_at",
    }


def test_is_remote():
    assert is_remote("Remote - US") is True
    assert is_remote("San Francisco, CA") is False
    assert is_remote(None) is None


def test_epoch_ms_to_iso():
    assert epoch_ms_to_iso(None) is None
    assert epoch_ms_to_iso(0) == "1970-01-01T00:00:00+00:00"
