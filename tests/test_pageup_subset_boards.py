import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "pageup_subset_boards",
    Path(__file__).resolve().parents[1] / "scripts/validate/pageup_subset_boards.py",
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_only_nonempty_containment_within_the_same_account_is_an_alias():
    assert module.subset_aliases(
        {
            "873/cw/en-us": {"1", "2", "3"},
            "873/sf/en-us": {"1", "2"},
            "873/la/en-us": {"3", "4"},
            "1083/cw/en": {"1", "2"},
            "873/empty/en": set(),
        }
    ) == {"873/sf/en-us": "873/cw/en-us"}


def test_equal_locale_views_elect_one_canonical_without_alias_chains():
    assert module.subset_aliases(
        {"1083/cw/en-us": {"1"}, "1083/cw/en": {"1"}, "1083/site/en": {"1"}}
    ) == {"1083/cw/en-us": "1083/cw/en", "1083/site/en": "1083/cw/en"}


def test_held_and_feed_canonical_locales_beat_malformed_archive_spellings():
    postings = {
        "822/lowell/en-": {"1"},
        "822/lowell/en-us": {"1"},
        "726/cw/content": {"2"},
        "726/cw/en-us": {"2"},
    }
    assert module.subset_aliases(
        postings,
        preferred={"822/lowell/en-us"},
        canonical={"822/lowell/en-us", "726/cw/en-us"},
    ) == {"822/lowell/en-": "822/lowell/en-us", "726/cw/content": "726/cw/en-us"}


def test_equal_sets_keep_an_existing_valid_locale_even_if_feed_names_another():
    assert module.subset_aliases(
        {"1011/cw/en": {"1"}, "1011/cw/en-us": {"1"}},
        preferred={"1011/cw/en"},
        canonical={"1011/cw/en-us"},
    ) == {"1011/cw/en-us": "1011/cw/en"}
