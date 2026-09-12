from headstart.ingest import board_freshness


def test_freshness_tracks_authoritative_age_and_only_missing_rows_as_not_reseen(tmp_path):
    live = {"lever:a": "lever:a", "lever:b": "lever:b"}
    ids = ["lever:a:seen", "lever:a:missing", "lever:b:unread"]
    board_freshness.update(tmp_path, live, {"lever:a"}, {}, ids, set(ids), "2026-09-01T00:00:00+00:00")
    report = board_freshness.update(tmp_path, live, set(), {"lever:a": "short"}, ids,
                                    {"lever:a:seen"}, "2026-09-03T00:00:00+00:00")
    assert report["ats"]["lever"] == {
        "excluded_boards": 1, "protected_rows": 2, "not_reseen_rows": 1,
        "unknown_authoritative_age_boards": 0, "oldest_authoritative_run_days": 2.0,
    }
    assert report["boards"][0]["consecutive_exclusions"] == 1
    report = board_freshness.update(tmp_path, live, set(), {"lever:a": "short"}, ids,
                                    set(), "2026-09-04T00:00:00+00:00")
    assert report["boards"][0]["consecutive_exclusions"] == 2
    assert report["boards"][0]["excluded_since"] == "2026-09-03T00:00:00+00:00"


def test_first_observed_exclusion_has_unknown_age_and_clean_read_resets_streak(tmp_path):
    live = {"lever:a": "lever:a"}
    report = board_freshness.update(tmp_path, live, set(), {"lever:a": "failed"}, ["lever:a:1"],
                                    set(), "2026-09-01T00:00:00+00:00")
    assert report["ats"]["lever"]["unknown_authoritative_age_boards"] == 1
    assert report["ats"]["lever"]["oldest_authoritative_run_days"] is None
    board_freshness.update(tmp_path, live, {"lever:a"}, {}, [], set(), "2026-09-02T00:00:00+00:00")
    report = board_freshness.update(tmp_path, live, set(), {"lever:a": "failed"}, [],
                                    set(), "2026-09-03T00:00:00+00:00")
    assert report["boards"][0]["consecutive_exclusions"] == 1
    assert report["boards"][0]["excluded_since"] == "2026-09-03T00:00:00+00:00"


def test_unselected_board_retains_history_without_counting_another_failed_attempt(tmp_path):
    live = {"lever:a": "lever:a"}
    board_freshness.update(tmp_path, live, set(), {"lever:a": "failed"}, ["lever:a:1"],
                           set(), "2026-09-01T00:00:00+00:00")
    report = board_freshness.update(tmp_path, live, set(), {}, ["lever:a:1"],
                                    set(), "2026-09-03T00:00:00+00:00")
    assert report["boards"][0]["consecutive_exclusions"] == 1
    assert report["boards"][0]["not_reseen_rows"] is None
    assert report["ats"]["lever"]["protected_rows"] == 1
    assert report["ats"]["lever"]["not_reseen_rows"] == 0
    assert board_freshness.update(tmp_path, {}, set(), {}, [], set(),
                                  "2026-09-04T00:00:00+00:00")["boards"] == []
