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
