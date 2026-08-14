import pytest

from headstart.ingest import shard_speedup


def test_load_missing_file_is_the_serial_default(tmp_path):
    # cold start must reproduce the old serial prediction, never under-predict on no evidence
    assert shard_speedup.load(tmp_path / "absent.csv").ratio == shard_speedup.DEFAULT


def test_load_corrupt_file_degrades_instead_of_raising(tmp_path):
    path = tmp_path / "shard_speedup.csv"
    path.write_text("speedup,samples,updated_at\nnot-a-number,3,2026-08-14\n")
    assert shard_speedup.load(path).ratio == shard_speedup.DEFAULT


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "state" / "shard_speedup.csv"
    shard_speedup.save(path, 2.8, samples=15)
    loaded = shard_speedup.load(path)
    assert (loaded.ratio, loaded.samples) == (2.8, 15)


def test_blend_moves_halfway_on_the_default_weight():
    # CURRENT_WEIGHT 0.5: one run of 3.0x against a stored 1.0x lands midway
    assert shard_speedup.blend(1.0, [3.0]) == 2.0


def test_blend_averages_the_run_before_folding_it_in():
    # a 15-shard run must not move the estimate more than a 3-shard one
    many = shard_speedup.blend(1.0, [3.0] * 15)
    few = shard_speedup.blend(1.0, [3.0] * 3)
    assert many == few


def test_blend_ignores_sub_serial_samples():
    # below 1.0x the sample measures something other than concurrency — noise, not a speedup
    assert shard_speedup.blend(2.0, [0.4]) == 2.0


def test_ratios_are_measured_against_serial_not_the_shards_own_prediction():
    # predicted_minutes IS this model's output; feeding it back settles on sqrt(true speedup)
    reports = [{"serial_minutes": 120.0, "seconds": 42 * 60, "predicted_minutes": 62.0}]
    assert shard_speedup.ratios_from_reports(reports) == [120.0 / 42.0]


def test_a_budget_killed_shard_is_never_blended():
    # its wall clock measures the budget, not the work: 120 packed, killed at 60, half done ->
    # reports 2.0x against a real ~1.0x, which would inflate the estimate and mute the warning
    reports = [{"serial_minutes": 120.0, "seconds": 60 * 60, "killed_by_budget": True}]
    assert shard_speedup.ratios_from_reports(reports) == []


def test_reports_without_a_serial_figure_are_skipped():
    # an older plan shipped no serial sum; absence is not an error
    assert shard_speedup.ratios_from_reports([{"seconds": 600}]) == []


def test_truncated_report_does_not_raise():
    assert shard_speedup.ratios_from_reports([{}, {"seconds": None}]) == []


def test_blend_with_no_usable_samples_keeps_history():
    assert shard_speedup.blend(2.5, []) == 2.5


def test_predict_divides_the_packed_sum_by_the_learned_speedup():
    assert shard_speedup.predict_minutes(120.0, floor_minutes=10.0, ratio=3.0) == 40.0


def test_predict_floors_at_the_slowest_single_board():
    # concurrency cannot take a shard below one Board; this floor was the better predictor of
    # the 2026-08-14 runs (30-35 min floor vs 42 min actual, against a 120 min packed sum)
    assert shard_speedup.predict_minutes(120.0, floor_minutes=45.0, ratio=3.0) == 45.0


def test_predict_never_multiplies_the_estimate():
    # a speedup below 1.0x would inflate the prediction above serial; clamped instead
    assert shard_speedup.predict_minutes(60.0, floor_minutes=0.0, ratio=0.25) == 60.0


def test_repeated_blending_converges_on_the_true_speedup(tmp_path):
    """The loop must settle on the real ratio, not on its own output.

    Measuring against the shard's own `predicted_minutes` would make each run divide an
    already-divided number and settle at sqrt(true) — 1.69x against a true 2.86x, wrong in the
    direction that looks converged. Measuring against the packed serial sum, which never moves,
    converges on the truth.
    """
    path = tmp_path / "shard_speedup.csv"
    true_ratio, serial = 2.86, 120.0
    for _ in range(12):
        stored = shard_speedup.load(path)
        # a real run: the shard takes serial/true_ratio no matter what we predicted
        reports = [
            {"serial_minutes": serial, "seconds": (serial / true_ratio) * 60}
            for _ in range(15)
        ]
        blended = shard_speedup.blend(
            stored.ratio, shard_speedup.ratios_from_reports(reports)
        )
        shard_speedup.save(path, blended, stored.samples + len(reports))
    assert shard_speedup.load(path).ratio == pytest.approx(true_ratio, abs=0.01)


def test_the_prediction_lands_near_the_real_2026_08_14_run():
    """Run 31738892152: 120.8 min packed, 29.9 min slowest Board, 42.6 min actual."""
    predicted = shard_speedup.predict_minutes(120.8, floor_minutes=29.9, ratio=2.86)
    assert predicted == pytest.approx(42.2, abs=0.5)  # was 120.8 — a 3x over-prediction
