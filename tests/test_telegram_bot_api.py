"""The access bot's Telegram client — that a failed send says *why* without saying *where*.

`send_message` swallows its failure on purpose (one blocked chat must not abort the polling
loop), so the log line it leaves is the only trace that send ever happened. This API puts the
bot token in the URL path, which is why `alerts/telegram._reason` exists and why this client
formats through it rather than restating the reasoning — a second copy is how one of the two
silently stops applying it, which is exactly what had happened here.
"""

from __future__ import annotations

import io
import logging
import urllib.error
import urllib.request

from headstart.telegram_bot_api import TelegramClient

_TOKEN = "123456:AAHnotarealtokennotarealtokennotare"
_CHAT = "987654321"


def _blocked(*_args, **_kwargs):
    raise urllib.error.HTTPError(
        f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
        403,
        "Forbidden",
        {},
        io.BytesIO(b'{"ok":false,"description":"bot was blocked by the user"}'),
    )


def test_a_failed_send_names_its_cause_and_never_the_token(monkeypatch, caplog):
    """The token rides in the URL, and `HTTPError` carries that URL on `.filename`/`.url`.

    `str(exc)` happens not to print it, which is why the old bare `{exc}` never leaked — a
    property of one exception class, not a guarantee about the ones this can raise. The line
    also gains what it never had: Telegram's own `description` lives in the response *body*,
    which is read once and then gone with the exception.
    """
    monkeypatch.setattr(urllib.request, "urlopen", _blocked)
    with caplog.at_level(logging.WARNING, logger="headstart.telegram_bot_api"):
        TelegramClient(_TOKEN).send_message(_CHAT, "hello")

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "HTTP 403" in message
    assert "bot was blocked by the user" in message  # the cause, from the body
    assert _TOKEN not in message  # the credential
    assert "api.telegram.org" not in message  # and the URL that carries it
    assert _CHAT not in message  # the chat id stays hashed, as it already was
