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
        transports.digest, "to_telegram", lambda sub, jobs, total=None: ["one", "two"]
    )
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
        transports.Payload(b"xlsx", 2),
        "https://s",
        {"TELEGRAM_BOT_TOKEN": "t"},
    )

    assert sent == [("t", "4242", ["one", "two"], b"xlsx")]


def test_email_send_passes_the_unsubscribe_url_telegram_has_no_use_for(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        transports.digest,
        "render",
        lambda sub, jobs, url, total=None: seen.setdefault("url", url) or "body",
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
        transports.Payload(b"xlsx", 1),
        "https://space",
        {"RESEND_API_KEY": "k", "ALERTS_SENDER": "a@x.dev"},
    )

    assert seen["to"] == "ada@example.com"
    assert "id=abc" in seen["url"] and "token=tok" in seen["url"]


@pytest.mark.parametrize("base", ["https://space", "https://space/"])
def test_unsubscribe_url_carries_id_and_token_without_a_double_slash(base):
    url = transports.unsubscribe_url(base, _sub(unsubscribe_token="t0k"))
    assert url == "https://space/unsubscribe?id=abc&token=t0k"


def test_the_attachment_is_passed_through_not_rebuilt(monkeypatch):
    """`run` renders the spreadsheet once, from every fresh row; a Transport that rebuilt
    it from `jobs` would silently shrink it back to the capped shortlist."""
    seen = {}
    monkeypatch.setattr(
        transports.digest,
        "to_xlsx",
        lambda jobs: pytest.fail("transport rebuilt the xlsx"),
    )
    monkeypatch.setattr(
        transports.telegram,
        "send",
        lambda token, chat_id, chunks, attachment: seen.update(att=attachment),
    )
    monkeypatch.setattr(
        transports.digest, "to_telegram", lambda sub, jobs, total=None: ["x"]
    )

    transports.TELEGRAM.send(
        _sub(telegram="1"),
        [{"title": "Eng"}],
        transports.Payload(b"the-bigger-xlsx", 75),
        "https://s",
        {"TELEGRAM_BOT_TOKEN": "t"},
    )

    assert seen["att"] == b"the-bigger-xlsx"


def test_the_payload_total_reaches_the_renderer_so_counts_are_not_understated():
    """The body is capped and the spreadsheet is not, so a Digest that counted only what it
    showed would say "30 new matches" over a file holding 75."""
    seen = {}
    transports.digest.to_telegram(
        _sub(telegram="1", query="backend"),
        [{"title": "Eng", "url": "https://j/1", "score": 0.5}],
        total=75,
    )
    chunks = transports.digest.to_telegram(
        _sub(telegram="1", query="backend"),
        [{"title": "Eng", "url": "https://j/1", "score": 0.5}],
        total=75,
    )
    assert "75 new job(s)" in chunks[0]
    assert any("74 more" in c for c in chunks), "the rest must be pointed at the file"
    assert seen == {}


def test_config_from_covers_every_transport_without_run_naming_one(monkeypatch):
    """ADR-0038: "adding Slack or a webhook is one literal and one tuple entry; `run` never
    learns a channel exists."

    `run.main` built its config from a hard-coded name tuple, so a new Transport's `needs` were
    absent from it, `Transport.missing` then reported the channel unconfigured, and every one of
    its Subscriptions was skipped as `TransportUnset` — the seam failing closed *and* silently.
    """
    extra = transports.Transport(
        name="slack",
        selects=lambda sub: False,
        send=lambda *a, **k: None,
        needs=("SLACK_WEBHOOK_URL",),
    )
    monkeypatch.setattr(transports, "TRANSPORTS", (*transports.TRANSPORTS, extra))

    config = transports.config_from({"SLACK_WEBHOOK_URL": "https://hooks.example"})

    assert extra.missing(config) == []


def test_config_from_reads_but_does_not_demand():
    """A repo with only Telegram configured runs Telegram and skips email, rather than
    refusing to start (ADR-0038)."""
    config = transports.config_from({"TELEGRAM_BOT_TOKEN": "t"})

    assert config["TELEGRAM_BOT_TOKEN"] == "t"
    assert config["RESEND_API_KEY"] == ""
    assert transports.EMAIL.missing(config) == ["RESEND_API_KEY", "ALERTS_SENDER"]
