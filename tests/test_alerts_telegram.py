"""The Telegram sender's failure contract — a refusal must reach the caller (ADR-0038).

The `post` seam stands in for the API, the way `tests/test_alerts_mail.py` does for Resend.
"""

import json

import pytest

from headstart.alerts import telegram


def test_sends_each_chunk_as_its_own_message():
    calls = []

    def post(url, body, headers):
        calls.append((url, json.loads(body) if b"{" == body[:1] else body, headers))
        return {"ok": True}

    telegram.send("tok", "4242", ["first", "second"], post=post)

    assert len(calls) == 2
    assert all(url.endswith("/bottok/sendMessage") for url, _, _ in calls)
    assert [payload["text"] for _, payload, _ in calls] == ["first", "second"]
    assert {payload["chat_id"] for _, payload, _ in calls} == {"4242"}
    assert all(payload["parse_mode"] == "HTML" for _, payload, _ in calls)


def test_an_attachment_goes_as_a_multipart_document_after_the_messages():
    calls = []

    def post(url, body, headers):
        calls.append((url, body, headers))
        return {"ok": True}

    telegram.send("tok", "4242", ["only"], attachment=b"xlsx-bytes", post=post)

    assert [u.rsplit("/", 1)[-1] for u, _, _ in calls] == [
        "sendMessage",
        "sendDocument",
    ]
    url, body, headers = calls[-1]
    assert headers["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b"xlsx-bytes" in body
    assert b'name="chat_id"' in body and b"4242" in body


def test_no_attachment_means_no_document_call():
    calls = []
    telegram.send(
        "tok",
        "4242",
        ["only"],
        attachment=None,
        post=lambda u, b, h: calls.append(u) or {"ok": True},
    )
    assert [u.rsplit("/", 1)[-1] for u in calls] == ["sendMessage"]


def test_an_ok_false_reply_raises_rather_than_reading_as_delivered():
    # Telegram answers HTTP 200 with ok:false for a blocked bot or unknown chat, so a
    # transport that only checked the status code would advance the Watermark on a
    # message that never arrived.
    with pytest.raises(telegram.TelegramError, match="refused"):
        telegram.send(
            "tok",
            "4242",
            ["hi"],
            post=lambda u, b, h: {"ok": False, "description": "bot was blocked"},
        )


def test_a_transport_error_becomes_a_telegram_error():
    def explode(url, body, headers):
        raise OSError("connection reset")

    with pytest.raises(telegram.TelegramError, match="OSError"):
        telegram.send("tok", "4242", ["hi"], post=explode)


def test_sending_stops_at_the_first_refusal():
    calls = []

    def post(url, body, headers):
        calls.append(url)
        return {"ok": False}

    with pytest.raises(telegram.TelegramError):
        telegram.send("tok", "4242", ["one", "two", "three"], post=post)
    assert len(calls) == 1, "a partial Digest is re-sent whole next run, not continued"


def test_multipart_body_is_well_formed():
    body, content_type = telegram.multipart(
        {"chat_id": "42"}, "jobs.xlsx", b"\x00binary"
    )
    boundary = content_type.split("boundary=")[1]

    assert body.startswith(f"--{boundary}\r\n".encode())
    assert body.endswith(f"\r\n--{boundary}--\r\n".encode())
    assert b'filename="jobs.xlsx"' in body
    assert b"\x00binary" in body
