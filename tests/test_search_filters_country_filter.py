from headstart.search_filters import (
    country_filter,
    country_gazetteer,
    india_filter,
    india_gazetteer,
)


def test_india_is_the_india_filters_whole_country_rule():
    assert country_filter.clause("IN", True) == india_filter.clause("india", True)
    assert country_filter.clause("IN", True) == "country = 'IN'"
    assert country_filter.clause("IN", False) == india_gazetteer.where("india")


def test_every_other_code_is_the_gazetteers_clause_whatever_the_table_carries():
    for code in country_gazetteer.COUNTRIES:
        assert country_filter.clause(code, True) == country_gazetteer.where(code)
        assert country_filter.clause(code, False) == country_gazetteer.where(code)


def test_an_unknown_code_compiles_to_nothing():
    assert country_filter.clause("ZZ", True) is None
    assert country_filter.clause("us", True) is None  # parse_filters upper-cases first


def test_the_codes_are_unique_iso_alpha_2_largest_first():
    codes = country_filter.CODES
    assert len(set(codes)) == len(codes)
    assert all(len(c) == 2 and c.isupper() for c in codes)
    assert codes[:3] == ("US", "IN", "GB")
    assert country_filter.options()[1] == ("IN", "India")
    assert country_filter.name("DE") == "Germany"


def test_a_code_a_name_or_a_common_abbreviation_is_read_as_its_code():
    """What the MCP tool reads a caller's country with (ADR-0322): "UK" was refused (ec06)."""
    for asked, code in (
        ("gb", "GB"),
        ("U.K.", "GB"),
        ("Great Britain", "GB"),
        ("USA", "US"),
        ("united states of america", "US"),
        ("UAE", "AE"),
        ("Germany", "DE"),
        ("Türkiye", "TR"),
        ("Turkey", "TR"),
        ("India", "IN"),
    ):
        assert country_filter.code_for(asked) == code, asked
    assert country_filter.code_for("Deutschland") is None
    # "America" also means the Americas and Latin America, so it is no country.
    assert country_filter.code_for("America") is None
    assert country_filter.code_for("") is None
