from headstart.experience_filter import CEILINGS, column, flags


def test_flags_keep_unknown_experience_eligible():
    assert flags(None) == {column(ceiling): True for ceiling in CEILINGS}


def test_flags_match_each_offered_ceiling_independently():
    assert flags(5) == {
        "experience_at_most_0": False,
        "experience_at_most_2": False,
        "experience_at_most_5": True,
        "experience_at_most_10": True,
    }
