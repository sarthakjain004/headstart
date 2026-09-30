"""A count with its noun: one plural rule for every answer (round-4 review S7)."""

from headstart.space_mcp import noun_counts


def test_one_is_singular_and_every_other_count_plural():
    assert noun_counts.counted(1, "job") == "1 job"
    assert noun_counts.counted(0, "job") == "0 jobs"
    assert noun_counts.counted(2334, "job") == "2,334 jobs"


def test_an_irregular_plural_is_given():
    assert noun_counts.counted(1, "company", "companies") == "1 company"
    assert noun_counts.counted(3, "company", "companies") == "3 companies"


def test_a_leading_word_counts_with_its_noun():
    assert noun_counts.counted(1, "more posting") == "1 more posting"
    assert noun_counts.counted(8, "more posting") == "8 more postings"
