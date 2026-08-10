"""The Telegram enrolment flow: master-on-first-use, then approval (ADR-0038).

`handle` is pure, so the whole flow runs here without a network — updates and state in,
replies out.
"""

import pytest

from headstart.alerts import bot
from headstart.alerts.registry import Pending, Registry
from headstart.alerts.store import Subscription, chat_subscription_id

MASTER = "1000"
ADA = "2000"


class _Store:
    """The Subscriptions dataset as a dict keyed by record id."""

    def __init__(self, records=None):
        self.records = dict(records or {})

    def get(self, sub_id):
        return self.records.get(sub_id)

    def put(self, sub):
        self.records[sub.id] = sub

    def remove(self, sub_id):
        self.records.pop(sub_id, None)


def _update(chat_id, text, username="", first="", last="", update_id=1):
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": int(chat_id)},
            "from": {
                "id": int(chat_id),
                "username": username,
                "first_name": first,
                "last_name": last,
            },
            "text": text,
        },
    }


def _approved(chat_id, query="backend"):
    return {chat_subscription_id(chat_id): Subscription.for_chat(chat_id, query)}


def test_the_first_start_claims_the_master_seat():
    registry, store = Registry(), _Store()

    replies = bot.handle(_update(MASTER, "/start"), registry, store)

    assert registry.master == MASTER
    assert replies == [(MASTER, bot.MASTER_HELP)]
    assert store.records == {}, "claiming the seat is not itself a subscription"


def test_the_second_person_is_announced_to_the_master_by_name():
    registry, store = Registry(master=MASTER), _Store()

    replies = bot.handle(
        _update(ADA, "/start", username="ada_l", first="Ada", last="Lovelace"),
        registry,
        store,
    )

    assert ADA in registry.pending
    asked, announced = replies
    assert asked[0] == ADA and "Asked for access" in asked[1]
    assert announced[0] == MASTER
    # The master is asked about a person, not an opaque number.
    assert "Ada Lovelace" in announced[1] and "@ada_l" in announced[1]
    assert f"/allow {ADA}" in announced[1] and f"/deny {ADA}" in announced[1]


def test_a_second_start_while_waiting_does_not_re_announce():
    registry, store = Registry(master=MASTER), _Store()
    bot.handle(_update(ADA, "/start"), registry, store)

    replies = bot.handle(_update(ADA, "/start"), registry, store)

    assert replies == [
        (ADA, "Still waiting on approval — you'll get a message when it's in.")
    ]
    assert len(registry.pending) == 1


def test_allow_creates_the_subscription_and_tells_both_sides():
    registry = Registry(master=MASTER, pending={ADA: Pending(ADA, "ada_l", "Ada")})
    store = _Store()

    replies = bot.handle(_update(MASTER, f"/allow {ADA}"), registry, store)

    assert ADA not in registry.pending
    sub = store.get(chat_subscription_id(ADA))
    assert sub is not None and sub.telegram == ADA
    assert sub.email == "", "a chat has no verified address behind it"
    assert sub.query == "", "approval and choosing a search are separate steps"
    assert [chat for chat, _ in replies] == [MASTER, ADA]


def test_deny_drops_the_request_and_says_so_rather_than_leaving_them_waiting():
    registry = Registry(master=MASTER, pending={ADA: Pending(ADA, "ada_l", "Ada")})
    store = _Store()

    replies = bot.handle(_update(MASTER, f"/deny {ADA}"), registry, store)

    assert ADA not in registry.pending
    assert store.records == {}
    assert [chat for chat, _ in replies] == [MASTER, ADA]
    assert "wasn't approved" in replies[1][1]


@pytest.mark.parametrize("command", ["allow", "deny"])
def test_answering_about_somebody_who_is_not_waiting_says_so(command):
    registry, store = Registry(master=MASTER), _Store()
    replies = bot.handle(_update(MASTER, f"/{command} 999"), registry, store)
    assert replies == [(MASTER, "999 isn't waiting on anything.")]


def test_q_sets_the_search_for_an_approved_chat():
    registry, store = Registry(master=MASTER), _Store(_approved(ADA, ""))

    replies = bot.handle(
        _update(ADA, "/q backend engineer at a startup"), registry, store
    )

    assert store.get(chat_subscription_id(ADA)).query == "backend engineer at a startup"
    assert replies == [(ADA, "Searching for: backend engineer at a startup")]


def test_changing_the_search_keeps_the_watermark_and_unsubscribe_token():
    before = Subscription.for_chat(ADA, "old")
    store = _Store({before.id: before})

    bot.handle(_update(ADA, "/q new search"), Registry(master=MASTER), store)

    after = store.get(before.id)
    assert after.query == "new search"
    assert after.watermark == before.watermark, "re-aiming must not re-send the backlog"
    assert after.unsubscribe_token == before.unsubscribe_token


