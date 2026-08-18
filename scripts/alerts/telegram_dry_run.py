"""Drive the whole Telegram alert flow against a fake Telegram and a fake store.

    python scripts/eval/telegram_dry_run.py

Unit tests assert each piece; this walks the path a real person walks — master claims the
bot, a stranger asks, the master approves, they set a search, a pipeline run delivers — and
prints every message as it would arrive. It is the check that the *seams line up*: the bot
writes a record shape `alerts.run` actually selects, and the Digest a chat receives is the
one `shortlist` produced.

Nothing here touches the network, Hugging Face, Telegram or Resend, so it is safe to run
anywhere and needs no secrets. What it cannot prove is that Telegram accepts the payloads —
only a live `/start` against a real token does that (see docs/telegram-alerts.md).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from headstart.alerts import bot, digest, run, telegram, transports
from headstart.alerts.registry import Registry
from headstart.alerts.store import Subscription, chat_subscription_id

MASTER, ADA = "1000", "2000"


class FakeStore:
    """The Subscriptions dataset in memory."""

    def __init__(self) -> None:
        self.records: dict[str, Subscription] = {}

    def get(self, sub_id):
        return self.records.get(sub_id)

    def put(self, sub):
        self.records[sub.id] = sub

    def remove(self, sub_id):
        self.records.pop(sub_id, None)

    def all(self):
        return list(self.records.values())

    def invites(self):
        return []  # this walk is the Telegram path; the allowlist path has its own tests


def update(chat_id, text, username="", first="", uid=1):
    return {
        "update_id": uid,
        "message": {
            "chat": {"id": int(chat_id)},
            "from": {"id": int(chat_id), "username": username, "first_name": first},
            "text": text,
        },
    }


def step(title):
    print(f"\n\033[1m── {title}\033[0m", flush=True)


def pump(updates, registry, store):
    for one in updates:
        for chat_id, text in bot.handle(one, registry, store):
            who = "master" if chat_id == MASTER else f"chat {chat_id}"
            first = text.splitlines()[0]
            rest = (
                ""
                if len(text.splitlines()) == 1
                else f"  (+{len(text.splitlines()) - 1} more lines)"
            )
            print(f"  → {who}: {first}{rest}", flush=True)


def main() -> int:
    registry, store = Registry(), FakeStore()

    step("1. The owner claims the bot")
    pump([update(MASTER, "/start", first="Owner")], registry, store)
    print(f"  master is now {registry.master!r}", flush=True)

    step("2. A stranger asks for access")
    pump([update(ADA, "/start", username="ada_l", first="Ada", uid=2)], registry, store)

    step("3. The stranger asks again before an answer (must not re-announce)")
    pump([update(ADA, "/start", username="ada_l", first="Ada", uid=3)], registry, store)

    step("4. The master approves")
    pump([update(MASTER, f"/allow {ADA}", uid=4)], registry, store)

    step("5. The newcomer sets a search")
    pump(
        [update(ADA, "/q backend engineer at a climate startup", uid=5)],
        registry,
        store,
    )

    sub = store.get(chat_subscription_id(ADA))
    print(
        f"\n  stored record: id={sub.id} telegram={sub.telegram} email={sub.email!r}",
        flush=True,
    )
    print(f"  query={sub.query!r} watermark={sub.watermark}", flush=True)

    step("6. The alerts run picks them up")
    picked = run.telegram_subscriptions(store)
    print(
        f"  telegram_subscriptions selected {len(picked)} record(s): {[s.id for s in picked]}",
        flush=True,
    )
    assert [s.id for s in picked] == [sub.id], (
        "the bot's record must be what the run delivers to"
    )

    step("7. A pipeline run delivers a Digest")
    jobs = [
        {
            "title": f"Backend Engineer {i}",
            "company": "Acme <Climate>",  # deliberately contains markup
            "location": "Remote",
            "score": 0.9 - i / 100,
            "url": f"https://jobs.example.com/{i}?ref=a&b=2",
        }
        for i in range(23)
    ]
    chunks = digest.to_telegram(sub, jobs)
    print(
        f"  {len(jobs)} roles -> {len(chunks)} message(s); longest {max(map(len, chunks))} chars (cap 4096)",
        flush=True,
    )
    assert all(len(c) < 4096 for c in chunks), "a chunk exceeded Telegram's message cap"
    assert "<Climate>" not in chunks[0], (
        "markup must be escaped or Telegram rejects the message"
    )
    print("\n  --- first message as it would arrive ---", flush=True)
    for line in chunks[0].splitlines()[:4]:
        print(f"  {line}", flush=True)
    print("  ...", flush=True)

    step("8. The transport it would go out on")
    calls = []
    telegram.send(
        "fake-token",
        sub.telegram,
        chunks,
        attachment=b"fake-xlsx",
        post=lambda url, body, headers: (
            calls.append((url.rsplit("/", 1)[-1], len(body))) or {"ok": True}
        ),
    )
    print(f"  transport = {transports.for_subscription(sub).name}", flush=True)
    for method, size in calls:
        print(f"  {method}: {size} bytes", flush=True)
    assert [m for m, _ in calls] == ["sendMessage"] * len(chunks) + ["sendDocument"]

    step("9. A refusal must not advance the Watermark")
    try:
        telegram.send(
            "t",
            sub.telegram,
            ["hi"],
            post=lambda *a: {"ok": False, "description": "bot was blocked"},
        )
    except telegram.TelegramError as exc:
        print(f"  raised as required: {exc}", flush=True)
    else:  # pragma: no cover - the assert below is the real check
        raise AssertionError("a refused send must raise, or a Digest is silently lost")

    step("10. Every bot reply survives Telegram's parser")
    # The gap this step closes: an earlier version sent bot replies through the Digest
    # sender, which sets parse_mode=HTML. `/q <what you're looking for>` and `/allow <id>`
    # are unsupported start tags, so Telegram answered ok:false and the very first /start
    # failed. Faking the transport hid it — so check the payload, not just the flow.
    import re

    bodies = []
    for text in (bot.HELP, bot.MASTER_HELP, "Approved Ada (@ada_l) — id 2000."):
        telegram.send(
            "t",
            "1",
            [text],
            parse_mode=None,
            post=lambda url, body, headers: (
                bodies.append(json.loads(body)) or {"ok": True}
            ),
        )
    assert all("parse_mode" not in b for b in bodies), (
        "bot replies must go as plain text; they contain <…> that HTML mode rejects"
    )
    tags = [tag for b in bodies for tag in re.findall(r"<[^>]*>", b["text"])]
    print(
        f"  {len(bodies)} bot replies sent as plain text; they contain {len(tags)} <…> runs",
        flush=True,
    )
    print(
        f"  e.g. {tags[:2]} — these would be rejected under parse_mode=HTML", flush=True
    )

    digest_bodies = []
    telegram.send(
        "t",
        "1",
        chunks[:1],
        post=lambda url, body, headers: (
            digest_bodies.append(json.loads(body)) or {"ok": True}
        ),
    )
    assert digest_bodies[0]["parse_mode"] == "HTML", "a Digest still needs its links"
    print("  digest still sent as HTML (its links are escaped markup)", flush=True)

    step("11. The master revokes")
    pump([update(MASTER, f"/revoke {ADA}", uid=6)], registry, store)
    print(f"  records remaining: {len(store.records)}", flush=True)
    assert store.records == {}

    print(
        f"\n\033[32mAll steps passed.\033[0m Registry: {json.dumps(registry.to_dict())}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
