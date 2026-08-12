"""Subscription records and their per-file store (ADR-0035).

The four HF calls are replaced the way `tests/test_http.py` replaces its session, so the
repo layout and the deny-on-missing-allowlist rule are exercised without a network.
"""

import json

import pytest

from headstart.alerts import store as st

REPO, TOKEN = "acme/subs", "tok"


class _Hub:
    """A dict standing in for the dataset's file tree."""

    def __init__(self, files=None):
        self.files = dict(files or {})

    def install(self, monkeypatch):
        monkeypatch.setattr(st, "_list_files", lambda repo, token: list(self.files))
        monkeypatch.setattr(st, "_read", lambda repo, path, token: self.files[path])
        monkeypatch.setattr(
            st,
            "_write",
            lambda repo, path, data, token: self.files.__setitem__(path, data),
        )
        monkeypatch.setattr(
            st, "_delete", lambda repo, path, token: self.files.pop(path, None)
        )
        return self


def _record(sub_id, email="ada@example.com", query="backend engineer"):
    return json.dumps(
        {
            "id": sub_id,
            "email": email,
            "query": query,
            "filters": {"remote": "true"},
            "created_at": "2026-08-01T00:00:00+00:00",
            "watermark": "2026-08-02T00:00:00+00:00",
            "unsubscribe_token": "t0ken",
        }
    ).encode()


def test_id_is_stable_and_hides_the_address():
    first = st.subscription_id("Ada@Example.com ")
    assert first == st.subscription_id("ada@example.com")
    assert "ada" not in first and "@" not in first


def test_create_starts_the_watermark_now_so_no_backlog_is_mailed():
    sub = st.Subscription.create(
        "Ada@Example.com", " backend engineer ", {}, when="2026-08-05T10:00:00+00:00"
    )
    assert sub.email == "ada@example.com"
    assert sub.query == "backend engineer"
    assert sub.watermark == sub.created_at == "2026-08-05T10:00:00+00:00"
    assert sub.unsubscribe_token


def test_create_keeps_only_allowed_filters():
    sub = st.Subscription.create(
        "ada@example.com",
        "backend",
        {"remote": "true", "company": "", "seen_within": "2", "nonsense": "x"},
    )
    # seen_within would fight the Watermark; empty and unknown keys are dropped.
    assert sub.search_filters == {"remote": "true"}


def test_round_trips_through_the_store(monkeypatch):
    hub = _Hub().install(monkeypatch)
    store = st.Store(REPO, TOKEN)
    sub = st.Subscription.create("ada@example.com", "backend engineer", {})

    store.put(sub)
    assert sub.path() in hub.files
    assert [s.email for s in store.all()] == ["ada@example.com"]

    store.remove(sub.id)
    assert store.all() == []


def test_all_skips_the_allowlist_and_unreadable_records(monkeypatch):
    _Hub(
        {
            "subscriptions/aaa.json": _record("aaa"),
            "subscriptions/bad.json": b"{not json",
            st.ALLOWLIST_PATH: b'{"allowed": ["ada@example.com"]}',
            "README.md": b"ignored",
        }
    ).install(monkeypatch)
    assert [s.id for s in st.Store(REPO, TOKEN).all()] == ["aaa"]


def test_revising_keeps_the_unsubscribe_token_and_watermark():
    # Every Digest already delivered carries the old token; rotating it would 404 those
    # links. Resetting the Watermark would skip whatever arrived since the last Digest.
    first = st.Subscription.create("ada@example.com", "backend", {"remote": "true"})
    second = first.revised("frontend", {"company": "acme"})

    assert second.query == "frontend"
    assert second.search_filters == {"company": "acme"}
    assert second.id == first.id
    assert second.unsubscribe_token == first.unsubscribe_token
    assert second.watermark == first.watermark
    assert second.created_at == first.created_at


def test_get_returns_one_record_or_none(monkeypatch):
    real = st.subscription_id("ada@example.com")
    _Hub({f"subscriptions/{real}.json": _record(real)}).install(monkeypatch)
    store = st.Store(REPO, TOKEN)
    assert store.get(real).email == "ada@example.com"
    assert store.get("0" * 16) is None  # well-formed but absent


@pytest.mark.parametrize(
    "sub_id", ["allowlist", "../README", "aaa", "", "0" * 15, "NOTHEX0000000000"]
)
def test_get_refuses_ids_that_are_not_ids(monkeypatch, sub_id):
    # The id arrives from a query string. Unchecked, `allowlist` reads the allowlist file
    # and `../` walks out of the prefix — both reached hf_hub_download before this guard.
    reads = []

    def spy(repo, path, token):
        reads.append(path)
        raise AssertionError(f"unvalidated id reached a repo path: {path}")

    monkeypatch.setattr(st, "_read", spy)
    assert st.Store(REPO, TOKEN).get(sub_id) is None
    assert reads == []


def test_get_answers_none_for_a_file_that_is_not_a_subscription(monkeypatch):
    ok = "a" * 16
    _Hub({f"subscriptions/{ok}.json": b'{"allowed": ["ada@example.com"]}'}).install(
        monkeypatch
    )
    assert st.Store(REPO, TOKEN).get(ok) is None


def test_resubscribing_overwrites_rather_than_duplicates(monkeypatch):
    hub = _Hub().install(monkeypatch)
    store = st.Store(REPO, TOKEN)
    store.put(st.Subscription.create("ada@example.com", "backend", {}))
    store.put(st.Subscription.create("ada@example.com", "frontend", {}))
    assert len(hub.files) == 1
    assert [s.query for s in store.all()] == ["frontend"]


