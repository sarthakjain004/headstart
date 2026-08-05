"""The allowlist gate — deny-by-default is the load-bearing property (ADR-0035)."""

from headstart.alerts.access import is_allowed, normalize

ALLOWED = ["Ada@Example.com", " bob@example.com "]


def test_matches_case_and_space_insensitively():
    assert is_allowed("ada@example.com", ALLOWED) is True
    assert is_allowed("  ADA@example.com  ", ALLOWED) is True
    assert is_allowed("bob@example.com", ALLOWED) is True


def test_unlisted_address_is_refused():
    assert is_allowed("eve@example.com", ALLOWED) is False


def test_empty_allowlist_allows_nobody():
    # The failure that matters: a missing or unreadable allowlist must not open the feature.
    assert is_allowed("ada@example.com", []) is False
    assert is_allowed("ada@example.com", ["", "  "]) is False


def test_empty_address_is_refused():
    assert is_allowed("", ALLOWED) is False
    assert is_allowed("   ", ALLOWED) is False


def test_normalize():
    assert normalize("  Ada@Example.COM ") == "ada@example.com"
