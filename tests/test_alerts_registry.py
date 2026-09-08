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


def _read_raising(exc):
    """A `read_bytes` that fails the way one real failure mode fails."""

    def read(repo, path, token):
        raise exc

    return read


def test_an_unreadable_registry_is_loud_and_names_what_it_will_erase(
    monkeypatch, caplog
):
    """The branch that destroys state, pinned under the deps CI actually installs.

    `bot.main` *saves* whatever `load` returned, so a read that fails and answers "starting
    empty" erases the master and everyone pending — and the next `/start` claims the master
    seat, with `/allow`, `/deny` and `/revoke` behind it, every 15 minutes.

    A plain exception rather than one of the Hub's, on purpose: `store._is_absent` answers False
    for anything that is not the Hub's own `EntryNotFoundError`, and False again when
    `huggingface_hub` is not importable at all — so this reaches the loud branch with nothing but
    `.[dev]` installed. That is what makes it a CI check. `huggingface_hub` sits in the `alerts`
    extra and CI installs `.[dev]` alone, so the test below skips there; before this one existed,
    the only behaviour that can erase the bot's state was pinned on a developer's laptop and
    nowhere else, and a green CI said nothing about it.
    """
    monkeypatch.setattr(reg, "read_bytes", _read_raising(Exception("connection reset")))
    with caplog.at_level(logging.DEBUG, logger="headstart.alerts.registry"):
        assert reg.load(REPO, TOKEN) == reg.Registry()

    unreadable = caplog.records[-1]
    assert unreadable.levelno == logging.ERROR, (
        "an ::error:: annotation on the run, not a line in a scroll nobody reads"
    )
    assert (
        unreadable.exc_info is not None
    )  # the traceback is the only clue to which read
    # The consequence, not just the symptom — this empty Registry is about to be persisted.
    assert "SAVED OVER" in unreadable.getMessage()


def test_an_unreachable_hub_is_not_filed_as_a_first_run(monkeypatch, caplog):
    """Where the line falls between "no such record" and "no answer" — both start empty.

    Only the level and the message separate them. `LocalEntryNotFoundError` subclasses
    `EntryNotFoundError` while meaning the opposite, so the split is drawn by `store._is_absent`
    rather than by an `isinstance` on the parent — and only the real classes can prove that
    holds, which is why this half keeps an `importorskip` and does not run in CI. What the loud
    side then *says* is pinned by the test above, which does.
    """
    hub_errors = pytest.importorskip(
        "huggingface_hub.errors",
        reason="the real exception hierarchy; CI installs `.[dev]`, which has no huggingface_hub",
    )

    monkeypatch.setattr(
        reg, "read_bytes", _read_raising(hub_errors.EntryNotFoundError("gone"))
    )
    with caplog.at_level(logging.DEBUG, logger="headstart.alerts.registry"):
        assert reg.load(REPO, TOKEN) == reg.Registry()
    absent = caplog.records[-1]
    assert absent.levelno == logging.INFO
    assert "first run" in absent.getMessage()

    caplog.clear()
    monkeypatch.setattr(
        reg,
        "read_bytes",
        _read_raising(hub_errors.LocalEntryNotFoundError("unreachable")),
    )
    with caplog.at_level(logging.DEBUG, logger="headstart.alerts.registry"):
        assert reg.load(REPO, TOKEN) == reg.Registry()
    assert caplog.records[-1].levelno == logging.ERROR, (
        "an unreachable Hub read as an absent record is how the master seat is lost"
    )


def test_a_broken_install_is_not_reported_as_an_absent_registry(monkeypatch):
    # A missing huggingface_hub is not "first run" — saying so claims the bot is fine
    # while it can neither read nor write. This is how it read when bot.yml installed
    # base deps only.
    def no_hub(repo, path, token):
        raise ModuleNotFoundError("No module named 'huggingface_hub'")

    monkeypatch.setattr(reg, "read_bytes", no_hub)
    with pytest.raises(ModuleNotFoundError):
        reg.load(REPO, TOKEN)
