"""The run's ordering guarantee: the Watermark advances only after a Digest is accepted,
and one Subscription's failure never stops the rest (ADR-0035)."""

import pytest

from headstart.alerts import digest as digest_mod
from headstart.alerts import mail, run, search
from headstart.alerts.store import Subscription

AFTER = "2026-08-02T12:00:00+00:00"


class _Store:
    def __init__(self):
        self.saved = []

    def put(self, sub):
        self.saved.append((sub.id, sub.notified_at))


def _sub(sub_id="abc"):
    return Subscription(
        id=sub_id, email="ada@example.com", query="backend", notified_at=AFTER
    )


def _rows(n=2):
    return [
        {
            "title": f"Engineer {i}",
            "company": "Acme",
            "score": 0.5,
            "url": f"https://j/{i}",
            "first_seen": "2026-08-02T13:00:00+00:00",
        }
        for i in range(n)
    ]


@pytest.fixture
def no_xlsx(monkeypatch):
    # The spreadsheet needs an extra CI does not install; the body is what is under test.
    monkeypatch.setattr(
        digest_mod, "to_xlsx", lambda jobs: (_ for _ in ()).throw(ImportError())
    )


def test_sends_then_advances_the_watermark(monkeypatch, no_xlsx):
    monkeypatch.setattr(search, "newly_seen", lambda *a, **k: _rows())
    sent = []
    monkeypatch.setattr(
        mail, "send", lambda key, sender, to, body, att=None: sent.append(to) or "id"
    )
    store, sub = _Store(), _sub()

    assert run.send_one(sub, store, "https://s", "key", "a@x.dev") == 2
    assert sent == ["ada@example.com"]
    assert store.saved and store.saved[0][1] > AFTER  # Watermark moved forward


def test_a_failed_send_leaves_the_watermark_untouched(monkeypatch, no_xlsx):
    monkeypatch.setattr(search, "newly_seen", lambda *a, **k: _rows())

    def refuse(*a, **k):
        raise mail.MailError("resend down")

    monkeypatch.setattr(mail, "send", refuse)
    store, sub = _Store(), _sub()

    with pytest.raises(mail.MailError):
        run.send_one(sub, store, "https://s", "key", "a@x.dev")
    assert store.saved == []  # nothing written, so next run retries the same window
    assert sub.notified_at == AFTER


def test_no_matches_sends_nothing_and_does_not_advance(monkeypatch):
    monkeypatch.setattr(search, "newly_seen", lambda *a, **k: [])

    def explode(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("an empty Digest must not be sent")

    monkeypatch.setattr(mail, "send", explode)
    store, sub = _Store(), _sub()

    assert run.send_one(sub, store, "https://s", "key", "a@x.dev") == 0
    assert store.saved == []


def test_unsubscribe_url_carries_id_and_token():
    sub = _sub()
    sub.unsubscribe_token = "t0k"
    url = run.unsubscribe_url("https://space/", sub)
    assert url == "https://space/unsubscribe?id=abc&token=t0k"


def test_main_skips_cleanly_when_unconfigured(monkeypatch, capsys):
    for name in run._REQUIRED:
        monkeypatch.delenv(name, raising=False)
    assert run.main() == 0
    assert "not configured" in capsys.readouterr().out
