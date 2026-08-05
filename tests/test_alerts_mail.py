"""The Resend send: request shape, attachment encoding, and failure mapping (ADR-0035)."""

import base64
import json

import pytest

from headstart.alerts import mail
from headstart.alerts.digest import Digest

DIGEST = Digest(subject="2 new matches", text="plain", html="<p>rich</p>")


def test_payload_without_an_attachment_has_no_attachments_key():
    body = mail.payload("alerts@x.dev", "ada@example.com", DIGEST)
    assert body["from"] == "alerts@x.dev"
    assert body["to"] == ["ada@example.com"]
    assert body["subject"] == "2 new matches"
    assert "attachments" not in body


def test_payload_base64_encodes_the_spreadsheet():
    body = mail.payload("a@x.dev", "b@x.com", DIGEST, b"\x50\x4b\x03\x04binary")
    attachment = body["attachments"][0]
    assert attachment["filename"] == "new-jobs.xlsx"
    assert base64.b64decode(attachment["content"]) == b"\x50\x4b\x03\x04binary"


def test_send_posts_json_with_the_api_key_and_returns_the_id():
    captured = {}

    def post(url, body, headers):
        captured["url"], captured["headers"] = url, headers
        captured["body"] = json.loads(body)
        return {"id": "msg_123"}

    assert mail.send("key-1", "a@x.dev", "b@x.com", DIGEST, post=post) == "msg_123"
    assert captured["url"] == mail.ENDPOINT
    assert captured["headers"]["Authorization"] == "Bearer key-1"
    assert captured["body"]["html"] == "<p>rich</p>"


def test_failure_becomes_mail_error_so_the_watermark_is_not_advanced():
    def refuse(url, body, headers):
        raise OSError("connection reset")

    with pytest.raises(mail.MailError):
        mail.send("key", "a@x.dev", "b@x.com", DIGEST, post=refuse)
