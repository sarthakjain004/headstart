"""JazzHR scraper tests.

The two fixtures are unedited live captures (``restopros``, 2026-09-07): the ``/apply/jobs``
embed listing and the ``/apply/{key}`` detail page of its first posting. Everything the tests
assert about them was read off the live pages, not invented.

The markup excerpts inlined below are verbatim too — pulled out of live captures of the two
themes ``restopros`` does not use (``geonetric``'s classic ``resumator-*`` theme and
``selectra``'s fully custom one), and of a posting that carries a JSON-LD ``JobPosting``
(``greenpaws``) since ``restopros`` carries none. Only that blob's ``description`` string is
replaced, and only to keep the file readable; every field under test is byte-identical to what
the host served.
"""

from pathlib import Path

from headstart.salary import extract as extract_salary
from headstart.scrapers.registry import SCRAPERS, get_scraper

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCRAPED_AT = "2026-01-01T00:00:00+00:00"

LISTING = (FIXTURES / "jazzhr_restopros_listing.html").read_text(encoding="utf-8")
DETAIL = (FIXTURES / "jazzhr_restopros_detail.html").read_text(encoding="utf-8")

# geonetric.applytojob.com/apply/5kZiyUskiF, 2026-09-07 — the classic theme labels each
# attribute with its own <strong> prefix and uses <h2>, not <div>.
CLASSIC_ATTRIBUTES = """<div id="resumator-job-overview" class="resumator-jobs-text clrfix">
\t\t\t\t\t\t\t<h2 id="resumator-job-location" class="resumator-jobs-text">
\t\t\t\t\t<strong>Location:</strong> Cedar Rapids, IA\t\t\t\t</h2>
\t\t\t\t\t\t\t\t\t<h2 id="resumator-job-employment" class="resumator-jobs-text">
\t\t\t\t<strong>Type: </strong>Full Time\t\t\t</h2>
\t\t\t<h2 id="resumator-job-experience" class="resumator-jobs-text">
\t\t\t\t<strong>Min. Experience: </strong>Experienced\t\t\t</h2>
\t\t</div>
<div id="resumator-job-description"><p>Owns the day-to-day accounting function.</p></div>"""

# selectra.applytojob.com/apply/IgwXPTE6iN, 2026-09-07 — a fully custom tenant theme. Note the
# `resumator-job-type` spelling, which no stock theme emits.
CUSTOM_ATTRIBUTES = """<span id="resumator-job-location">Malaga, Spain</span>
<span id="resumator-job-type">Full Time</span>
<span id="resumator-job-experience">Entry Level</span>
<div id="resumator-job-description"><p>Importa la actitud.</p></div>"""

# greenpaws.applytojob.com/apply/F4b7cYPehS, 2026-09-07 — description elided (see the module
# docstring); datePosted, baseSalary and uniqueJobCode are exactly as served.
JOBPOSTING_LD = """<script type="application/ld+json">
{
    "@context": "http://schema.org/",
    "@type": "JobPosting",
    "url": "https://greenpaws.applytojob.com/apply/F4b7cYPehS/Dog-Walker-And-Pet-Sitter",
    "title": "Dog Walker and Pet Sitter",
    "description": "<p>Elided.</p>",
    "datePosted": "2026-08-31",
    "validThrough": "2026-11-29",
    "employmentType": "FULL_TIME",
    "experienceRequirements": "Entry Level",
    "uniqueJobCode": "job_20260831210058_4MTOW1XEUOSLTLYI",
    "baseSalary": {
        "@type": "MonetaryAmount",
        "currency": "USD",
        "value": {
            "@type": "QuantitativeValue",
            "unitText": "YEAR",
            "minValue": 30000,
            "maxValue": 40000
        }
    }
}
</script>"""


def _parse(listing=LISTING, details=None):
    return get_scraper("jazzhr", "restopros", "Fallback Co").parse(
        {"listing": listing, "details": details or {}}, SCRAPED_AT
    )


def test_registered():
    assert SCRAPERS["jazzhr"].ats == "jazzhr"
    assert SCRAPERS["jazzhr"].has_detail_pass is True


