"""Where a Digest goes, and how a new destination is added (ADR-0038).

`for_subscription` picks the Transport; `Transport.send` delivers. Everything a channel
needs to be pluggable lives in one record, so adding Slack, Discord or a webhook is a
`Transport(...)` literal and one entry in `TRANSPORTS` — no edit to `run`, no new branch in
a dispatcher, nothing else to keep in step.

**This deliberately revisits ADR-0035.** That ADR rejected formal adapters because "`identity`
and `mail` have exactly one real implementation each, which is a hypothetical seam, not a
real one" — sound at the time, and the reason the alerts package injects plain callables
everywhere else. Telegram makes the seam real, so the premise is gone rather than the rule
being wrong. What is kept is the *idiom*: a Transport is a record of functions, not a class
hierarchy, so it still reads like `resume_query.query_for(text, ask=…)` and still fakes with
`monkeypatch.setattr` rather than a mocking library.

The order of `TRANSPORTS` is the selection order — most specific first, with email last as
the one whose `selects` always matches, so a Subscription can never fall off the end.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from . import digest, mail, telegram
from .store import Subscription


def unsubscribe_url(base: str, sub: Subscription) -> str:
    """The one-click unsubscribe link a Digest carries. Email's concern: a Telegram DM is
    stopped by blocking the bot or by striking the entry from the allowlist."""
    return f"{base.rstrip('/')}/unsubscribe?id={sub.id}&token={sub.unsubscribe_token}"


@dataclass(frozen=True)
class Transport:
    """One delivery channel: how to recognise its Subscriptions, what it needs, how to send.

    `needs` names environment variables rather than reading them, so a channel stays inert
    until configured without `run` knowing which secrets belong to which channel.
    """

    name: str
    selects: Callable[[Subscription], bool]
    send: Callable[
        [Subscription, list[dict[str, Any]], bytes, str, Mapping[str, str]], None
    ]
    needs: tuple[str, ...] = field(default_factory=tuple)

    def missing(self, config: Mapping[str, str]) -> list[str]:
        """The names in `needs` this config leaves empty."""
        return [name for name in self.needs if not config.get(name)]


def _send_telegram(
    sub: Subscription,
    jobs: list[dict[str, Any]],
    attachment: bytes,
    space: str,
    config: Mapping[str, str],
) -> None:
    telegram.send(
        config["TELEGRAM_BOT_TOKEN"],
        sub.telegram,
        digest.to_telegram(sub, jobs),
        attachment,
    )


def _send_email(
    sub: Subscription,
    jobs: list[dict[str, Any]],
    attachment: bytes,
    space: str,
    config: Mapping[str, str],
) -> None:
    mail.send(
        config["RESEND_API_KEY"],
        config["ALERTS_SENDER"],
        sub.email,
        digest.render(sub, jobs, unsubscribe_url(space, sub)),
        attachment,
    )


TELEGRAM = Transport(
    name="telegram",
    selects=lambda sub: bool(sub.telegram),
    send=_send_telegram,
    needs=("TELEGRAM_BOT_TOKEN",),
)

EMAIL = Transport(
    name="email",
    # Last and unconditional: an address is the one thing every Subscription has.
    selects=lambda sub: True,
    send=_send_email,
    needs=("RESEND_API_KEY", "ALERTS_SENDER"),
)

TRANSPORTS: tuple[Transport, ...] = (TELEGRAM, EMAIL)


def for_subscription(sub: Subscription) -> Transport:
    """The Transport this Subscription is delivered by — the first whose `selects` matches.

    Exactly one per Subscription, which is what lets a single Watermark be correct: two
    channels sharing it would let a healthy one advance the Watermark past a window the
    other never delivered.
    """
    return next(t for t in TRANSPORTS if t.selects(sub))
