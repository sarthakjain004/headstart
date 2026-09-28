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
