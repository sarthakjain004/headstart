from headstart.excluded_and_parked import EXCLUDED_BOARDS, PARKED_BOARDS


def test_skip_list_keys_are_lowercase():
    """Both lookups lowercase the ledger's key, so an entry carrying a capital could never
    match — it would sit in the list looking effective while the Board kept being scraped."""
    assert all(key == key.lower() for key in EXCLUDED_BOARDS)
    assert all(key == key.lower() for key in PARKED_BOARDS)
