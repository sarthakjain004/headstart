"""The Telegram bot: enrolment by the master's approval (ADR-0038).

`python -m headstart.alerts.bot`, run on a schedule by `.github/workflows/bot.yml`.

This replaces the keyword-alert bot that used to live at `headstart/bot.py`. That one
matched `Filter`s against the **Feed** and kept its own seen-Job state — the second ranking
rule ADR-0035 called a real duplication. Telegram Digests are now ranked by the same
`shortlist` as email, and this module's whole job is deciding *who gets one*.

**The first chat to `/start` becomes the master.** Everyone after that is announced to the
master, who answers `/allow` or `/deny`. This is what makes enrolment one tap for the person
joining while leaving the owner in control — the allowlist's role for email, done in a chat
rather than by hand-editing a file. Trust-on-first-use is the deliberate simplification: the
bot token is a secret only the owner holds, so the first `/start` is theirs unless the token
leaked before setup, and `master` can only be reassigned by editing the registry.

`handle` is pure — updates and state in, replies and mutations out — so the entire approval
flow is tested without a network, and `main` is only the wiring.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from .registry import Pending, Registry
from .store import Store, Subscription, chat_subscription_id, now_iso

HELP = (
    "HeadStart job alerts.\n\n"
    "/q <what you're looking for> — set your search, e.g.\n"
    "   /q backend engineer at a climate startup\n"
    "/status — show your current search\n"
    "/stop — stop alerts\n\n"
    "You'll get a message after each pipeline run with new roles that match."
)

MASTER_HELP = (
    "You're the master for this bot — requests to join come to you.\n\n"
    "/allow <id> — approve someone\n"
    "/deny <id> — refuse someone\n"
    "/pending — who's waiting\n"
    "/revoke <id> — stop someone already approved\n\n" + HELP
)


def _who(message: dict[str, Any]) -> tuple[str, str, str]:
    """The chat id, @username and display name behind one message."""
    chat_id = str((message.get("chat") or {}).get("id") or "")
    sender = message.get("from") or {}
    name = " ".join(
        str(sender.get(part) or "") for part in ("first_name", "last_name")
    ).strip()
    return chat_id, str(sender.get("username") or ""), name


def handle(
    update: dict[str, Any], registry: Registry, store: Store
) -> list[tuple[str, str]]:
    """Process one update. Returns [(chat_id, reply)] and mutates `registry` / `store`.

    Replies are returned rather than sent so ordering and content are assertable, and so a
    send failure cannot leave the registry claiming something never announced.
    """
    message = update.get("message") or update.get("edited_message")
    if not message:
        return []
    chat_id, username, name = _who(message)
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return []

    command, _, argument = text.partition(" ")
    command = command.lower().lstrip("/").split("@")[0]
    argument = argument.strip()

    if not registry.master:
        # Trust on first use: whoever holds the token opens the bot and claims it. Only
        # `/start` claims it — an idle "hi" or a stray `/status` to a fresh bot would
        # otherwise hand the seat to whoever happened to type first, which is neither what
        # ADR-0038 says nor what anyone would expect.
        if command != "start":
            return [(chat_id, "Send /start to set up this bot.")]
        registry.master = chat_id
        return [(chat_id, MASTER_HELP)]

    is_master = chat_id == registry.master
    sub = store.get(chat_subscription_id(chat_id))
    known = is_master or sub is not None

    if is_master and command in {"allow", "deny", "revoke", "pending"}:
        return _master_command(command, argument, registry, store, chat_id)

    if not known:
        return _request_access(command, chat_id, username, name, registry)

    if command == "q":
        if not argument:
            return [(chat_id, "Say what to look for, e.g. /q backend engineer")]
        return _set_query(argument, chat_id, sub, store)
    if command == "status":
        current = (sub.query if sub else "") or "nothing yet — set one with /q"
        return [(chat_id, f"Your search: {current}")]
    if command == "stop":
        if sub:
            store.remove(sub.id)
        return [(chat_id, "Stopped. Send /start to set it up again.")]
    return [(chat_id, MASTER_HELP if is_master else HELP)]


def _request_access(
    command: str, chat_id: str, username: str, name: str, registry: Registry
) -> list[tuple[str, str]]:
    """Announce a newcomer to the master, once."""
    if chat_id in registry.denied:
        # Silent to the master: a refusal that re-announced on every /start would hand a
        # stranger a way to keep prompting them.
        return [(chat_id, "Your request for job alerts wasn't approved.")]
    if chat_id in registry.pending:
        return [
            (chat_id, "Still waiting on approval — you'll get a message when it's in.")
        ]
    if command not in {"start", "q", "status"}:
        return [(chat_id, "Send /start to ask for access.")]

    waiting = Pending(chat_id=chat_id, username=username, name=name, asked_at=now_iso())
    registry.pending[chat_id] = waiting
    return [
        (chat_id, "Asked for access — you'll get a message once it's approved."),
        (
            registry.master,
            (
                f"{waiting.describe()} wants job alerts.\n"
                f"/allow {chat_id}   or   /deny {chat_id}"
            ),
        ),
    ]


def _master_command(
    command: str, argument: str, registry: Registry, store: Store, master: str
) -> list[tuple[str, str]]:
    if command == "pending":
        if not registry.pending:
            return [(master, "Nobody waiting.")]
        listing = "\n".join(p.describe() for p in registry.pending.values())
        return [(master, f"Waiting:\n{listing}")]

    if not argument:
        return [(master, f"Which id? e.g. /{command} 12345")]

    if command == "allow":
        waiting = registry.pending.pop(argument, None)
        # A denied chat is deliberately not in `pending` — /deny removed it and its /start is
        # answered without re-queueing, so requiring a pending entry here made ADR-0038's
        # "the master can change their mind" unreachable.
        if waiting is None and argument not in registry.denied:
            return [(master, f"{argument} isn't waiting on anything.")]
        # Idempotent: the registry is saved after the store, so a crash in between replays
        # this `/allow` next run. Minting a second record would reset that person's
        # Watermark to now — silently skipping everything since — and rotate the
        # unsubscribe token in messages already delivered.
        if store.get(chat_subscription_id(argument)) is None:
            store.put(Subscription.for_chat(argument))
        if argument in registry.denied:
            registry.denied.remove(
                argument
            )  # the master is allowed to change their mind
        return [
            (master, f"Approved {waiting.describe() if waiting else argument}."),
            (argument, "You're in.\n\n" + HELP),
        ]

    if command == "deny":
        waiting = registry.pending.pop(argument, None)
        if waiting is None:
            return [(master, f"{argument} isn't waiting on anything.")]
        if argument not in registry.denied:
            registry.denied.append(argument)
        # The person is told, rather than left waiting on a message that never comes.
        return [
            (master, f"Denied {waiting.describe()}."),
            (argument, "Your request for job alerts wasn't approved."),
        ]

    sub = store.get(chat_subscription_id(argument))
    if sub is None:
        return [(master, f"{argument} isn't receiving alerts.")]
    store.remove(sub.id)
    return [
        (master, f"Revoked {argument}."),
        (argument, "Your job alerts have been stopped."),
    ]


def _set_query(
    argument: str, chat_id: str, sub: Subscription | None, store: Store
) -> list[tuple[str, str]]:
    # `revised` for an existing record, so setting a new search keeps the Watermark and
    # does not re-send everything since they joined.
    updated = (
        sub.revised(argument, sub.search_filters)
        if sub
        else Subscription.for_chat(chat_id, argument)
    )
    store.put(updated)
    return [(chat_id, f"Searching for: {updated.query}")]


def main() -> int:
    required = ("TELEGRAM_BOT_TOKEN", "SUBSCRIBERS_REPO", "SUBSCRIBERS_TOKEN")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        print(
            f"bot not configured (missing {', '.join(missing)}) - skipping", flush=True
        )
        return 0

    from . import registry as registry_store
    from . import telegram as sender

    repo, token = os.environ["SUBSCRIBERS_REPO"], os.environ["SUBSCRIBERS_TOKEN"]
    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    store = Store(repo, token)
    registry = registry_store.load(repo, token)

    from headstart.telegram_bot_api import TelegramClient

    replies: list[tuple[str, str]] = []
    updates = TelegramClient(bot_token).get_updates(offset=registry.offset)
    for update in updates:
        # Advanced even for updates that produce no reply, or an unanswerable one would be
        # re-fetched every run forever.
        registry.offset = update["update_id"] + 1
        try:
            replies.extend(handle(update, registry, store))
        except Exception as exc:  # noqa: BLE001 — one bad update must not stall the queue
            print(f"[bot] update {update.get('update_id')} failed: {exc}", flush=True)

    # `alerts.telegram.send` rather than the polling client's `send_message`, which swallows
    # failures. A swallowed failure here is not cosmetic: the update that put somebody in
    # `pending` is consumed either way, so a lost announcement leaves them told to wait for
    # an answer the master was never asked for. Failing loudly makes the run red, and
    # `/pending` is the recovery.
    failed = 0
    for chat_id, text in replies:
        try:
            # Plain text: these replies are prose, and `/q <…>` would be read as markup.
            sender.send(bot_token, chat_id, [text], parse_mode=None)
        except sender.TelegramError as exc:
            failed += 1
            print(f"[bot] reply to {chat_id} FAILED: {exc}", flush=True)
            continue
        print(f"[bot] replied to {chat_id}", flush=True)

    # Saved after sending: a crash before this replays the update, and every write `handle`
    # makes is idempotent, so a replay costs a duplicate message rather than a lost one.
    registry_store.save(repo, token, registry)
    print(
        f"[bot] {len(updates)} update(s), {len(replies) - failed} repl(ies), "
        f"{failed} failed, {len(registry.pending)} waiting",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
