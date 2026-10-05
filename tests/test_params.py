"""Tests for parameter math and preset resolution."""

from __future__ import annotations

import pytest

from orca_advanced_calibration.params import (
    MAX_SAMPLES,
    PRESETS,
    make_samples,
    preset,
    sample_ratios,
    total_samples,
)


class TestTotalSamples:
    def test_formula(self):
        assert total_samples(0) == 1
        assert total_samples(1) == 3
        assert total_samples(3) == 7
        assert total_samples(5) == 11


class TestSampleRatios:
    def test_is_symmetric_and_sorted(self):
        ratios = sample_ratios(1.0, 0.02, 3)
        assert ratios == sorted(ratios)
        assert len(ratios) == 7
        assert ratios[0] == pytest.approx(0.94)
        assert ratios[-1] == pytest.approx(1.06)
        assert ratios[3] == pytest.approx(1.0)

    def test_center_is_exactly_once(self):
        ratios = sample_ratios(1.0, 0.02, 5)
        assert ratios.count(1.0) == 1

    def test_off_center_stays_symmetric(self):
        ratios = sample_ratios(0.97, 0.01, 4)
        assert len(ratios) == 9
        assert ratios[len(ratios) // 2] == pytest.approx(0.97)
        assert (ratios[-1] - 0.97) == pytest.approx(0.97 - ratios[0])

    def test_tiny_step_deduplicates_after_rounding(self):
        # A step smaller than the stored precision must not emit duplicate cubes.
        ratios = sample_ratios(1.0, 0.00001, 4)
        assert ratios == [1.0]

    def test_rejects_non_positive_step(self):
        with pytest.raises(ValueError):
            sample_ratios(1.0, 0.0, 3)
        with pytest.raises(ValueError):
            sample_ratios(1.0, -0.01, 3)

    def test_rejects_negative_side_count(self):
        with pytest.raises(ValueError):
            sample_ratios(1.0, 0.01, -1)


class TestMakeSamples:
    def test_labels_carry_absolute_ratio(self):
        samples = make_samples(1.0, 0.02, 1)
        assert [s.label for s in samples] == ["flow_0.9800", "flow_1.0000", "flow_1.0200"]

    def test_index_is_dense_and_ordered(self):
        samples = make_samples(0.98, 0.005, 4)
        assert [s.index for s in samples] == list(range(9))
        assert [s.flow_ratio for s in samples] == sorted(s.flow_ratio for s in samples)

    def test_offset_is_relative_to_center(self):
        samples = make_samples(1.0, 0.02, 2)
        assert samples[0].offset == pytest.approx(-0.04)
        assert samples[-1].offset == pytest.approx(0.04)

    def test_rejects_center_outside_range(self):
        with pytest.raises(ValueError):
            make_samples(0.1, 0.01, 3)
        with pytest.raises(ValueError):
            make_samples(2.0, 0.01, 3)

    def test_rejects_step_beyond_bounds(self):
        with pytest.raises(ValueError):
            make_samples(1.0, 0.5, 3)

    def test_rejects_too_many_samples(self):
        with pytest.raises(ValueError):
            make_samples(1.0, 0.01, MAX_SAMPLES)


class TestPresets:
    def test_all_presets_are_buildable(self):
        for name, item in PRESETS.items():
            assert item.step > 0, name
            assert item.side_count >= 1, name
            make_samples(1.0, item.step, item.side_count)

    def test_coarse_is_wider_than_fine(self):
        assert PRESETS["coarse"].step > PRESETS["fine"].step
        assert PRESETS["coarse"].span > PRESETS["fine"].span

    def test_fine_brackets_tighter_than_coarse(self):
        assert PRESETS["fine"].span < PRESETS["coarse"].span

    def test_documented_sample_counts(self):
        assert PRESETS["coarse"].samples == 7
        assert PRESETS["fine"].samples == 9
        assert PRESETS["orca-yolo"].samples == 11

    def test_orca_yolo_matches_orca_step(self):
        # Orca's YOLO pass uses a 0.01 flow step.
        assert PRESETS["orca-yolo"].step == pytest.approx(0.01)

    def test_lookup_is_case_insensitive(self):
        assert preset("COARSE") is PRESETS["coarse"]
        assert preset(" fine ") is PRESETS["fine"]

    def test_unknown_preset_lists_known_names(self):
        with pytest.raises(KeyError) as excinfo:
            preset("nope")
        assert "coarse" in str(excinfo.value)
