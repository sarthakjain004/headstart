"""Deliver one Digest as Telegram DMs (ADR-0038).

`send(...)` is the interface and the `post` seam is what makes it testable without a network
or a bot token — the same shape as `mail.send`, for the same reason: one POST to a documented
JSON endpoint needs only stdlib urllib, which keeps this importable in CI's quality job.

**Deliberately not `headstart.telegram_bot_api.TelegramClient`.** That client swallows a failed send
so one blocked chat cannot abort the polling loop, which is right for a bot answering
commands and exactly wrong here: the alerts run advances a Watermark only once delivery is
accepted, so a swallowed failure would silently skip that person's window forever. Same API,
opposite failure contract — so a separate sender rather than a flag on the shared one.

No retries, matching `mail`: a failure leaves the Watermark where it was and the same window
is simply tried again next run.
"""

from __future__ import annotations

import json
import secrets
import urllib.request
from collections.abc import Callable
from typing import Any

API = "https://api.telegram.org"
_TIMEOUT = 30
FILENAME = "new-jobs.xlsx"


class TelegramError(Exception):
    """Telegram refused or could not be reached; the Watermark must not advance."""


def _post(url: str, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.load(response)


def multipart(
    fields: dict[str, str], filename: str, content: bytes
) -> tuple[bytes, str]:
    """A `multipart/form-data` body and its content type — pure, so it is testable.

    Hand-rolled because `sendDocument` is the one Telegram call that cannot be JSON, and
    pulling in `requests` for a single upload would undo the stdlib-only reasoning above.
    """
    boundary = f"----headstart{secrets.token_hex(16)}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode()
    )
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def send(
    token: str,
    chat_id: str,
    chunks: list[str],
    attachment: bytes | None = None,
    post: Callable[[str, bytes, dict[str, str]], dict[str, Any]] | None = None,
    parse_mode: str | None = "HTML",
) -> None:
    """Send `chunks` as consecutive messages, then the spreadsheet if there is one.

    `parse_mode` is a parameter rather than a constant because the two callers need
    opposite things. A Digest is markup — escaped links built by `digest.to_telegram`. The
    bot's replies are prose containing `/q <what you're looking for>` and `/allow <id>`,
    which under HTML parsing are unsupported start tags: Telegram answers 200 with
    `"ok": false` and the message never arrives. Sending those as plain text is both
    simpler and safer than escaping help text that has no markup in it to begin with.

    Raises :class:`TelegramError` on the first refusal. Sending stops there rather than
    pressing on: a partial Digest with the Watermark held back is re-sent whole next run,
    where continuing past a failure would leave no record of what did and did not arrive.
    """
    call = post or _post

    def _call(method: str, body: bytes, content_type: str) -> None:
        try:
            reply = call(
                f"{API}/bot{token}/{method}", body, {"Content-Type": content_type}
            )
        except Exception as exc:  # refusal and unreachable are one outcome
            raise TelegramError(f"{method}: {type(exc).__name__}: {exc}") from exc
        # Telegram answers HTTP 200 with `"ok": false` for application-level refusals — a
        # blocked bot, an unknown chat id — so a status code alone reads those as sent.
        if not (isinstance(reply, dict) and reply.get("ok")):
            raise TelegramError(f"{method} refused: {reply}")

    for chunk in chunks:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        _call("sendMessage", json.dumps(payload).encode("utf-8"), "application/json")

    if attachment:
        body, content_type = multipart(
            {
                "chat_id": chat_id,
                "caption": "Every new match from this run, including the ones not listed above.",
            },
            FILENAME,
            attachment,
        )
        _call("sendDocument", body, content_type)