def test_q_from_a_stranger_asks_for_access_instead_of_setting_anything():
    registry, store = Registry(master=MASTER), _Store()

    replies = bot.handle(_update(ADA, "/q backend"), registry, store)

    assert store.records == {}, "a stranger must not be able to create a subscription"
    assert ADA in registry.pending
    assert replies[1][0] == MASTER


def test_a_stranger_sending_anything_else_is_pointed_at_start():
    registry, store = Registry(master=MASTER), _Store()

    replies = bot.handle(_update(ADA, "hello?"), registry, store)

    assert replies == [(ADA, "Send /start to ask for access.")]
    assert registry.pending == {}, "idle chatter must not queue an approval request"


def test_stop_removes_the_subscription():
    store = _Store(_approved(ADA))
    replies = bot.handle(_update(ADA, "/stop"), Registry(master=MASTER), store)
    assert store.records == {}
    assert replies[0][0] == ADA


def test_revoke_stops_someone_already_approved():
    store = _Store(_approved(ADA))

    replies = bot.handle(
        _update(MASTER, f"/revoke {ADA}"), Registry(master=MASTER), store
    )

    assert store.records == {}
    assert [chat for chat, _ in replies] == [MASTER, ADA]


def test_pending_lists_who_is_waiting():
    registry = Registry(master=MASTER, pending={ADA: Pending(ADA, "ada_l", "Ada")})
    replies = bot.handle(_update(MASTER, "/pending"), registry, _Store())
    assert "Ada" in replies[0][1] and "@ada_l" in replies[0][1]


def test_pending_with_nobody_waiting():
    replies = bot.handle(_update(MASTER, "/pending"), Registry(master=MASTER), _Store())
    assert replies == [(MASTER, "Nobody waiting.")]


def test_status_reports_the_current_search():
    store = _Store(_approved(ADA, "data engineer"))
    replies = bot.handle(_update(ADA, "/status"), Registry(master=MASTER), store)
    assert replies == [(ADA, "Your search: data engineer")]


def test_a_group_style_command_suffix_is_understood():
    # Telegram appends @botname to commands in groups.
    store = _Store(_approved(ADA, "old"))
    bot.handle(_update(ADA, "/q@headstartbot new"), Registry(master=MASTER), store)
    assert store.get(chat_subscription_id(ADA)).query == "new"


@pytest.mark.parametrize(
    "update",
    [{"update_id": 1}, {"update_id": 1, "message": {"chat": {"id": 5}}}],
)
def test_updates_with_nothing_to_act_on_are_ignored(update):
    registry = Registry(master=MASTER)
    assert bot.handle(update, registry, _Store()) == []
    assert registry.master == MASTER, "a non-message must not claim the master seat"


def test_the_master_can_also_set_their_own_search():
    store = _Store()
    replies = bot.handle(_update(MASTER, "/q backend"), Registry(master=MASTER), store)
    assert store.get(chat_subscription_id(MASTER)).query == "backend"
    assert replies == [(MASTER, "Searching for: backend")]


def test_only_start_claims_an_unclaimed_bot():
    # Otherwise an idle "hi" to a freshly deployed bot hands away the master seat.
    registry, store = Registry(), _Store()

    replies = bot.handle(_update(ADA, "hello"), registry, store)

    assert registry.master == "", "a stray message must not claim the seat"
    assert replies == [(ADA, "Send /start to set up this bot.")]


def test_allow_is_idempotent_so_a_replay_cannot_reset_a_watermark():
    # The store is written before the registry, so a crash between them replays this
    # /allow. A second Subscription.for_chat would reset the Watermark to now and rotate
    # the unsubscribe token.
    existing = Subscription.for_chat(ADA, "backend")
    registry = Registry(master=MASTER, pending={ADA: Pending(ADA, "ada_l", "Ada")})
    store = _Store({existing.id: existing})

    bot.handle(_update(MASTER, f"/allow {ADA}"), registry, store)

    after = store.get(existing.id)
    assert after.watermark == existing.watermark
    assert after.unsubscribe_token == existing.unsubscribe_token
    assert after.query == "backend", "an existing search survives a replayed approval"


def test_a_denial_sticks_so_the_master_is_not_re_prompted():
    registry = Registry(master=MASTER, pending={ADA: Pending(ADA, "ada_l", "Ada")})
    store = _Store()

    bot.handle(_update(MASTER, f"/deny {ADA}"), registry, store)
    assert registry.denied == [ADA]

    # Their next /start must not reach the master again.
    replies = bot.handle(_update(ADA, "/start"), registry, store)
    assert [chat for chat, _ in replies] == [ADA]
    assert ADA not in registry.pending


def test_the_master_can_change_their_mind_after_a_denial():
    registry = Registry(master=MASTER, denied=[ADA], pending={ADA: Pending(ADA)})
    store = _Store()

    bot.handle(_update(MASTER, f"/allow {ADA}"), registry, store)

    assert registry.denied == [], "an approval clears the refusal"
    assert store.get(chat_subscription_id(ADA)) is not None
