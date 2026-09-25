from headstart.search_filters import india_filter, india_gazetteer


def test_only_the_whole_country_uses_the_materialized_column():
    assert india_filter.clause("india", True) == "country = 'IN'"
    assert india_filter.clause("india", False) == india_gazetteer.where("india")
    assert india_filter.clause("bengaluru", True) == india_gazetteer.where("bengaluru")


def test_an_unknown_place_compiles_to_nothing():
    assert india_filter.clause("atlantis", True) is None


def test_the_served_value_is_the_gazetteer_verdict():
    assert india_filter.country("Bengaluru, Karnataka") == "IN"
    assert india_filter.country("Berlin") is None
