"""The run's ordering guarantee: the Watermark advances only after a Digest is accepted,
and one Subscription's failure never stops the rest (ADR-0035)."""

import pytest

from headstart.alerts import digest, mail, run, space_query, transports
from headstart.alerts.store import Invite, Subscription, now_iso, subscription_id

AFTER = "2026-08-02T12:00:00+00:00"
CONFIG = {"RESEND_API_KEY": "key", "ALERTS_SENDER": "a@x.dev"}


class _Store:
    def __init__(self):
        self.saved = []

    def put(self, sub):
        self.saved.append((sub.id, sub.watermark))


def _sub(sub_id="abc"):
    return Subscription(
        id=sub_id, email="ada@example.com", query="backend", watermark=AFTER
    )


def _rows(n=2):
    return [
        {
            "title": f"Engineer {i}",
            "company": "Acme",
            "score": 0.5,
            "url": f"https://j/{i}",
            "first_seen": "2026-08-02T13:00:00+00:00",
        }
        for i in range(n)
    ]


@pytest.fixture
def no_xlsx(monkeypatch):
    # The spreadsheet needs an extra CI does not install; the ordering is what is under test.
    monkeypatch.setattr(digest, "to_xlsx", lambda jobs: b"xlsx")


def test_sends_then_advances_the_watermark(monkeypatch, no_xlsx):
    monkeypatch.setattr(space_query, "newly_seen", lambda *a, **k: _rows())
    sent = []
    monkeypatch.setattr(
        mail, "send", lambda key, sender, to, body, att=None: sent.append(to) or "id"
    )
    store, sub = _Store(), _sub()

    assert run.send_one(sub, store, "https://s", CONFIG) == 2
    assert sent == ["ada@example.com"]
    assert store.saved and store.saved[0][1] > AFTER  # Watermark moved forward


def test_watermark_is_stamped_before_the_search_not_after_the_send(
    monkeypatch, no_xlsx
):
    # Jobs indexed while this Subscription is being searched and mailed must still be
    # offered next run. Stamping the Watermark after the send would put them behind it,
    # and no later run would ever look at them again.
    searched_at = {}

    def slow_search(space, sub, after):
        searched_at["after"] = after
        return _rows()

    monkeypatch.setattr(space_query, "newly_seen", slow_search)
    monkeypatch.setattr(mail, "send", lambda *a, **k: "id")
    store, sub = _Store(), _sub()

    run.send_one(sub, store, "https://s", CONFIG)
    assert searched_at["after"] == AFTER  # searched from the old Watermark
    assert AFTER < sub.watermark  # advanced...
    # ...but to a stamp taken before the search, so the window has no hole in it.
    assert sub.watermark <= now_iso()


def test_a_failed_send_leaves_the_watermark_untouched(monkeypatch, no_xlsx):
    monkeypatch.setattr(space_query, "newly_seen", lambda *a, **k: _rows())

    def refuse(*a, **k):
        raise mail.MailError("resend down")

    monkeypatch.setattr(mail, "send", refuse)
    store, sub = _Store(), _sub()

    with pytest.raises(mail.MailError):
        run.send_one(sub, store, "https://s", CONFIG)
    assert store.saved == []  # nothing written, so next run retries the same window
    assert sub.watermark == AFTER


