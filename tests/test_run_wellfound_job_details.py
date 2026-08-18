"""Job-detail parsing — the surface that supplies full descriptions.

Three live findings this pins:
  * detail pages carry a JSON-LD `JobPosting`, NOT `__NEXT_DATA__`, so the default
    `_is_blocked` predicate reads every one as blocked and burns the whole 40s retry budget
    (measured 41s/page vs 0.2s with `is_challenged`);
  * a resolved page with no JobPosting is a dead listing, not a block — but *some live
    listings serve no JSON-LD either*, so the rendered `#job-description` block is the
    fallback, verified byte-identical on a page carrying both;
  * the company page's `descriptionSnippet` is ~300 chars where the detail page's description
    is 7.5k-9.7k, which is the entire reason this stage exists.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "scrape"))

# The module imports pydoll at module scope; skip cleanly where it isn't installed (CI
# installs base deps only).
pytest.importorskip("pydoll")

from run_wellfound_job_details import (
    description_html,
    html_to_text,
    is_challenged,
    ld_jobposting,
    posting_matches,
)

ARTIFACT = ROOT / "tests" / "fixtures" / "wellfound_job_detail_jsonld.html"


@pytest.fixture(scope="module")
def detail_html() -> str:
    if not ARTIFACT.exists():
        pytest.skip(f"capture missing: {ARTIFACT}")
    return ARTIFACT.read_text(encoding="utf-8")


def test_detail_page_genuinely_has_no_next_data(detail_html):
    """The premise of the whole ready-predicate change. If this ever flips, revisit it."""
    assert "__NEXT_DATA__" not in detail_html


def test_ready_predicate_accepts_a_resolved_detail_page(detail_html):
    """The regression: keying on __NEXT_DATA__ made this page look blocked for 40s."""
    assert is_challenged(detail_html) is False


def test_ready_predicate_still_rejects_the_datadome_challenge():
    assert is_challenged("<html>geo.captcha-delivery.com</html>") is True
    assert is_challenged("<html>Just a moment</html>") is True


def test_a_dead_listing_is_not_treated_as_a_block(detail_html):
    """A closed job resolves with no JobPosting. Waiting for one cost 62s on the first we hit
    (4486544-principal-customer-success-engineer, a 404 with no captcha markers)."""
    assert is_challenged("<html><body>404 not found</body></html>") is False
    assert ld_jobposting("<html><body>404 not found</body></html>") is None


# --- a job must never inherit another job's description ---------------------------------------


def test_posting_matches_the_requested_job(detail_html):
    ld = ld_jobposting(detail_html)
    expected = ld["identifier"]["value"].split("-", 1)[0]
    assert posting_matches(ld, expected) is True


def test_posting_from_a_different_job_is_rejected(detail_html):
    """Guards the stale-read corruption: writing job A's description onto job B's row."""
    ld = ld_jobposting(detail_html)
    assert posting_matches(ld, "9999999") is False


def test_posting_without_a_usable_identifier_is_rejected():
    assert posting_matches({}, "123") is False
    assert posting_matches({"identifier": "not-a-dict"}, "123") is False
    assert posting_matches({"identifier": {"value": ""}}, "123") is False


# --- a live listing with no JSON-LD is not a dead one -----------------------------------------

NO_LD_ARTIFACT = ROOT / "tests" / "fixtures" / "wellfound_job_detail_no_jsonld.html"


@pytest.fixture(scope="module")
def no_ld_html() -> str:
    if not NO_LD_ARTIFACT.exists():
        pytest.skip(f"capture missing: {NO_LD_ARTIFACT}")
    return NO_LD_ARTIFACT.read_text(encoding="utf-8")


def test_a_live_listing_can_carry_no_json_ld_at_all(no_ld_html):
    """Checkfront's 4476061 renders fine and says 'Actively Hiring' — but has no JobPosting.
    Classifying it as gone (as the first cut did) silently drops a real description."""
    assert ld_jobposting(no_ld_html) is None
    assert is_challenged(no_ld_html) is False


def test_rendered_block_supplies_the_description_when_json_ld_is_absent(no_ld_html):
    text = html_to_text(description_html(no_ld_html))
    assert text and len(text) > 1000


def test_the_job_id_is_present_so_the_fallback_can_be_id_guarded(no_ld_html):
    """The fallback has no identifier field, so the id check falls back to the page body."""
    assert "4476061" in no_ld_html


def test_rendered_block_matches_the_json_ld_when_both_exist(detail_html):
    """Validates the fallback against ground truth: same page, two sources, same text."""
    from_ld = html_to_text(ld_jobposting(detail_html).get("description"))
    from_html = html_to_text(description_html(detail_html))
    assert from_html == from_ld


def test_no_description_block_returns_none():
    assert description_html("<html><body>nothing</body></html>") is None


def test_full_description_dwarfs_the_company_page_snippet(detail_html):
    ld = ld_jobposting(detail_html)
    assert ld is not None
    text = html_to_text(ld.get("description"))
    assert text and len(text) > 5000  # snippets measured 198-424 chars


def test_jobposting_is_picked_out_of_the_other_ld_blocks(detail_html):
    """Detail pages carry several ld+json blocks; only JobPosting has the description."""
    ld = ld_jobposting(detail_html)
    assert ld["@type"] == "JobPosting"
    assert ld.get("title")


def test_html_description_is_flattened_to_readable_text():
    raw = (
        "<ul><li>Build things</li><li>Ship them</li></ul><p>Great&amp; fun</p><br/>End"
    )
    # Block closers become newlines, so list/paragraph boundaries read as paragraph breaks.
    assert html_to_text(raw) == "- Build things\n- Ship them\n\nGreat& fun\n\nEnd"


def test_flattened_text_keeps_no_markup(detail_html):
    text = html_to_text(ld_jobposting(detail_html).get("description"))
    assert "<" not in text and "&nbsp;" not in text


def test_empty_description_is_none_not_empty_string():
    assert html_to_text(None) is None
    assert html_to_text("") is None
    assert html_to_text("   ") is None


def test_malformed_ld_json_is_skipped_rather_than_raising():
    assert (
        ld_jobposting('<script type="application/ld+json">{not json}</script>') is None
    )
