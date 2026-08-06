"""The bot's durable state: master, pending requests, polling offset (ADR-0038)."""

import json

from headstart.alerts import registry as reg
from headstart.alerts import store as st

REPO, TOKEN = "acme/subs", "tok"


def test_round_trips_through_json():
    before = reg.Registry(
        master="1000",
        pending={
            "2000": reg.Pending(
                "2000", "ada_l", "Ada Lovelace", "2026-08-06T00:00:00+00:00"
            )
        },
        offset=17,
    )

    after = reg.Registry.from_dict(json.loads(json.dumps(before.to_dict())))

    assert after == before


def test_describe_names_a_person_not_a_number():
    assert reg.Pending("2000", "ada_l", "Ada").describe() == "Ada (@ada_l) — id 2000"
    assert reg.Pending("2000", "", "Ada").describe() == "Ada — id 2000"
    assert reg.Pending("2000").describe() == "someone — id 2000"


def test_a_missing_registry_reads_as_empty_so_the_next_start_claims_it(monkeypatch):
    def absent(repo, path, token):
        raise FileNotFoundError(path)

    monkeypatch.setattr(reg, "_read", absent)
    empty = reg.load(REPO, TOKEN)

    assert empty == reg.Registry()
    assert empty.master == "", (
        "an empty registry is what makes first-run setup possible"
    )


def test_malformed_pending_entries_are_dropped_rather_than_raising():
    parsed = reg.Registry.from_dict(
        {
            "master": "1",
            "pending": {"2": "not-a-dict", "3": {"chat_id": "3"}},
            "offset": "4",
        }
    )

    assert list(parsed.pending) == ["3"]
    assert parsed.offset == 4, "offset survives arriving as a string"


def test_save_writes_the_registry_path(monkeypatch):
    written = {}
    monkeypatch.setattr(
        reg,
        "_write",
        lambda repo, path, data, token: written.update(path=path, data=data),
    )

    reg.save(REPO, TOKEN, reg.Registry(master="1000", offset=3))

    assert written["path"] == reg.PATH
    assert json.loads(written["data"])["master"] == "1000"


def test_the_registry_lives_beside_subscriptions_without_colliding_with_one():
    # `Store.all` walks `subscriptions/`; the registry must not be picked up as a record.
    assert not reg.PATH.startswith(st.PREFIX)
