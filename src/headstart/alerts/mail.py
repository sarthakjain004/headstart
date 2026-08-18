"""Send one Digest through Resend (ADR-0035).

`send(...)` is the interface, and the `post` seam is what makes it testable without a
network or an API key. One POST to a documented JSON endpoint needs only stdlib urllib —
the same reasoning as `llm_router`: no SDK keeps this importable in CI's quality job,
which installs no extras.

No retries. The alerts run advances a Subscription's Watermark only after this returns, so
a failure means the same window is simply tried again next run — a retry here would risk
a duplicate for no gain.
"""

from __future__ import annotations

import base64
import json
import urllib.request
from collections.abc import Callable
from typing import Any

from .digest import Digest

ENDPOINT = "https://api.resend.com/emails"
_TIMEOUT = 30


class MailError(Exception):
    """Resend refused or could not be reached; the Watermark must not advance."""


def _post(url: str, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.load(response)


FILENAME = "new-jobs.xlsx"


def payload(
    sender: str,
    to: str,
    digest: Digest,
    attachment: bytes | None = None,
) -> dict[str, Any]:
    """The Resend request body — pure, so the attachment encoding is testable."""
    body: dict[str, Any] = {
        "from": sender,
        "to": [to],
        "subject": digest.subject,
        "text": digest.text,
        "html": digest.html,
    }
    if attachment:
        body["attachments"] = [
            {
                "filename": FILENAME,
                "content": base64.b64encode(attachment).decode("ascii"),
            }
        ]
    return body


def send(
    api_key: str,
    sender: str,
    to: str,
    digest: Digest,
    attachment: bytes | None = None,
    post: Callable[[str, bytes, dict[str, str]], dict[str, Any]] | None = None,
) -> str:
    """Hand one Digest to Resend; returns its message id. Raises :class:`MailError`."""
    body = json.dumps(payload(sender, to, digest, attachment)).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        reply = (post or _post)(ENDPOINT, body, headers)
    except Exception as exc:
        raise MailError(f"{type(exc).__name__}: {exc}") from exc
    return str(reply.get("id") or "")