def test_no_matches_sends_nothing_and_does_not_advance(monkeypatch):
    monkeypatch.setattr(space_query, "newly_seen", lambda *a, **k: [])

    def explode(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("an empty Digest must not be sent")

    monkeypatch.setattr(mail, "send", explode)
    store, sub = _Store(), _sub()

    assert run.send_one(sub, store, "https://s", CONFIG) == 0
    assert store.saved == []


def test_main_skips_cleanly_when_unconfigured(monkeypatch, capsys):
    for name in run._REQUIRED:
        monkeypatch.delenv(name, raising=False)
    assert run.main() == 0
    assert "not configured" in capsys.readouterr().out


class _InviteStore:
    """A store standing in for the dataset, remembering what was written."""

    def __init__(self, records=None):
        self.records = dict(records or {})
        self.saved = []

    def get(self, sub_id):
        return self.records.get(sub_id)

    def put(self, sub):
        self.records[sub.id] = sub
        self.saved.append(sub)


def test_an_invite_with_a_query_creates_and_stores_a_subscription():
    # Stored immediately: the Watermark starts now, and `send_one` only persists after a
    # Digest goes out — so a first run with no matches would otherwise restart the window
    # every run and this person would never be mailed at all.
    store = _InviteStore()
    invite = Invite("ada@example.com", "backend engineer", {"remote": "true"})

    sub = run.subscription_for(invite, store, frozenset())

    assert sub is not None
    assert sub.query == "backend engineer"
    assert sub.search_filters == {"remote": "true"}
    assert store.saved == [sub], (
        "a new Subscription must survive a run that sends nothing"
    )


def test_an_invite_without_a_query_and_no_record_is_skipped():
    assert (
        run.subscription_for(Invite("ada@example.com"), _InviteStore(), frozenset())
        is None
    )


def test_an_invite_can_edit_filters_without_restating_the_query():
    """ADR-0035: "an allowlist entry may now be an object carrying `query` and `filters`".

    The revision was gated on `invite.query` being truthy, so an entry that changed only its
    filters was a no-op forever — and the stored query must not be blanked to apply them.
    """
    stored = Subscription(
        id=subscription_id("ada@example.com"),
        email="ada@example.com",
        query="backend engineer",
        watermark=AFTER,
        unsubscribe_token="keep-me",
    )
    store = _InviteStore({stored.id: stored})

    sub = run.subscription_for(
        Invite("ada@example.com", None, {"remote": "true"}), store, frozenset()
    )

    assert sub.search_filters == {"remote": "true"}
    assert sub.query == "backend engineer", (
        "an absent query must not blank the stored one"
    )
    assert sub.watermark == AFTER
    assert sub.unsubscribe_token == "keep-me"


def test_an_invite_query_revises_a_stored_subscription_keeping_watermark_and_token():
    stored = Subscription(
        id=subscription_id("ada@example.com"),
        email="ada@example.com",
        query="old query",
        watermark=AFTER,
        unsubscribe_token="keep-me",
    )
    store = _InviteStore({stored.id: stored})

    sub = run.subscription_for(
        Invite("ada@example.com", "new query"), store, frozenset()
    )

    assert sub.query == "new query"
    assert sub.watermark == AFTER, (
        "revising must not skip the window since the last Digest"
    )
    assert sub.unsubscribe_token == "keep-me", (
        "links already delivered must keep working"
    )


def test_an_invite_without_a_query_defers_to_what_they_chose_at_signin():
    stored = Subscription(
        id=subscription_id("ada@example.com"),
        email="ada@example.com",
        query="their own query",
        watermark=AFTER,
    )
    store = _InviteStore({stored.id: stored})

    sub = run.subscription_for(Invite("ada@example.com"), store, frozenset())

    assert sub.query == "their own query"
    assert store.saved == [], "an unchanged Subscription needs no write"


def test_a_default_query_seeds_a_new_record_but_never_revises_a_signed_in_one():
    # A default is a statement about nobody in particular. Folding it into the entry's own
    # Query made it authoritative, which silently overwrote what a signed-in person chose
    # at sign-in — every run, forever.
    stored = Subscription(
        id=subscription_id("ada@example.com"),
        email="ada@example.com",
        query="their own query",
        watermark=AFTER,
    )
    store = _InviteStore({stored.id: stored})
    invite = Invite("ada@example.com", "", {}, "the file default")

    assert run.subscription_for(invite, store, frozenset()).query == "their own query"
    assert store.saved == []

    # The same default does seed somebody with no record at all.
    fresh = run.subscription_for(
        Invite("bob@example.com", "", {}, "the file default"),
        _InviteStore(),
        frozenset(),
    )
    assert fresh.query == "the file default"


def test_a_revision_is_stored_so_the_record_cannot_drift_stale():
    # `send_one` persists only after a Digest goes out, so a revision followed by a run
    # with no matches would leave the stored query stale — and that record is what
    # `/subscribe` reads back.
    stored = Subscription(
        id=subscription_id("ada@example.com"),
        email="ada@example.com",
        query="old query",
        watermark=AFTER,
    )
    store = _InviteStore({stored.id: stored})

    run.subscription_for(Invite("ada@example.com", "new query"), store, frozenset())

    assert [s.query for s in store.saved] == ["new query"]


def test_telegram_subscriptions_are_the_bot_records_only():
    """Bot records carry a chat and no address; an allowlisted person given a chat id by
    hand carries both, and their Invite already covers them — selecting on `telegram`
    alone would deliver to that person twice in one run."""

    class _All:
        def __init__(self, records):
            self._records = records

        def all(self):
            return self._records

    from_bot = Subscription.for_chat("4242", "backend")
    allowlisted_with_chat = Subscription(
        id="x", email="ada@example.com", query="backend", telegram="9999"
    )
    plain_email = Subscription(id="y", email="bob@example.com", query="backend")

    picked = run.telegram_subscriptions(
        _All([from_bot, allowlisted_with_chat, plain_email])
    )

    assert [s.id for s in picked] == [from_bot.id]


def test_the_spreadsheet_carries_more_than_the_message(monkeypatch, no_xlsx):
    """The user asked for "the excel file or some document with the larger jobset".
    The body is capped at 30; the attachment must be every fresh row the Space returned."""
    rows = [
        {
            "title": f"Engineer {i}",
            "company": "Acme",
            "score": i / 100,
            "url": f"https://j/{i}",
            "first_seen": "2026-08-02T13:00:00+00:00",
        }
        for i in range(75)
    ]
    monkeypatch.setattr(space_query, "newly_seen", lambda *a, **k: rows)
    xlsx_rows = {}
    monkeypatch.setattr(
        digest, "to_xlsx", lambda jobs: xlsx_rows.setdefault("n", len(jobs)) and b"x"
    )
    body_rows = {}
    monkeypatch.setattr(
        run.transports,
        "for_subscription",
        lambda sub: transports.Transport(
            name="fake",
            selects=lambda s: True,
            send=lambda s, jobs, att, space, cfg: body_rows.setdefault("n", len(jobs)),
        ),
    )

    assert run.send_one(_sub(), _Store(), "https://s", CONFIG) == 30
    assert body_rows["n"] == 30, "the message is capped"
    assert xlsx_rows["n"] == 75, "the spreadsheet carries every fresh row"


def test_an_account_with_sets_keeps_its_projection_against_the_allowlist():
    """ADR-0069: once an Account keeps Saved sets, the sets endpoints own the Subscription.

    ADR-0043 made them "the only writer that keeps the two in step", which is why /subscribe
    409s while sets are live — but the alerts run re-projected from the Invite on every run,
    so an allowlist entry carrying its own query silently overwrote the emailing set's, and
    the Matches tab showed a set the Digest was not delivering.
    """
    stored = Subscription(
        id=subscription_id("ada@example.com"),
        email="ada@example.com",
        query="what the set says",
        watermark=AFTER,
        unsubscribe_token="keep-me",
    )
    store = _InviteStore({stored.id: stored})

    sub = run.subscription_for(
        Invite("ada@example.com", "what the allowlist says", {"remote": "true"}),
        store,
        frozenset({stored.id}),
    )

    assert sub.query == "what the set says", "the set's projection must survive the run"
    assert sub.search_filters == stored.search_filters
    assert store.saved == [], "a read-only pass must not write"


def test_an_account_without_sets_is_unaffected():
    """The allowlist stays the owner's edit path for everyone who never signed in."""
    stored = Subscription(
        id=subscription_id("ada@example.com"),
        email="ada@example.com",
        query="old query",
        watermark=AFTER,
        unsubscribe_token="keep-me",
    )
    store = _InviteStore({stored.id: stored})

    sub = run.subscription_for(
        Invite("ada@example.com", "new query"), store, frozenset()
    )

    assert sub.query == "new query"
    assert sub.watermark == AFTER and sub.unsubscribe_token == "keep-me"


def test_an_account_with_sets_but_no_subscription_is_not_minted_one():
    """Enabling email is a Matches-tab action (ADR-0043). An Invite must not start mail for
    somebody who signed in, made sets, and never turned email on."""
    store = _InviteStore()

    sub = run.subscription_for(
        Invite("ada@example.com", "a query"),
        store,
        frozenset({subscription_id("ada@example.com")}),
    )

    assert sub is None
    assert store.saved == []


def test_a_bare_string_invite_does_not_blank_the_filters_chosen_at_signin():
    """ADR-0035: "Bare strings still mean self-serve — invited, Query supplied at sign-in".

    A bare string parses to `Invite(search_filters={})`. The revise test then sees `{}` differ
    from what the person chose, and `revised(...)` wrote the empty dict back — every run,
    forever. This is the same defect already fixed one line above for `query`; the filters half
    was left behind.
    """
    stored = Subscription(
        id=subscription_id("ada@example.com"),
        email="ada@example.com",
        query="their own query",
        search_filters={"remote": "true", "location": "Berlin"},
        watermark=AFTER,
    )
    store = _InviteStore({stored.id: stored})

    sub = run.subscription_for(Invite("ada@example.com"), store, frozenset())

    assert sub.search_filters == {"remote": "true", "location": "Berlin"}
    assert store.saved == [], "an unchanged Subscription needs no write"


def test_an_invite_can_still_replace_the_filters_it_states():
    """The fall-back must not make filters unwritable — a stated filter set still wins.

    Values are compared after the store's own normalisation (it stringifies), so this asserts
    the stated set replaced the stored one, not the literal types handed in.
    """
    stored = Subscription(
        id=subscription_id("ada@example.com"),
        email="ada@example.com",
        query="their own query",
        search_filters={"etype": "INTERN"},
        watermark=AFTER,
    )
    store = _InviteStore({stored.id: stored})

    sub = run.subscription_for(
        Invite("ada@example.com", "", {"etype": "FULL_TIME"}),
        store,
        frozenset(),
    )

    assert sub.search_filters == {"etype": "FULL_TIME"}
    assert store.saved == [sub]