def test_parse_listing_only():
    """Every Job the listing carries, with no detail pass at all — the shape a Board whose
    detail fetches all failed still returns."""
    jobs = _parse()
    assert (
        len(jobs) == 4
    )  # not 8: the same page repeats each posting in a mobile <div> layout
    j = jobs[0]
    assert j.id == "jazzhr:restopros:oVX1rAxJJi"
    assert j.ats == "jazzhr"
    assert (
        j.company == "RestoPros"
    )  # the listing's Organization JSON-LD, not "Fallback Co"
    assert j.title == "Business Development Manager"
    assert j.location == "Summerville, SC"
    assert j.department == "Marketing and Sales"
    assert j.url == "https://restopros.applytojob.com/apply/oVX1rAxJJi"
    assert j.scraped_at == SCRAPED_AT
    # everything the listing cannot state
    assert j.description is None
    assert j.employment_type is None
    assert j.experience is None
    assert j.posted_at is None
    assert j.salary is None
    # an empty <span class="resumator_department"> is no department, not ""
    assert jobs[2].department is None
    assert jobs[2].location == "Charlotte, NC"
    # the tenant's own casing is kept, not title-cased on the way through
    assert jobs[1].location == "charlotte, NC"


def test_parse_with_detail_page():
    jobs = _parse(details={"oVX1rAxJJi": DETAIL})
    j = jobs[0]
    assert j.employment_type == "Full Time"
    assert j.experience == "Experienced"
    assert j.description.startswith(
        "Business Development Manager (Restoration & Construction"
    )
    assert len(j.description) > 2000
    # this posting carries no JSON-LD JobPosting (30.3% of pages don't), so neither field resolves
    assert j.posted_at is None
    assert j.salary is None
    # the jobs whose detail page was not fetched keep their listing-only fields
    assert jobs[1].description is None


def test_json_ld_supplies_posted_at_and_salary():
    jobs = _parse(details={"oVX1rAxJJi": DETAIL + JOBPOSTING_LD})
    j = jobs[0]
    assert j.posted_at == "2026-08-31"
    assert j.salary == "30000-40000 USD YEAR"
    # the HTML attribute wins over the JSON-LD's coarser enum, which would say "FULL_TIME"
    assert j.employment_type == "Full Time"


def test_salary_field_reaches_the_structured_parser():
    """The point of registering jazzhr in `salary._FIELD_PARSERS`: a bare unit word is only
    annualized by the structured parser, so an hourly figure is otherwise rejected outright."""
    assert extract_salary("30000-40000 USD YEAR", None, "jazzhr").min_annual == 30000
    hourly = extract_salary("20-25 USD HOUR", None, "jazzhr")
    assert (hourly.min_annual, hourly.max_annual, hourly.currency) == (
        41600,
        52000,
        "USD",
    )
    # the same string on an unregistered ATS is read as annual and correctly refused
    assert extract_salary("20-25 USD HOUR", None, "some-other-ats") is None


def test_classic_theme_attributes():
    jobs = _parse(details={"oVX1rAxJJi": CLASSIC_ATTRIBUTES})
    j = jobs[0]
    assert (
        j.employment_type == "Full Time"
    )  # the "<strong>Type: </strong>" label is stripped
    assert j.experience == "Experienced"
    assert j.description == "Owns the day-to-day accounting function."


def test_custom_theme_attributes():
    jobs = _parse(details={"oVX1rAxJJi": CUSTOM_ATTRIBUTES})
    j = jobs[0]
    assert j.employment_type == "Full Time"  # `resumator-job-type`, not `-employment`
    assert j.experience == "Entry Level"
    assert j.description == "Importa la actitud."


def test_remote_from_location():
    listing = LISTING.replace("Summerville, SC", "Remote")
    jobs = _parse(listing=listing)
    assert jobs[0].remote is True
    assert jobs[1].remote is False


def test_empty_board_and_departed_tenant_both_parse_to_nothing():
    """A live board with nothing open renders the table shell with no rows; a departed tenant
    renders no shell at all. Telling those apart is the liveness probe's job — here both are
    simply zero Jobs, and neither may raise."""
    shell_only = LISTING.split('<tr id="row_job_')[0] + "</table></body></html>"
    assert _parse(listing=shell_only) == []
    assert (
        _parse(listing="<html><title>JazzHR - Inactive Career Page</title></html>")
        == []
    )


def test_slug_from_is_the_bare_tenant_label():
    """No override: the pool's `tenant` is already the bare subdomain label and its `url` is the
    bare host — 4,647 of 4,647 rows, zero with a path, a query or upper case (checked
    2026-09-07). So unlike zoho and personio, whose slugs are hostnames and whose `url()`
    appends a path, there is no deep link that could leak into the URL this scraper builds."""
    scraper = SCRAPERS["jazzhr"]
    assert scraper.slug_from("restopros", "restopros.applytojob.com") == "restopros"
    assert scraper("restopros").url() == "https://restopros.applytojob.com/apply/jobs"
    assert scraper("restopros").board_key() == "jazzhr:restopros"