@pytest.mark.parametrize(
    "body, expected",
    [
        # Addresses come back normalized now that this list drives sending, not just
        # comparison — see `parse_allowlist`.
        (b'{"allowed": ["a@x.com", "B@x.com"]}', ["a@x.com", "b@x.com"]),
        (b'["a@x.com"]', ["a@x.com"]),
        (b"{}", []),
        (b'{"allowed": [{"email": "a@x.com", "query": "backend"}]}', ["a@x.com"]),
    ],
)
def test_allowlist_shapes(monkeypatch, body, expected):
    _Hub({st.ALLOWLIST_PATH: body}).install(monkeypatch)
    assert st.Store(REPO, TOKEN).allowlist() == expected


@pytest.mark.parametrize(
    "body, expected",
    [
        # A bare string is still the self-serve shape: invited, no Query of its own.
        (b'{"allowed": ["a@x.com"]}', [st.Invite("a@x.com", "", {})]),
        (
            b'{"allowed": [{"email": "A@x.com", "query": " backend ",'
            b' "filters": {"remote": "true", "bogus": "x"}}]}',
            [st.Invite("a@x.com", "backend", {"remote": "true"})],
        ),
        # A default is carried *separately*, never folded into the entry's own Query:
        # it may seed somebody with no record, but must not revise a signed-in one.
        (
            b'{"default_query": "data engineer", "allowed": ["a@x.com"]}',
            [st.Invite("a@x.com", "", {}, "data engineer")],
        ),
        (
            b'{"default_query": "data", "allowed": [{"email": "a@x.com",'
            b' "query": "backend"}]}',
            [st.Invite("a@x.com", "backend", {}, "data")],
        ),
        # One malformed entry is dropped; it must not deny everybody else.
        (
            b'{"allowed": [42, {"query": "no email"}, "a@x.com"]}',
            [st.Invite("a@x.com")],
        ),
        # Same address twice, differing only in case, is one Invite — first wins.
        (
            b'{"allowed": [{"email": "a@x.com", "query": "first"}, "A@x.com"]}',
            [st.Invite("a@x.com", "first", {})],
        ),
    ],
)
def test_parse_allowlist_entry_shapes(body, expected):
    assert st.parse_allowlist(json.loads(body)) == expected


def test_missing_allowlist_denies_rather_than_raising(monkeypatch):
    _Hub().install(monkeypatch)  # no allowlist file at all
    assert st.Store(REPO, TOKEN).allowlist() == []


# ---- Saved sets (ADR-0043) ----


def test_saved_set_create_normalizes_and_keys_by_account():
    saved = st.SavedSet.create(
        "Ada@Example.com ",
        "  backend roles  ",
        " backend engineer ",
        {"remote": "true", "junk": "x"},
    )
    assert saved.account == st.subscription_id("ada@example.com")
    assert saved.name == "backend roles"
    assert saved.query == "backend engineer"
    assert saved.search_filters == {"remote": "true"}  # same whitelist as Subscriptions
    assert not saved.emails
    assert saved.path() == f"sets/{saved.account}/{saved.id}.json"


def test_saved_set_revised_keeps_identity_and_email_flag():
    saved = st.SavedSet.create("ada@example.com", "backend", "backend engineer", {})
    flagged = st.replace(saved, emails=True)
    revised = flagged.revised("ML roles", "ML engineer", {"max_years": 3})
    assert (revised.id, revised.account, revised.created_at) == (
        saved.id,
        saved.account,
        saved.created_at,
    )
    assert revised.emails is True
    assert revised.search_filters == {"max_years": "3"}


def test_sets_round_trip_scoped_to_their_account(monkeypatch):
    hub = _Hub().install(monkeypatch)
    store = st.Store(REPO, TOKEN)
    mine = st.SavedSet.create("ada@example.com", "backend", "backend engineer", {})
    theirs = st.SavedSet.create("bob@example.com", "frontend", "frontend engineer", {})
    store.put_set(mine)
    store.put_set(theirs)

    assert [s.name for s in store.sets_for(mine.account)] == ["backend"]
    assert store.get_set(mine.account, mine.id).query == "backend engineer"
    assert store.get_set(mine.account, theirs.id) is None  # someone else's id: not mine

    store.remove_set(mine.account, mine.id)
    assert store.sets_for(mine.account) == []
    assert hub.files  # the other account's record is untouched


def test_set_lookups_reject_malformed_ids(monkeypatch):
    _Hub().install(monkeypatch)
    store = st.Store(REPO, TOKEN)
    account = st.subscription_id("ada@example.com")
    # neither part may name an arbitrary repo path
    assert store.get_set("../allowlist", "deadbeef") is None
    assert store.get_set(account, "../../secret") is None
    assert store.sets_for("not-an-account") == []


def test_sets_for_skips_unreadable_records(monkeypatch):
    account = st.subscription_id("ada@example.com")
    good = st.SavedSet.create("ada@example.com", "backend", "backend engineer", {})
    _Hub(
        {
            good.path(): json.dumps(good.to_dict()).encode(),
            f"sets/{account}/deadbeef.json": b"not json",
        }
    ).install(monkeypatch)
    assert [s.name for s in st.Store(REPO, TOKEN).sets_for(account)] == ["backend"]
