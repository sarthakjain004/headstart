"""The bot's durable state: master, pending requests, polling offset (ADR-0038)."""

import json
import logging

import pytest

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

    monkeypatch.setattr(reg, "read_bytes", absent)
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
        "write_bytes",
        lambda repo, path, data, token: written.update(path=path, data=data),
    )

    reg.save(REPO, TOKEN, reg.Registry(master="1000", offset=3))

    assert written["path"] == reg.PATH
    assert json.loads(written["data"])["master"] == "1000"


def test_the_registry_lives_beside_subscriptions_without_colliding_with_one():
    # `Store.all` walks `subscriptions/`; the registry must not be picked up as a record.
    assert not reg.PATH.startswith(st.PREFIX)


def test_a_denial_is_remembered_so_the_gate_cannot_be_reopened():
    # Forgetting a refusal would let the same stranger's next /start re-announce them to
    # the master, indefinitely.
    parsed = reg.Registry.from_dict({"master": "1", "denied": ["2000", 3000]})
    assert parsed.denied == ["2000", "3000"]
    assert reg.Registry.from_dict({"master": "1"}).denied == []


def test_an_unreachable_hub_is_loud_where_a_first_run_is_not(monkeypatch, caplog):
    """Both start empty; only the level and the message separate them.

    `bot.main` *saves* whatever `load` returned, so a read blip that reads as "first run"
    erases the master and everyone pending — and the next `/start` claims the master seat,
    with `/allow`, `/deny` and `/revoke` behind it, every 15 minutes. `LocalEntryNotFound
    Error` subclasses `EntryNotFoundError` while meaning the opposite, so the split is drawn
    by `store._is_absent` rather than by an `isinstance` on the parent.
    """
    hub_errors = pytest.importorskip("huggingface_hub.errors")

    def raising(exc):
        def read(repo, path, token):
            raise exc

        return read

    monkeypatch.setattr(
        reg, "read_bytes", raising(hub_errors.EntryNotFoundError("gone"))
    )
    with caplog.at_level(logging.DEBUG, logger="headstart.alerts.registry"):
        assert reg.load(REPO, TOKEN) == reg.Registry()
    absent = caplog.records[-1]
    assert absent.levelno == logging.INFO
    assert "first run" in absent.getMessage()

    caplog.clear()
    monkeypatch.setattr(
        reg, "read_bytes", raising(hub_errors.LocalEntryNotFoundError("unreachable"))
    )
    with caplog.at_level(logging.DEBUG, logger="headstart.alerts.registry"):
        assert reg.load(REPO, TOKEN) == reg.Registry()
    unreachable = caplog.records[-1]
    assert unreachable.levelno == logging.ERROR
    assert (
        unreachable.exc_info is not None
    )  # the traceback is the only clue to which read
    # The consequence, not just the symptom — this empty Registry is about to be persisted.
    assert "SAVED OVER" in unreachable.getMessage()


def test_a_broken_install_is_not_reported_as_an_absent_registry(monkeypatch):
    # A missing huggingface_hub is not "first run" — saying so claims the bot is fine
    # while it can neither read nor write. This is how it read when bot.yml installed
    # base deps only.
    def no_hub(repo, path, token):
        raise ModuleNotFoundError("No module named 'huggingface_hub'")

    monkeypatch.setattr(reg, "read_bytes", no_hub)
    with pytest.raises(ModuleNotFoundError):
        reg.load(REPO, TOKEN)
