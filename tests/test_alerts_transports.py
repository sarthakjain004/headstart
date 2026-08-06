"""Which channel a Subscription is delivered by, and what each needs (ADR-0038)."""

import pytest

from headstart.alerts import transports
from headstart.alerts.store import Subscription


def _sub(**over):
    base = {"id": "abc", "email": "ada@example.com", "query": "backend"}
    base.update(over)
    return Subscription(**base)


def test_a_chat_id_selects_telegram_and_its_absence_selects_email():
    assert transports.for_subscription(_sub(telegram="4242")).name == "telegram"
    assert transports.for_subscription(_sub()).name == "email"


def test_every_subscription_selects_exactly_one_transport():
    # `for_subscription` takes the first match, so a Subscription that matched none would
    # raise StopIteration inside the per-person guard and read as a delivery failure.
    for sub in (_sub(), _sub(telegram="1"), _sub(email="", telegram="")):
        assert transports.for_subscription(sub) in transports.TRANSPORTS


def test_missing_names_the_unset_secrets_without_reading_the_environment():
    assert transports.TELEGRAM.missing({}) == ["TELEGRAM_BOT_TOKEN"]
    assert transports.TELEGRAM.missing({"TELEGRAM_BOT_TOKEN": "t"}) == []
    assert transports.EMAIL.missing({"RESEND_API_KEY": "k"}) == ["ALERTS_SENDER"]
    assert transports.EMAIL.missing({"RESEND_API_KEY": "k", "ALERTS_SENDER": "a"}) == []


def test_an_empty_secret_counts_as_unset():
    # Actions passes an unset secret through as "", not as an absent key.
    assert transports.TELEGRAM.missing({"TELEGRAM_BOT_TOKEN": ""}) == [
        "TELEGRAM_BOT_TOKEN"
    ]


def test_telegram_send_carries_the_chat_id_chunks_and_spreadsheet(monkeypatch):
    sent = []
    monkeypatch.setattr(
        transports.digest, "to_telegram", lambda sub, jobs: ["one", "two"]
    )
    monkeypatch.setattr(transports.digest, "to_xlsx", lambda jobs: b"xlsx")
    monkeypatch.setattr(
        transports.telegram,
        "send",
        lambda token, chat_id, chunks, attachment: sent.append(
            (token, chat_id, chunks, attachment)
        ),
    )

    transports.TELEGRAM.send(
        _sub(telegram="4242"),
        [{"title": "Eng"}],
        "https://s",
        {"TELEGRAM_BOT_TOKEN": "t"},
    )

    assert sent == [("t", "4242", ["one", "two"], b"xlsx")]


def test_email_send_passes_the_unsubscribe_url_telegram_has_no_use_for(monkeypatch):
    seen = {}
    monkeypatch.setattr(transports.digest, "to_xlsx", lambda jobs: b"xlsx")
    monkeypatch.setattr(
        transports.digest,
        "render",
        lambda sub, jobs, url: seen.setdefault("url", url) or "body",
    )
    monkeypatch.setattr(
        transports.mail,
        "send",
        lambda key, sender, to, body, att: seen.update(to=to, sender=sender),
    )

    sub = _sub(unsubscribe_token="tok")
    transports.EMAIL.send(
        sub,
        [{"title": "Eng"}],
        "https://space",
        {"RESEND_API_KEY": "k", "ALERTS_SENDER": "a@x.dev"},
    )

    assert seen["to"] == "ada@example.com"
    assert "id=abc" in seen["url"] and "token=tok" in seen["url"]


@pytest.mark.parametrize("base", ["https://space", "https://space/"])
def test_unsubscribe_url_carries_id_and_token_without_a_double_slash(base):
    url = transports.unsubscribe_url(base, _sub(unsubscribe_token="t0k"))
    assert url == "https://space/unsubscribe?id=abc&token=t0k"
